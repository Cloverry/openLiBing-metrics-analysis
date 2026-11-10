# -*- coding: utf-8 -*-
"""第 1 步 夜间统计与产品点灯（双维度：版本可用度 VA + 编译成功率 BS）：T1 发现产品清单 → 逐产品调官方 ops-nightly-detail → 产品级双指标点灯 → 待分析流水线（双维度并集）。

接口:
  POST /gateway/openlibing-ops/manage/ops-overview/main          产品清单发现（含 projectId 与官方产品级 nightlyVersionAvailabilityRate / nightlyBuildSuccessRate）
  POST /gateway/openlibing-ops/manage/common/detail              category=ops-nightly-detail，官方口径流水线明细（versionAvail / buildSuccess）
统计规则:
  - 产品级两指标直接取 ops-overview/main 返回字段（服务端工作日口径），只作展示与兜底口径
  - **采集范围 = 全部流水线全量采集**：看板比率是否已达 1 不再是准入条件。要求「每天的全部构建
    记录都保存下来」，达标流水线同样要查执行记录、同样要落归档，否则记录不完整；
    版本可用度 / 编译成功率改由第 2 步采集到的执行记录重算（第 7 步覆盖归档层比率），
    看板返回值只在该流水线区间内没有任何运行记录时兜底
  - dims 只表示该流水线在哪个维度上有口径：va 恒有（版本可用度对每条流水线都成立）；
    bs 仅当服务端返回了数值（github 平台无 Build* 任务，不计算编译成功率）
  - 指标只落数值（0-1 比率），**不落黄/绿/红**：灯色由页面按阈值就地渲染，归档里不存任何灯色字段
  - **流水线身份必须唯一**：key = "<产品>|<短名>"，短名同时是归档目录第二层、recordId 前缀
    与缓存键的一半。短名默认取 pipelineName 末段（历史目录名不变），同产品内末段重复时
    改用「去掉 Nightly-CI-*-pipeline_ 前缀的全名」，仍重复再追加源名 sha1 前 6 位。
    没有这一步，MindSpeed 的 5 条 *_master 会共用 key=MindSpeed|master，在第 2 步结果字典、
    第 7 步归档目录、records/ 与缓存四处互相覆盖 → 按时间区间筛选时流水线与记录数全部偏少
  - scheduled_only 标记的产品（config.analysis_policy）只采集定时任务运行（displayTitle 含 (scheduled)）
  - 编译成功率维度仅统计子任务名 Build*（大小写不敏感）开头任务的构建；一条流水线一次执行算一条数据（重试不重复计）
  - 两个接口都带重试（collect.detail_retry_times 次，退避 detail_retry_backoff_ms）：
    空响应体 / JSON 解析失败 / code!=200 / data 为空 都算失败并重试；**不比对流水线条数**（数量本身会增减，
    0 条也可能是真实结果）。重试后仍失败则整个第 1 步报错中止，绝不把失败当「当天无流水线」静默跳过
产物: out/step_1_nightly_raw.json / out/step_1_products.json / out/step_1_bad_pipelines.json
退出码: 0 成功；42 看板未返回任何流水线；1 失败
"""
import collections
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, date_range_source, load_config,
                          normalize_date_range, parse_rate, pct, run_page_js,
                          save_json)
from lib.paths import safe_name


def policy_sets(cfg):
    """口径例外（config.analysis_policy）：强制纳入分析的产品 / 只采定时任务的产品。"""
    pol = cfg.get("analysis_policy") or {}
    always = {str(x) for x in (pol.get("always_analyze") or []) if x}
    sched = {str(x) for x in (pol.get("scheduled_only") or []) if x}
    return always, sched


# 看板统一前缀：Nightly-CI-Component-pipeline_<组件> / Nightly-CI-Version-pipeline_<组件>
_PIPELINE_PREFIX = re.compile(r"^nightly-ci-[a-z]+-pipeline_", re.I)


def _short_tail(pname):
    """默认短名：pipelineName 末段（保持历史归档目录名不变，不做无谓迁移）。"""
    return pname.rsplit("_", 1)[-1] or pname


