# -*- coding: utf-8 -*-
"""第 2 步【N2】nightly-dashboard-detail 执行记录 → 双轨道圈定待查运行（仅统计工作日）。

接口: N2 = POST /gateway/openlibing-ops/manage/common/detail  category=nightly-dashboard-detail
      执行记录即 N2 逐运行看板明细（不再调 T3 pipeline-run/list 在流水线上获取执行记录）：
      pipelineRunNumber/pipelineRunId/pipelineStatus/pipelineRunStarttime/pipelineRunEndtime +
      isVersionAvailable/buildCount/buildPassCount/failedTaskType/casePassRate/workflowRunUrl；
      待查运行直接以记录内 pipelineRunId 构造 openlibing /apps/pipelineDetail 链接下钻
      （第 3 步 T4 用同一 pipelineRunId 取任务图谱）。
时间口径: 运行按【结束时间】归属——是否落入统计周期、归属哪一天、是否工作日
          均以 pipelineRunEndtime 判定（跨午夜运行归到结束日）；
          无结束时间的记录（进行中/排队中）不纳入本周期统计。
同运行去重: 同一运行可能有多条评估记录（挂起运行按日重复评估），
          按 pipelineRunNumber 去重保留 pipelineRunEndtime 最新一条。
VA 日可用口径（双指标）: 当天任一次运行 服务端 isVersionAvailable=true → 当天可用，跳过当天全部失败；
          isVersionAvailable 语义 = 任务构建成功 且 测试用例通过率 100%（casePassRate 为空"--"视为失败）。
N2 接口失败的流水线: 无执行记录数据，记入 _meta.dashFailed 待办（无旧口径回退）。
工作日筛查: 法定工作日历（timor.tech 节假日 API + config overrides + 周一~五兜底），
          法定调休补班日计入工作日，法定假与周末不分析。
双轨道圈定（按 pipelineRunId 全局去重，同一运行只取证一次）:
  VA 轨道: 当天无 isVersionAvailable=true 运行 → 当天 FAILED/CANCELED 运行进入待查（dims 含 va）
  BS 轨道（编译成功率，任务级语义，不按天豁免）: 当天已有可用运行时，同日的 FAILED/CANCELED
          运行仍可能是构建失败（Build* 任务失败拉低 buildSuccess，与当天可用与否无关），
          补圈为 bs-only 待查（dims 仅含 bs）；Build 是否真失败在第 3 步 T4 详情后过滤
说明: CANCELED（人工取消/已停止）运行同样纳入取证范围；第 1 步看板比率只作兜底，
      版本可用度 / 编译成功率一律在此按逐运行记录重算（可用日 / 有运行日、ΣbuildPassCount/ΣbuildCount）；
      runHistory 为**区间内全部运行**（成功 + 失败），是归档层「每天全部构建记录」的唯一来源；
      逐运行附 编译次数/编译成功次数/失败任务类型（未失败为 null，报告展示"--"）及构建号跳转链接；
      github 平台流水线不计算编译成功率（buildSuccess 恒 null），bc/bpc 置 null 不采集不展示。
缓存状态校准: 任务可能发生重试/重跑，复用前比对 N2 状态与结束时间，任一变更即视为失效重新获取。
github: platform=github 的流水线（buildSuccess 恒 null，仅 VA 轨道）failedRuns 附 platform/ghTitle/ghEvent/ghRepo/
      ghDuration，供第 5c 步直接以 pipelineRunId（=GitHub run id）下钻 GitHub Actions 日志。
产物: out/step_2_dash_raw.json / out/step_2_failed_runs.json
     （含 _meta 工作日口径/VA 双指标规则/N2 失败清单；每流水线 runHistory = 区间内全量构建记录
       （成功 + 失败，归档层逐日记录即由它生成）；failedRuns = 双轨道并集，每条带 dims 标注进入原因）
退出码: 0 成功；42 无任何流水线（第 1 步已提前退出）；1 失败
"""
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, chunks, date_range_source, load_config,
                          load_json, normalize_date_range, run_page_js,
                          save_json)
from lib.cache import (cache_enabled, entry_key, entry_valid, load_cache,
                       prune_cache, save_cache)
from lib.workdays import build_workday_set


