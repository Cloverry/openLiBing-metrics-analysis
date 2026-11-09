# -*- coding: utf-8 -*-
"""第 3 步【接口 T4】运行详情 → 失败任务图谱 → 按任务名三路分流生成采集参数。

接口: GET /gateway/openlibing-cicd/project/pipeline/pipeline-run/detail
响应: data.stages[].jobs[]，job.id=jobRunId，job.steps[].id=stepRunId
分流: 任务名 test 开头（不区分大小写）→ test 路线（第 4 步 T6）；否则 → log 路线（第 5 步 T5）
剔除: 任务状态 UNSELECTED/CANCELED/IGNORED/INIT、非 FAILED 及无失败步骤的任务
BS 轨道过滤（step2 track=bs 的豁免日补圈运行）: 仅保留 Build*（大小写不敏感开头）失败任务；
     Build 全成功的 bs-only 运行记入 bsCleanRuns（服务端 buildSuccess 计数疑似有误，
     报告在共性（编译成功率）章节标注「工具计算错误」）
产物: out/step_3_details_raw.json / out/step_3_params.json
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, chunks, load_config, load_json,
                          run_page_js, save_json)
from lib.cache import cache_enabled, entry_key, load_cache

SKIP_JOB_STATUS = {"UNSELECTED", "CANCELED", "IGNORED", "INIT"}


def walk_jobs(data):
    for st in data.get("stages") or []:
        for job in st.get("jobs") or []:
            yield st.get("name"), job


def main():
    cfg = load_config()
    runs_info = load_json(OUT_DIR / "step_2_failed_runs.json")
    meta = {b["key"]: b for b in load_json(OUT_DIR / "step_1_bad_pipelines.json")["pipelines"]}
    print("== 第 3 步【T4】失败任务分流 ==")

    runs_param = []
    n_cached_runs = 0
    n_gh_runs = 0
    for key, info in runs_info.items():
        if key == "_meta":
            continue
        for fr in info["failedRuns"]:
            # github 平台运行无 T4 任务图谱（openLiBing 侧仅透传 GitHub run 状态），
            # 由第 5c 步按 GitHub jobs 元数据分流
            if fr.get("platform") == "github" or \
                    (meta[key].get("platform") or "").lower() == "github":
                n_gh_runs += 1
                continue
            runs_param.append({
                "key": key, "rn": fr["rn"], "st": fr["start"], "status": fr["status"],
                "cached": bool(fr.get("cached")),
                "track": fr.get("track") or "va",
                "dims": fr.get("dims") or [],
                "projectId": meta[key]["projectId"], "pipelineId": meta[key]["pipelineId"],
                "pipelineRunId": fr["rid"],
            })
            n_cached_runs += bool(fr.get("cached"))
    fetch_runs = [r for r in runs_param if not r["cached"]]
    print("待查运行 %d 次（缓存复用 %d / 新抓取 %d，其中 BS 豁免日补圈 %d；另 github 平台 %d 次转第 5c 步）"
          % (len(runs_param), n_cached_runs, len(fetch_runs),
             sum(1 for r in runs_param if r["track"] == "bs"), n_gh_runs))

    batch_size = cfg["chrome"]["batch_size"]
    delay = cfg["chrome"]["inter_request_delay_ms"]
    details, errors = [], []
    for i, batch in enumerate(chunks(fetch_runs, batch_size)):
        print("  批次 %d/%d（%d 个运行）..." % (
            i + 1, (len(fetch_runs) + batch_size - 1) // batch_size, len(batch)))
        out = run_page_js("step_3_details.js",
                          {"runs": batch, "delay": delay,
                           "conc": cfg["chrome"].get("request_concurrency", 4)},
                          "step_3_details_batch%d.json" % (i + 1), cfg, "T4")
        details.extend(out.get("details") or [])
        errors.extend(out.get("errors") or [])
    save_json(OUT_DIR / "step_3_details_raw.json", {"details": details})

    def collect_items(d, track):
        """从 T4 详情提取失败任务条目；BS 轨道仅保留 Build* 任务。"""
        key = d.get("key")
        if "err" in d or d.get("code") != 200:
            errors.append({"key": key, "rn": d.get("rn"),
                           "err": d.get("err") or ("code=" + str(d.get("code")))})
            return [], []
        items, bs_clean = [], []
        for stage_name, job in walk_jobs(d.get("data") or {}):
            jstatus = job.get("status")
            if jstatus in SKIP_JOB_STATUS or jstatus in ("COMPLETED", None):
                continue
            if jstatus != "FAILED":
                skipped.append((key, d.get("rn"), job.get("name"), jstatus))
                continue
            bad_steps = [s for s in (job.get("steps") or [])
                         if s.get("status") not in ("COMPLETED", None)]
            if not bad_steps:
                skipped.append((key, d.get("rn"), job.get("name"), "FAILED_NO_STEPS"))
                continue
            jname = job.get("name") or ""
            is_build = jname.lower().startswith("build")
            if track == "bs" and not is_build:
                skipped.append((key, d.get("rn"), jname, "BS_TRACK_NON_BUILD"))
                continue
            # step.inputs[] 里 key=jobId 的值为云构建（buildflow）任务的构建 jobId，
            # build-log/export 与 build/log 下钻子任务日志时必填（stepRunId 仍为父步骤 id）
            step_recs = []
            build_job_id = None
            for s in bad_steps:
                sinputs = {kv.get("key"): kv.get("value")
                          for kv in s.get("inputs") or []}
                jid = sinputs.get("jobId")
                if jid and not build_job_id:
                    build_job_id = jid
                step_recs.append({"id": s.get("id"), "name": s.get("name"),
                                  "status": s.get("status"), "errMsg": s.get("message"),
                                  "lastDispatchId": s.get("last_dispatch_id"),
                                  "jobIdParam": jid})
            item = {
                "key": key, "pipeline": meta[key]["pipelineShortName"],
                "pipelineName": meta[key]["pipelineName"],
                "projectId": meta[key]["projectId"], "pipelineId": meta[key]["pipelineId"],
                "rid": d["rid"], "rn": d["rn"], "st": d["st"],
                "jobId": job.get("id"), "jobName": jname,
                "stepIds": [s["id"] for s in bad_steps],
                "steps": step_recs,
                "jobErrMsg": job.get("message"),
                "jobLastDispatchId": job.get("last_dispatch_id"),
                "route": "test" if jname.lower().startswith("test") else "log",
                "isBuild": is_build,
                "buildJobId": build_job_id,
            }
            items.append(item)
        if track == "bs" and not any(i["isBuild"] for i in items):
            bs_clean.append({"key": key, "rn": d.get("rn"), "rid": d.get("rid"),
                             "st": d.get("st"), "status": d.get("status")})
        return items, bs_clean

    test_items, log_items, skipped, bs_clean_runs = [], [], [], []
    for d in details:
        items, bs_clean = collect_items(d, d.get("track") or "va")
        for it in items:
            (test_items if it["route"] == "test" else log_items).append(it)
        bs_clean_runs.extend(bs_clean)

    # 缓存复用的运行：任务分流直接取上次结果，不再调 T4
    if n_cached_runs:
        cache = load_cache(cfg)
        for r in runs_param:
            if not r["cached"]:
                continue
            e = (cache.get("runs") or {}).get(entry_key(r["key"], r["pipelineRunId"]))
            if not e:
                continue
            n_build_added = 0
            for t in e.get("tasks") or []:
                item = dict(t)
                item["cached"] = True
                if item.get("isBuild") is None:
                    item["isBuild"] = (item.get("jobName") or "").lower().startswith("build")
                # BS 轨道复用同样只保留 Build* 失败任务
                if r["track"] == "bs" and not item["isBuild"]:
                    continue
                n_build_added += bool(item["isBuild"])
                (test_items if item["route"] == "test" else log_items).append(item)
            # BS 补圈运行缓存复用时同样判定 bsClean（无 Build 失败任务）
            if r["track"] == "bs" and n_build_added == 0:
                bs_clean_runs.append({"key": r["key"], "rn": r["rn"],
                                       "rid": r["pipelineRunId"],
                                       "st": r["st"], "status": r["status"]})

    # 缓存条目补 isBuild 标注（历史条目无此字段）
    for it in test_items + log_items:
        if it.get("isBuild") is None:
            it["isBuild"] = (it.get("jobName") or "").lower().startswith("build")

    save_json(OUT_DIR / "step_3_params.json",
              {"test": test_items, "log": log_items, "skipped": skipped,
               "bsCleanRuns": bs_clean_runs, "errors": errors})
    n_cached_items = sum(1 for i in test_items + log_items if i.get("cached"))
    n_build = sum(1 for i in test_items + log_items if i.get("isBuild"))
    print("\n采集参数: test 路线 %d 项 / log 路线 %d 项（其中 Build* 任务 %d 项）/ 剔除噪音 %d 项 / 错误 %d 项（缓存复用 %d 项）"
          % (len(test_items), len(log_items), n_build, len(skipped), len(errors), n_cached_items))
    if bs_clean_runs:
        print("BS 补圈运行 %d 次无 Build 失败任务（服务端 buildSuccess 计数疑似有误，报告将标注工具计算错误）"
              % len(bs_clean_runs))
    print("-> out/step_3_params.json")


if __name__ == "__main__":
    main()