def _short_full(pname):
    """冲突时退一步：去掉看板公共前缀的全名（github 工作流文件名本就无前缀，原样保留）。"""
    m = _PIPELINE_PREFIX.match(pname)
    return (pname[m.end():] if m else pname) or pname


def assign_short_names(pairs):
    """同一产品内的流水线唯一短名：[(pipelineId, pipelineName)] -> {pipelineId: 短名}。

    短名不是展示字段，而是数据主键的一半（key / 归档目录 / recordId / 缓存键），
    同产品内重复就会在后续各步互相覆盖，所以这里必须做去重，规则可解释且稳定：
      1) 末段唯一的流水线照旧用末段 —— 已有归档目录名完全不变；
      2) 末段重复的（如 MindSpeed 的 5 条 *_master）改用去前缀全名；
      3) 去前缀后仍重复的，再追加源名 sha1 前 6 位，确保任何输入下都唯一。
    """
    pairs = list(pairs)
    tail = {}
    for pid, pname in pairs:
        tail[pid] = _short_tail(pname) if pname else "pipeline_%s" % pid
    tail_cnt = collections.Counter(tail.values())

    out, used = {}, set()
    for pid, pname in pairs:
        if tail_cnt[tail[pid]] == 1:
            out[pid] = tail[pid]
            used.add(tail[pid])

    cand = {}
    for pid, pname in pairs:
        if pid not in out:
            cand[pid] = _short_full(pname) if pname else tail[pid]
    cand_cnt = collections.Counter(cand.values())
    for pid, pname in pairs:
        if pid in out:
            continue
        s = cand[pid]
        if cand_cnt[s] > 1 or s in used:
            s = "%s~%s" % (s, hashlib.sha1(
                (pname or str(pid)).encode("utf-8")).hexdigest()[:6])
        out[pid] = s
        used.add(s)
    return out