def parse_n2_time(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def is_scheduled_run(m):
    """定时任务运行判定（github 平台 engine 的 displayTitle 是唯一可靠信号）。

    event 字段不可靠：PR 检视运行有 127/131 条仍报 workflow_dispatch，
    只有标题能区分 —— "Nightly-A3 (scheduled)" 是定时任务，"Nightly-A3 (PR) 20635"
    与 "[v0.18.0][Doc][Misc] …"（event=pull_request）都是 PR 相关运行。
    m 可以是 N2 原始记录（displayTitle），也可以是第 2 步归一后的运行（title）。
    """
    title = str(m.get("displayTitle") or m.get("title") or "")
    if re.search(r"\(\s*pr\b", title, re.I):
        return False
    if str(m.get("event") or "").lower() == "pull_request":
        return False
    return "(scheduled)" in title.lower()


def main():
    cfg = load_config()
    start, end = normalize_date_range(cfg)
    workdays, wd_notes = build_workday_set(start, end, cfg)
    print("== 第 2 步【N2】执行记录·双轨道（区间 %s ~ %s 共 %d 个工作日，%s）==" % (
        start, end, len(workdays), date_range_source(cfg)))
    if wd_notes:
        print("  ".join(wd_notes))
    bad = load_json(OUT_DIR / "step_1_bad_pipelines.json")["pipelines"]
    meta = {b["key"]: b for b in bad}
    print("流水线 %d 条" % len(bad))

    pipelines = [{"key": b["key"], "projectId": b["projectId"],
                  "pipelineId": b["pipelineId"]} for b in bad]
    dash_params = {
        "startDate": start[:10], "endDate": end[:10],
        "pageSize": cfg["collect"].get("n2_page_size", 100),
        "delay": cfg["chrome"]["inter_request_delay_ms"],
        "conc": cfg["chrome"].get("request_concurrency", 4),
    }
    raw_dash = {}
    for i, batch in enumerate(chunks(pipelines, cfg["chrome"]["batch_size"])):
        out = run_page_js("step_2_dashboard.js", dict(dash_params, pipelines=batch),
                          "step_2_dash_batch%d.json" % (i + 1), cfg, "N2")
        raw_dash.update(out.get("dash") or {})
    save_json(OUT_DIR / "step_2_dash_raw.json", raw_dash)

    t_start = datetime.datetime.fromisoformat(start)
    t_end = datetime.datetime.fromisoformat(end).replace(hour=23, minute=59, second=59)

    result = {}
    dash_failed = []
    skipped_non_workday = 0
    skipped_running = 0
    n_va_track = n_bs_track = 0
    for p in pipelines:
        key = p["key"]
        pipe_dims = meta[key].get("dims") or []
        resp = raw_dash.get(key) or {}
        if resp.get("code") != 200:
            dash_failed.append(key)
            result[key] = {"failedRuns": [], "days": {}, "runHistory": [],
                           "availDays": 0, "daysInPeriod": 0}
            print("  {:<36} N2 接口失败（code={}），无执行记录，记待办".format(key, resp.get("code")))
            continue
        # 同运行多条评估记录 → 保留结束时间最新一条
        latest = {}
        for r in ((resp.get("data") or {}).get("records")) or []:
            try:
                rn = int(r.get("pipelineRunNumber"))
            except (TypeError, ValueError):
                continue
            et = r.get("pipelineRunEndtime") or ""
            if rn not in latest or et > latest[rn]["et"]:
                ftt = r.get("failedTaskType")
                latest[rn] = {
                    "et": et,
                    "rn": rn,
                    "rid": r.get("pipelineRunId"),
                    "status": r.get("pipelineStatus"),
                    "st": r.get("pipelineRunStarttime") or "",
                    "iva": r.get("isVersionAvailable"),
                    "bc": r.get("buildCount"), "bpc": r.get("buildPassCount"),
                    "cpr": r.get("casePassRate"),
                    "ftt": None if ftt in (None, "流水线未失败") else ftt,
                    "wfu": r.get("workflowRunUrl"),
                    # github 平台流水线：第 5c 步下钻所需（pipelineRunId 即 GitHub run id）
                    "title": r.get("displayTitle"),
                    "event": r.get("event"),
                    "repo": r.get("repoUrl"),
                    "dur": r.get("actualDurationMinutes"),
                }
        by_day = {}
        # 口径例外：只采定时任务运行 —— 含 PR 的运行一律不采集、不进待查、不做根因
        dropped_pr = 0
        if meta[key].get("scheduledOnly"):
            kept = {}
            for rn, m in latest.items():
                if is_scheduled_run(m):
                    kept[rn] = m
                else:
                    dropped_pr += 1
            latest = kept
        for m in latest.values():
            dt = parse_n2_time(m["et"])
            sdt = parse_n2_time(m["st"])
            if not dt or not sdt:
                # 进行中/排队中的运行没有结束时间，无法按结束时间口径归属本周期
                skipped_running += 1
                continue
            if not (t_start <= dt <= t_end):
                continue
            if dt.date().isoformat() not in workdays:
                skipped_non_workday += 1
                continue
            by_day.setdefault(dt.strftime("%m-%d"), []).append((m, dt, sdt))

        days, failed_runs, run_history, avail_days = {}, [], [], 0
        day_avail = {}
        is_gh = (meta[key].get("platform") or "").lower() == "github"
        for dstr, pairs in sorted(by_day.items()):
            statuses = sorted(m["status"] or "?" for m, _, _ in pairs)
            days[dstr] = statuses
            # VA 双指标口径：构建成功且用例通过率 100%（-- 视为失败），以服务端逐运行 isVersionAvailable 为准
            day_ok = any(m["iva"] is True for m, _, _ in pairs)
            day_avail[dstr] = 1 if day_ok else 0
            if day_ok:
                avail_days += 1
            for m, dt, sdt in pairs:
                url = ("https://www.openlibing.com/apps/pipelineDetail"
                       "?pipelineId=%s&projectId=%s&pipelineRunId=%s"
                       % (p["pipelineId"], p["projectId"], m["rid"]))
                run_history.append({
                    "rn": m["rn"],
                    "start": sdt.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": m["status"],
                    "rid": m["rid"],
                    "url": url,
                    # github 平台流水线不计算编译成功率：编译次数/编译成功次数不采集不展示
                    "bc": None if is_gh else m["bc"],
                    "bpc": None if is_gh else m["bpc"],
                    "cpr": m["cpr"], "iva": m["iva"],
                    "ftt": m["ftt"],
                })
                if m["status"] in ("FAILED", "CANCELED"):
                    gh_extra = {"platform": "github",
                                "ghTitle": m.get("title"), "ghEvent": m.get("event"),
                                "ghRepo": m.get("repo"), "ghDuration": m.get("dur")} \
                        if is_gh else {}
                    if not day_ok:
                        # VA 失败日：运行对流水线自身涉及的全部维度有意义（dims = 流水线 dims）
                        failed_runs.append({
                            "rn": m["rn"],
                            "rid": m["rid"],
                            "status": m["status"],
                            "start": sdt.strftime("%m-%d %H:%M"),
                            "startISO": sdt.isoformat(),
                            "endISO": dt.isoformat(),
                            "detailUrl": m["wfu"],
                            "openlibingUrl": url,
                            "dims": list(pipe_dims),
                            "track": "va",
                            # 失败类型（服务端 failedTaskType，如「用例失败」「编译失败」），供记录级展示
                            "ftt": m["ftt"],
                            **gh_extra,
                        })
                    elif "bs" in pipe_dims:
                        # 豁免日：仅编译成功率命中的流水线补圈（任务级语义，Build* 失败与当天可用无关）
                        failed_runs.append({
                            "rn": m["rn"],
                            "rid": m["rid"],
                            "status": m["status"],
                            "start": sdt.strftime("%m-%d %H:%M"),
                            "startISO": sdt.isoformat(),
                            "endISO": dt.isoformat(),
                            "detailUrl": m["wfu"],
                            "openlibingUrl": url,
                            "dims": ["bs"],
                            "track": "bs",
                            "ftt": m["ftt"],
                        })
        failed_runs.sort(key=lambda x: x["endISO"])
        n_va_track += sum(1 for f in failed_runs if f["track"] == "va")
        n_bs_track += sum(1 for f in failed_runs if f["track"] == "bs")
        recompute = bool(meta[key].get("recompute"))
        result[key] = {
            "failedRuns": failed_runs, "days": days,
            "runHistory": run_history,
            "runsInPeriod": len(run_history),
            "availDays": avail_days, "daysInPeriod": len(days),
            # 比率一律按采集到的运行重算（可用日 / 有运行日），看板比率仅作无运行记录时兜底
            "recompute": recompute,
            "scheduledOnly": bool(meta[key].get("scheduledOnly")),
            "droppedPrRuns": dropped_pr,
            "dayAvail": day_avail,
            "vaRateRecomputed": (avail_days / float(len(days))) if days else None,
        }
        print("  {:<36} 全量运行 {} 次 | 可用 {}/{} 工作日{} | 待查运行 {} 次（失败日 {} + 豁免日BS补圈 {}）".format(
            key, len(run_history), avail_days, len(days),
            ("  [重算可用度 %.2f%%，剔除 PR 运行 %d 次]" % (avail_days * 100.0 / len(days), dropped_pr))
            if days else "",
            len(failed_runs),
            sum(1 for f in failed_runs if f["track"] == "va"),
            sum(1 for f in failed_runs if f["track"] == "bs")))

    # 增量缓存：剔除区间外条目 + 标记本次待查运行哪些可直接复用上次取证
    # 复用前状态校准：运行可能发生重试/重跑，N2 状态或结束时间与缓存记录不一致的条目失效重抓
    n_reuse = n_stale = 0
    cache = load_cache(cfg)
    dropped = prune_cache(cache, start, end)
    for key, info in result.items():
        if key == "_meta":
            continue
        for fr in info["failedRuns"]:
            e = (cache["runs"] or {}).get(entry_key(key, fr.get("rid")))
            stale = bool(e) and (e.get("status") != fr.get("status")
                                 or e.get("endISO") != fr.get("endISO"))
            usable = cache_enabled(cfg) and not stale and entry_valid(e, OUT_DIR)
            fr["cached"] = bool(usable)
            n_reuse += bool(usable)
            n_stale += bool(stale and cache_enabled(cfg) and e)
    if cache_enabled(cfg):
        cache["lastFetch"] = {
            "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "start": start, "end": end,
            "pipelines": len(pipelines),
        }
        save_cache(cfg, cache)

    result["_meta"] = {
        "workdays": sorted(workdays), "workdayCount": len(workdays),
        "skippedNonWorkdayRuns": skipped_non_workday,
        "skippedRunningRuns": skipped_running,
        "workdayNotes": wd_notes,
        "vaDayRule": "VA 日可用口径（双指标）：当天任一次运行 服务端 isVersionAvailable=true（任务构建成功"
                     "且测试用例通过率 100%，casePassRate 为空/-- 视为失败）→ 当天可用；"
                     "scheduledOnly 产品（config.analysis_policy.scheduled_only）先剔除全部含 PR 的运行，"
                     "仅以定时任务运行（displayTitle 含 (scheduled)）判定 —— 当天有成功的定时运行才算可用，"
                     "否则当天全部失败的定时运行都进入根因分析；"
                     "执行记录即 N2 nightly-dashboard-detail 逐运行明细"
                     "（同运行多条评估记录保留结束时间最新一条）",
        "dashFailed": dash_failed,
        "rule": "执行记录取自 N2 nightly-dashboard-detail 逐运行明细（不再调流水线执行历史接口），"
                "按结束时间归属统计周期与日期（跨午夜运行归到结束日，无结束时间不纳入），"
                "仅统计工作日（含法定调休补班），周末与法定假不分析；"
                "VA 轨道按 isVersionAvailable 双指标口径按天豁免，"
                "BS 轨道任务级语义补圈豁免日失败运行（dims=bs）；"
                "N2 失败的流水线无执行记录（记 dashFailed 待办）；"
                "runHistory 附编译次数/编译成功次数/失败任务类型（未失败为 null）与构建号跳转链接"
                "（github 平台流水线不计算编译成功率，bc/bpc 置 null 不展示）；"
                "缓存复用前校准运行状态（N2 状态/结束时间与缓存记录比对，重试重跑变更即重抓）",
    }
    save_json(OUT_DIR / "step_2_failed_runs.json", result)
    total = sum(len(v["failedRuns"]) for k, v in result.items() if k != "_meta")
    total_runs = sum(len(v.get("runHistory") or []) for k, v in result.items() if k != "_meta")
    print("\n待查运行合计: %d 次（VA 失败日 %d + BS 豁免日补圈 %d，另跳过非工作日运行 %d 次、无结束时间运行 %d 次）"
          % (total, n_va_track, n_bs_track, skipped_non_workday, skipped_running))
    print("全量构建记录合计: %d 次（成功 + 失败，逐日归档由它生成；取证只走失败/取消运行）" % total_runs)
    if dash_failed:
        print("N2 接口失败流水线 %d 条（无执行记录，记待办）: %s"
              % (len(dash_failed), "、".join(dash_failed)))
    print("增量缓存: 复用上次取证 %d 次 / 新抓取 %d 次（剔除区间外缓存条目 %d 个，保留 %d 个，"
          "状态校准失效 %d 条）"
          % (n_reuse, total - n_reuse, len(dropped), len(cache["runs"]), n_stale))
    print("-> out/step_2_failed_runs.json")
    if not total_runs:
        print("区间内没有任何运行记录，流程提前结束")
        sys.exit(42)


if __name__ == "__main__":
    main()
