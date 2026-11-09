# -*- coding: utf-8 -*-
"""第 5b 步【T7】buildflow 子任务下钻：flow 接口拉构建子任务列表，失败子任务导出全量日志。

背景:   buildflow 任务的 exec-log/export 整 job 日志只有 build_flow 调度层记录
        （Follow jobs are failed / 您所提交的构建任务脚本(buildflow)中子任务…构建失败），
        子任务真实编译报错在调度层日志中不可见，需按子任务单独下钻。
链路:   GET /gateway/openlibing-cicd/project/pipeline/flow?stepRunIds=<失败步骤>
        → data[].builds[]（id=子任务构建实例 UUID、build_no、status、display_name）
        → 失败子任务 POST /project/pipeline/build-log/export（jobId=第 3 步从 T4 失败步骤
        inputs 里提取的 buildJobId，buildNo 来自 flow builds[]）
        → /export/detail 轮询 → /export/download-url OBS 直链 → Python 流式下载落盘
兜底:   导出失败/超时 → GET /project/pipeline/build/log（sort=DESC 尾部窗口，标记 truncated）
普通流水线 flow 返回空 builds → subTasks=[]，无子任务层，不产生额外请求。
范围:   第 3 步 log 路线任务（test 路线已有用例级证据，空测试回退任务已在第 5 步取整 job 日志）。
产物:   子任务日志 out/job_logs/NN_<流水线>_<构建号>_<任务>_<子任务>.log 并入 manifest.json；
        subTasks 字段（子任务状态 + 失败子任务日志与 errorLines）挂到
        out/step_5_log_evidence.json 对应任务行，缓存随之复用。
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, chunks, load_config, load_json,
                          run_page_js, save_json)
from lib.cache import (entry_key, load_cache, next_job_log_index,
                       write_cache_from_outputs)
from steps.step_5_logs import (LOG_DIR, download_file, make_excerpt, sanitize,
                              scan_error_lines)


def subtask_log_fields(st, path, cfg, download, truncated, has_more=None):
    """子任务日志落盘后的统一字段：大小/行数/错误行扫描/大日志摘录。"""
    coll = cfg["collect"]
    st["logFile"] = "job_logs/" + path.name
    st["download"] = download
    st["truncated"] = truncated
    st["logBytes"] = os.path.getsize(path)
    st["logLines"] = sum(1 for _ in open(path, "rb"))
    st["errorLines"] = scan_error_lines(path, coll["job_log_max_error_lines"])
    st["excerpt"] = make_excerpt(path, cfg)
    if has_more is not None:
        st["hasMore"] = has_more


def main():
    cfg = load_config()
    coll = cfg["collect"]
    params = load_json(OUT_DIR / "step_3_params.json")
    items = params.get("log") or []

    cache = load_cache(cfg)
    to_fetch, n_reuse = [], 0
    for it in items:
        e = (cache.get("runs") or {}).get(entry_key(it["key"], it["rid"]))
        row = None
        if e:
            for r in e.get("logEvidence") or []:
                if r.get("rid") == it["rid"] and r.get("jobId") == it.get("jobId"):
                    row = r
                    break
        subs = (row or {}).get("subTasks")
        # 复用条件同 _complete：subTasks 已判定（list）且失败子任务日志齐备
        if isinstance(subs, list) and all(st.get("logFile") for st in subs
                                          if st.get("status") == "FAILURE"):
            n_reuse += 1
        else:
            to_fetch.append(it)

    print("== 第 5b 步【T7】buildflow 子任务下钻（log 任务 %d 项：缓存复用 %d / 新下钻 %d）=="
          % (len(items), n_reuse, len(to_fetch)))
    if not items:
        print("无 log 路线任务，跳过")
        return
    if not to_fetch:
        print("全部命中缓存，本次未调用 flow/build-log 接口")
        return

    evid5_path = OUT_DIR / "step_5_log_evidence.json"
    if not evid5_path.exists():
        print("!! out/step_5_log_evidence.json 不存在，请先执行第 5 步")
        sys.exit(1)
    evid5 = load_json(evid5_path)

    delay = cfg["chrome"]["inter_request_delay_ms"]
    batch_size = max(1, min(cfg["chrome"]["batch_size"], 6))
    item_by = {(it["rid"], it["jobId"]): it for it in to_fetch}
    evidence, errors = {}, []
    for i, batch in enumerate(chunks(to_fetch, batch_size)):
        print("  下钻批次 %d/%d（%d 项）..." % (
            i + 1, (len(to_fetch) + batch_size - 1) // batch_size, len(batch)))
        out = run_page_js("step_5b_subtask.js",
                          {"items": batch, "delay": delay,
                           "conc": cfg["chrome"].get("request_concurrency", 4),
                           "pollTimeoutMs": coll["export_poll_timeout_ms"]},
                          "step_5b_subtask_batch%d.json" % (i + 1), cfg, "T7")
        for row in out.get("results") or []:
            it = item_by.get((row["rid"], row["jobId"]))
            if it is None:
                continue
            if row.get("flow") != "ok":
                # flow 拉取失败：不写 subTasks（条目保持不完整，下次重试）
                errors.append({"key": row["key"], "rn": row["rn"],
                               "jobName": row["jobName"],
                               "err": "flow: " + str(row.get("reason"))[:200]})
                continue
            subs = []
            for b in row.get("builds") or []:
                subs.append({
                    "name": b.get("name"), "buildNo": str(b.get("buildNo") or ""),
                    "buildJobId": b.get("buildJobId"), "status": b.get("status"),
                    "durationMs": b.get("durationMs"),
                    "startTime": b.get("startTime"), "finishTime": b.get("finishTime"),
                })
            evidence[(row["rid"], row["jobId"])] = subs
            n_fail = sum(1 for s in subs if s.get("status") == "FAILURE")
            if subs:
                print("    [flow] %s #%d %s：子任务 %d 个（失败 %d）"
                      % (it["pipeline"], it["rn"], it["jobName"], len(subs), n_fail))

        # 失败子任务日志：导出直链并发下载落盘 / 分页兜底写尾部窗口（序号先预分配，下载结果按原序回填）
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        idx = next_job_log_index(LOG_DIR) - 1
        jobs = []
        for sl in out.get("subLogs") or []:
            it = item_by.get((sl["rid"], sl["jobId"]))
            subs = evidence.get((sl["rid"], sl["jobId"])) or []
            st = next((s for s in subs
                       if s.get("name") == sl.get("subName")
                       and str(s.get("buildNo")) == str(sl.get("buildNo"))), None)
            if it is None or st is None:
                continue
            idx += 1
            fname = "%02d_%s_%s_%s_%s.log" % (
                idx, sanitize(it["pipeline"]), it["rn"],
                sanitize(it["jobName"]), sanitize(sl.get("subName")))
            jobs.append((sl, it, st, fname))

        def _dl(job):
            sl, it, st, fname = job
            path = LOG_DIR / fname
            try:
                download_file(sl["url"], path, coll["export_download_timeout_ms"])
                return (job, None)
            except Exception as e:
                return (job, e)

        dl_conc = max(1, int(coll.get("log_download_concurrency", 4)))
        dl_results = {}
        with ThreadPoolExecutor(max_workers=dl_conc) as ex:
            for job, err in ex.map(_dl, [j for j in jobs
                                         if j[0].get("export") == "ok" and j[0].get("url")]):
                dl_results[job[3]] = err

        for sl, it, st, fname in jobs:
            path = LOG_DIR / fname
            if sl.get("export") == "ok" and sl.get("url"):
                err = dl_results.get(fname)
                if err is None:
                    subtask_log_fields(st, path, cfg, "export", False)
                    print("    [export] %s %s bytes" % (fname, os.path.getsize(path)))
                else:
                    errors.append({"key": sl["key"], "rn": sl["rn"],
                                   "jobName": sl["jobName"],
                                   "err": "subtask %s download: %s" % (sl.get("subName"), str(err)[:160])})
                    st["logError"] = str(err)[:200]
            elif sl.get("paging") == "ok" and sl.get("logTail"):
                path.write_text(sl["logTail"], encoding="utf-8", errors="replace")
                subtask_log_fields(st, path, cfg, "paging", True, sl.get("hasMore"))
                print("    [paging] %s %s bytes（尾部窗口）" % (fname, os.path.getsize(path)))
            else:
                st["logError"] = sl.get("reason") or "子任务日志导出与分页兜底均失败"
                errors.append({"key": sl["key"], "rn": sl["rn"], "jobName": sl["jobName"],
                               "err": "subtask %s: %s" % (sl.get("subName"), st["logError"][:160])})

    # subTasks 挂到第 5 步证据行（缓存复用行的 subTasks 已随整行复用，无需处理）
    by_row = {(r.get("rid"), r.get("jobId")): r
              for r in evid5.get("results") or []}
    for k, subs in evidence.items():
        row = by_row.get(k)
        if row is None:
            errors.append({"key": (item_by.get(k) or {}).get("key"),
                           "rn": (item_by.get(k) or {}).get("rn"),
                           "err": "step5 证据行缺失，子任务证据未挂载"})
            continue
        row["subTasks"] = subs
    evid5.setdefault("errors", []).extend(errors)
    save_json(evid5_path, evid5)

    # manifest 并入子任务日志条目
    manifest_add = {}
    for k, subs in evidence.items():
        it = item_by.get(k) or {}
        for st in subs:
            if not st.get("logFile"):
                continue
            manifest_add["%s|%s|%s" % (k[0], k[1], st["name"])] = {
                "file": st["logFile"].split("/")[-1],
                "bytes": st.get("logBytes"), "lines": st.get("logLines"),
                "download": st.get("download"), "truncated": st.get("truncated"),
                "excerpt": st.get("excerpt"),
                "rn": it.get("rn"), "jobName": it.get("jobName"),
                "subTask": st["name"],
            }
    if manifest_add:
        mf_path = LOG_DIR / "manifest.json"
        manifest = {**load_json(mf_path), **manifest_add} if mf_path.exists() else manifest_add
        save_json(mf_path, manifest)

    n_flow = len(evidence)
    n_buildflow = sum(1 for s in evidence.values() if s)
    n_subs = sum(len(s) for s in evidence.values())
    n_fail = sum(1 for s in evidence.values() for x in s if x.get("status") == "FAILURE")
    n_ok = sum(1 for s in evidence.values() for x in s
               if x.get("status") == "FAILURE" and x.get("logFile"))
    print("子任务下钻 %d 项（buildflow %d 项 / 普通流水线 %d 项）| 子任务 %d 个（失败 %d，"
          "失败子任务日志取到 %d）| 采集错误 %d"
          % (n_flow, n_buildflow, n_flow - n_buildflow, n_subs, n_fail, n_ok, len(errors)))
    n_c = write_cache_from_outputs(cfg, OUT_DIR)
    print("增量缓存: 写入 %d 条（完整 %d）| 保留 %d 条 | 剔除区间外 %d 条" % n_c)
    print("-> out/job_logs/（子任务全量日志）+ out/step_5_log_evidence.json（subTasks 字段）+ out/cache/collection.json")


if __name__ == "__main__":
    main()