def main():
    cfg = load_config()
    start, end = normalize_date_range(cfg)
    wanted = [p for p in (cfg.get("products") or []) if p]
    print("== 第 1 步 夜间统计·双指标（区间 %s ~ %s，%s）==" % (start, end, date_range_source(cfg)))
    if wanted:
        print("产品范围: %s" % ", ".join(wanted))

    col = cfg.get("collect") or {}
    retry_times = int(col.get("detail_retry_times") or 3)
    raw = run_page_js("step_1_nightly.js", {
        "startDate": start, "endDate": end,
        "t1PageSize": col["t1_page_size"],
        "detailPageSize": col["nightly_detail_page_size"],
        "onlyProducts": wanted,
        "delay": cfg["chrome"]["inter_request_delay_ms"],
        # 空响应体 / JSON 解析失败 / code!=200 / data 为空 → 模板内自动重试（1s、2s 退避）
        "maxTry": retry_times,
        "retryBackoffMs": col.get("detail_retry_backoff_ms") or [1000, 2000],
    }, "step_1_nightly_raw.json", cfg, "T1+nightly-detail")

    # 明细请求最终失败即中止：空响应曾被当成「当天无流水线」静默跳过，
    # 结果生成了只有部分产品的残缺归档（比率对得上、流水线数却是 0）。宁可不写，也不写半套。
    failures = raw.get("failures") or []
    if failures:
        detail = "；".join("%s（%s 次）: %s" % (f.get("product"), f.get("attempts"), f.get("error"))
                           for f in failures)
        raise RuntimeError("nightly 明细拉取重试 %d 次后仍失败，本次采集不完整，已中止（不生成残缺归档）：%s"
                           % (retry_times, detail))
    retried = sorted(n for n, v in (raw.get("retryInfo") or {}).items()
                     if v.get("ok") and int(v.get("attempts") or 1) > 1)
    if retried:
        print("明细请求重试后成功: %d 个产品（%s）" % (len(retried), ", ".join(retried)))

    targets = raw.get("targets") or []
    if wanted:
        missing = [w for w in wanted if w not in {t["name"] for t in targets}]
        if missing:
            raise RuntimeError("看板未发现指定产品: %s（请核对产品名）" % ", ".join(missing))
    print("产品数: %d" % len(targets))

    products_overview, bad_pipelines, notes = [], [], []
    always_analyze, scheduled_only = policy_sets(cfg)
    if always_analyze:
        notes.append("analysis_policy.always_analyze 已不再是特例（%s）：全量采集对所有产品默认生效，"
                     "比率一律按执行记录重算，该配置当前无额外作用"
                     % ", ".join(sorted(always_analyze)))
    for t in targets:
        name = t["name"]
        sched_only = name in scheduled_only
        resp = (raw.get("details") or {}).get(name) or {}
        recs = (resp.get("data") or {}).get("records") or []
        if resp.get("code") != 200 and not recs:
            notes.append("产品 %s nightly 明细拉取失败: %s" % (name, resp.get("messageCn") or resp.get("error") or ("code=" + str(resp.get("code")))))
            print("  !! %s 明细拉取失败: %s" % (name, resp.get("messageCn") or resp.get("error")))
        recs = [r for r in recs if r.get("pipelineId")]
        va = parse_rate(t.get("nightlyVersionAvailabilityRate"), hundred_scale=True)
        bs = parse_rate(t.get("nightlyBuildSuccessRate"), hundred_scale=True)
        if va is None:
            vals = [r.get("versionAvail") for r in recs
                    if isinstance(r.get("versionAvail"), (int, float))]
            if vals:
                notes.append("产品 %s ops-overview 未返回 nightlyVersionAvailabilityRate，"
                             "暂以流水线均值 %.2f%% 代替" % (name, sum(vals) / len(vals) * 100))
        if bs is None:
            notes.append("产品 %s ops-overview 未返回 nightlyBuildSuccessRate，产品级编译成功率无数据" % name)
        bad_va = [r for r in recs if isinstance(r.get("versionAvail"), (int, float)) and r["versionAvail"] != 1]
        bad_bs = [r for r in recs if isinstance(r.get("buildSuccess"), (int, float)) and r["buildSuccess"] != 1]
        no_va = [r for r in recs if not isinstance(r.get("versionAvail"), (int, float))]
        no_bs = [r for r in recs if not isinstance(r.get("buildSuccess"), (int, float))]
        if no_va:
            notes.append("产品 %s 有 %d 条流水线 versionAvail 无数据，未纳入可用度统计" % (name, len(no_va)))
        if no_bs:
            notes.append("产品 %s 有 %d 条流水线 buildSuccess 无数据，未纳入编译成功率统计（该流水线可能无 Build* 任务）" % (name, len(no_bs)))
        # 采集范围全量化：不看板比率是否达标，全部流水线都查执行记录、都落归档。
        # 版本可用度 / 编译成功率一律由第 2 步采到的逐运行记录重算（第 7 步覆盖归档比率），
        # 看板返回值只在该流水线区间内没有任何运行记录时兜底。
        picked = {}
        dims_map = {}
        for r in recs:
            pid = r.get("pipelineId")
            picked[pid] = r
            dims = ["va"]                       # 版本可用度：每条流水线都成立
            if isinstance(r.get("buildSuccess"), (int, float)):
                dims.append("bs")               # 仅当服务端返回了该口径（github 平台无 Build* 任务）
            dims_map[pid] = dims
        if sched_only:
            notes.append("产品 %s 只采集定时任务运行（displayTitle 含 (scheduled)），"
                         "含 PR 的运行一律不采集、不归档、不做根因分析" % name)
        bad_ids = {r.get("pipelineId") for r in bad_va + bad_bs}
        analyzed = bool(picked)
        # 同产品内短名去重：短名是 key / 归档目录 / recordId / 缓存键的主键一半，
        # 重复就会让同名流水线的执行记录在下游互相覆盖（按时间筛选时数据偏少）。
        short_map = assign_short_names(
            [(pid, r.get("pipelineName") or "") for pid, r in picked.items()])
        renamed = sorted("%s -> %s" % ((r.get("pipelineName") or ""), short_map[pid])
                         for pid, r in picked.items()
                         if (r.get("pipelineName") or "")
                         and short_map[pid] != _short_tail(r.get("pipelineName") or ""))
        if renamed:
            notes.append("产品 %s 有 %d 条流水线末段同名（如 %s），已改用去前缀全名作唯一短名，"
                         "避免执行记录互相覆盖: %s"
                         % (name, len(renamed), renamed[0].split(" -> ")[-1],
                            "；".join(renamed)))
            print("  短名去重 %d 条: %s" % (len(renamed), "；".join(renamed)))
        products_overview.append({
            "product": name, "projectId": t["projectId"],
            "versionAvailabilityRate": va,
            "buildSuccessRate": bs,
            "pipelineCount": len(recs),
            "badPipelineCount": len(bad_ids),
            "badPipelineCountVA": len(bad_va),
            "badPipelineCountBS": len(bad_bs),
            "analyze": analyzed,
            "collectedPipelineCount": len(picked),
            "rateRule": "runs",
            "recompute": True, "scheduledOnly": sched_only,
        })
        mark = "  <-- 全量采集 %d 条流水线（看板未达标 %d 条：VA %d / BS %d）" % (
            len(picked), len(bad_ids), len(bad_va), len(bad_bs))
        print("  {:<20} 可用度 {}  编译成功率 {}  流水线 {} 条{}".format(
            name, pct(va), pct(bs), len(recs), mark))
        if not analyzed:
            continue
        for r in picked.values():
            pname = r.get("pipelineName") or ""
            pid = r.get("pipelineId")
            short = short_map.get(pid) or (_short_tail(pname) if pname else "pipeline_%s" % pid)
            bad_pipelines.append({
                "key": "%s|%s" % (name, short),
                "product": name, "projectId": t["projectId"],
                "pipelineName": pname,
                "pipelineShortName": short,
                "pipelineId": pid, "platform": r.get("platform"),
                "versionAvailabilityRate": r.get("versionAvail"),
                "buildSuccessRate": r.get("buildSuccess"),
                "dims": dims_map.get(r.get("pipelineId")) or [],
                # rateRule=runs：比率一律按采到的执行记录重算；scheduledOnly=只采定时任务运行
                "rateRule": "runs",
                "recompute": True,
                "scheduledOnly": sched_only,
            })

    # 硬校验：key 与「归档目录两级名」都必须全局唯一。短名冲突会让第 2 步结果字典、
    # 第 7 步归档目录、records/ 与缓存互相覆盖，导致按时间区间筛选时数据凭空偏少，
    # 这种错误必须当场炸掉，绝不能带着残缺主键继续跑完 2~9 步。
    dup_key = [k for k, n in collections.Counter(b["key"] for b in bad_pipelines).items() if n > 1]
    dup_dir = ["%s/%s" % k for k, n in collections.Counter(
        (safe_name(b["product"]), safe_name(b["pipelineShortName"]))
        for b in bad_pipelines).items() if n > 1]
    if dup_key or dup_dir:
        raise RuntimeError("流水线主键不唯一（会互相覆盖执行记录）：key 重复 %s；归档目录重复 %s"
                           % (dup_key or "无", dup_dir or "无"))

    github = [b for b in bad_pipelines if (b.get("platform") or "").lower() == "github"]
    for b in github:
        notes.append("github 平台流水线 %s 执行记录与日志在 github 运行平台（T4 任务图谱/T5 日志导出/T6 用例接口不适用），"
                     "走第 5c 步 github 平台采集（jobs 元数据筛选 → 失败 job 精取 / 整包 zip）" % b["pipelineName"])
    save_json(OUT_DIR / "step_1_products.json",
              {"dateRange": {"start": start, "end": end},
               "products": products_overview, "notes": notes})
    save_json(OUT_DIR / "step_1_bad_pipelines.json",
              {"pipelines": bad_pipelines, "github": github, "notes": notes})

    n_va = len([b for b in bad_pipelines if "va" in (b.get("dims") or [])])
    n_bs = len([b for b in bad_pipelines if "bs" in (b.get("dims") or [])])
    print("\n产品概览 -> out/step_1_products.json")
    print("采集流水线: %d 条（版本可用度 %d / 编译成功率 %d，其中 github 平台 %d 条走第 5c 步）-> out/step_1_bad_pipelines.json"
          % (len(bad_pipelines), n_va, n_bs, len(github)))
    if not bad_pipelines:
        print("看板未返回任何流水线，流程提前结束")
        sys.exit(42)


if __name__ == "__main__":
    main()
