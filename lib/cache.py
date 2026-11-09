# -*- coding: utf-8 -*-
"""增量采集缓存：上一次已取证的运行在新一次分析中直接复用，仅补抓新增运行。

缓存单位: 一次流水线运行（流水线 key + pipelineRunId），条目内含该运行的任务分流
          （step3 项）、测试取证（step4 行）、用例日志解析结果（case_errors 条目）、
          整 job 日志取证（step5 行）与采集错误。
复用条件: 条目 complete=true（上次采集无错误、全部任务有证据、用例日志全部下载成功）
          且引用的日志文件仍在磁盘上。
剔除规则: 条目运行日期（结束日，旧条目回退开始日）在本次分析区间 [start, end] 之外的一律剔除；
          失效条目（文件缺失/上次有错误）本次重新抓取并覆盖。
"""
import datetime
from collections import defaultdict
from pathlib import Path

from lib.cdp_exec import (OUT_DIR, load_json, normalize_date_range,
                          save_json)
from lib.paths import CACHE_DIR, OUT_DIR as OUT_ROOT

CACHE_VERSION = 2


def cache_enabled(cfg):
    return bool((cfg.get("cache") or {}).get("enabled", True))


def cache_file(cfg):
    """采集缓存放在 out/state/cache/（跨日期状态区，不随单次运行的中间产物清理）。"""
    name = (cfg.get("cache") or {}).get("path") or "collection.json"
    name = name.replace("\\", "/").split("/")[-1]
    return CACHE_DIR / name


def load_cache(cfg):
    p = cache_file(cfg)
    if p.exists():
        try:
            c = load_json(p)
        except Exception:
            c = None
        if c and c.get("version") == CACHE_VERSION:
            c.setdefault("runs", {})
            return c
    return {"version": CACHE_VERSION, "lastFetch": None, "runs": {}}


def save_cache(cfg, cache):
    save_json(cache_file(cfg), cache)


def entry_key(key, rid):
    return "%s#%s" % (key, rid)


def prune_cache(cache, start, end):
    """剔除运行日期（结束日，旧条目回退开始日）在区间外的条目，返回被剔除的 key 列表。"""
    dropped = []
    for k in list((cache.get("runs") or {}).keys()):
        e = cache["runs"][k]
        d = str(e.get("endISO") or e.get("startISO") or "")[:10]
        if d and not (start <= d <= end):
            dropped.append(k)
            del cache["runs"][k]
    return dropped


def _files_ok(entry, out_dir):
    for r in entry.get("logEvidence") or []:
        lf = r.get("logFile")
        if lf and not (out_dir / lf).exists():
            return False
        for st in r.get("subTasks") or []:
            slf = st.get("logFile")
            if slf and not (out_dir / slf).exists():
                return False
    gh = entry.get("github")
    if gh:
        for j in gh.get("jobs") or []:
            lf = j.get("logFile")
            if lf and not (out_dir / lf).exists():
                return False
        for sf in gh.get("systemLogs") or []:
            if not (out_dir / sf).exists():
                return False
    for e in entry.get("caseErrors") or []:
        if e.get("downloadStatus") == "ok":
            lf = e.get("localLog")
            if lf and not (out_dir / lf).exists():
                return False
    return True


def _candidate_dirs(entry, out_dir):
    """证据文件可能的落点：归档目录（step_7 归档后写入 archiveDir）与中间产物目录。"""
    dirs = []
    ad = (entry or {}).get("archiveDir")
    if ad:
        dirs.append(OUT_ROOT / ad)
    if out_dir is not None:
        dirs.append(Path(out_dir))
    return dirs


def entry_valid(entry, out_dir=None):
    """复用校验：complete 且引用的日志文件在磁盘上（归档目录或中间产物目录其一）。"""
    if not entry or not entry.get("complete"):
        return False
    for d in _candidate_dirs(entry, out_dir):
        if d.exists() and _files_ok(entry, d):
            return True
    return False


def _complete(entry, out_dir):
    """条目完整性判定（保守但非阻塞）。

    - step3(T4) 出错（任务图谱未知）→ 不完整，下次重抓
    - 期望任务缺少证据行 / 空测试未回退 / 日志完全未取到 / 用例日志下载失败 → 不完整
    - step4/step5 的过程性错误（如导出超时后分页兜底成功）不阻塞复用
    """
    if any(err.get("src") == "step3" for err in entry.get("errors") or []):
        return False
    tasks = entry.get("tasks") or []
    exp_test = {(t["rid"], t["jobId"]) for t in tasks if t.get("route") == "test"}
    exp_log = {(t["rid"], t["jobId"]) for t in tasks if t.get("route") == "log"}
    got_test = {(r["rid"], r["jobId"]) for r in entry.get("testEvidence") or []}
    got_log = {(r["rid"], r["jobId"]) for r in entry.get("logEvidence") or []}
    if not exp_test <= got_test or not exp_log <= got_log:
        return False
    for r in entry.get("testEvidence") or []:
        if r.get("empty") and (r["rid"], r["jobId"]) not in got_log:
            return False
    for r in entry.get("logEvidence") or []:
        if r.get("logFile") is None:
            return False
        # 子任务下钻（step5b）必须已执行：非回退行缺 subTasks 键说明未下钻；
        # 失败子任务无日志（导出+兜底均失败）同样不完整，下次重抓
        if not r.get("fallback") and "subTasks" not in r:
            return False
        for st in r.get("subTasks") or []:
            if st.get("status") == "FAILURE" and not st.get("logFile"):
                return False
    # github 平台运行（step5c）：证据行必须存在且目标 job 全部取到日志或确定性 404
    if (entry.get("pipelineMeta") or {}).get("platform") == "github":
        if not entry.get("github") or not entry["github"].get("ghComplete"):
            return False
    for e in entry.get("caseErrors") or []:
        if e.get("downloadStatus") != "ok":
            return False
    return any(_files_ok(entry, d) for d in _candidate_dirs(entry, out_dir))


def next_job_log_index(log_dir):
    """已有 job 日志文件名的最大序号 + 1（新导出续号，避免覆盖缓存文件）。"""
    idx = 0
    if log_dir.exists():
        for f in log_dir.iterdir():
            head = f.name.split("_", 1)[0]
            if head.isdigit():
                idx = max(idx, int(head))
    return idx + 1


def _strip_cached(items):
    out = []
    for t in items or []:
        t = dict(t)
        t.pop("cached", None)
        out.append(t)
    return out


def _err_attribution(rows, key_field="key", rn_field="rn"):
    by = defaultdict(list)
    for r in rows or []:
        by[(r.get(key_field), r.get(rn_field))].append(r)
    return by


def write_cache_from_outputs(cfg, out_dir, start=None, end=None):
    """从当前 out/ 各步产物重建缓存（step5 末尾与 bootstrap 共用）。

    每个待查运行一个条目；运行日期在 [start, end] 之外的不写入。
    返回 (写入条目数, 完整条目数, 保留的既有条目数, 剔除数)。
    """
    if start is None or end is None:
        start, end = normalize_date_range(cfg)
    s2 = load_json(out_dir / "step_2_failed_runs.json")
    s3 = load_json(out_dir / "step_3_params.json")
    s4 = load_json(out_dir / "step_4_test_evidence.json")
    s5 = load_json(out_dir / "step_5_log_evidence.json")
    ce = load_json(out_dir / "step_4_case_errors.json")
    s1b = load_json(out_dir / "step_1_bad_pipelines.json")
    p5c = out_dir / "step_5c_github_evidence.json"
    s5c = load_json(p5c) if p5c.exists() else {"results": [], "errors": []}
    pmeta = {b["key"]: b for b in s1b["pipelines"]}

    tasks_by, s4_by, s5_by, ce_by = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    s5c_by = {}
    for it in (s3.get("test") or []) + (s3.get("log") or []):
        tasks_by[(it["key"], it["rid"])].append(it)
    for r in s4.get("results") or []:
        s4_by[(r["key"], r["rid"])].append(r)
    for r in s5.get("results") or []:
        s5_by[(r["key"], r["rid"])].append(r)
    for r in s5c.get("results") or []:
        s5c_by[(r["key"], r["rid"])] = r
    url_run = {}
    for rows in s4_by.values():
        for r in rows:
            for c in r.get("failedCases") or []:
                u = c.get("resultDownloadUrl")
                if u:
                    url_run[u] = (r["key"], r["rid"])
    ce_seen = set()
    for e in ce or []:
        u = e.get("url")
        k = url_run.get(u)
        if k and u not in ce_seen:
            ce_seen.add(u)
            ce_by[k].append(e)

    errs_by_rn = defaultdict(list)
    for src, rows in (("step3", s3.get("errors")),
                      ("step4", s4.get("errors")),
                      ("step5", s5.get("errors")),
                      ("step5c", s5c.get("errors"))):
        for r in rows or []:
            errs_by_rn[(r.get("key"), r.get("rn"))].append({"src": src, **r})

    cache = load_cache(cfg)
    dropped = prune_cache(cache, start, end)
    kept = len(cache["runs"])
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    written, complete_n = 0, 0
    for key, info in s2.items():
        if key == "_meta":
            continue
        for fr in info["failedRuns"]:
            rid = fr.get("rid")
            if not rid:
                continue
            k = (key, rid)
            start_iso = fr.get("startISO")
            if not start_iso:
                for h in info.get("runHistory") or []:
                    if h.get("rn") == fr.get("rn") and h.get("start"):
                        start_iso = h["start"].replace(" ", "T")
                        break
            end_iso = fr.get("endISO")
            if not end_iso:
                for h in info.get("runHistory") or []:
                    if h.get("rn") == fr.get("rn") and h.get("end"):
                        end_iso = h["end"].replace(" ", "T")
                        break
            s4_rows = s4_by.get(k) or []
            url_set = {c.get("resultDownloadUrl")
                       for r in s4_rows for c in (r.get("failedCases") or [])}
            entry = {
                "key": key, "rn": fr["rn"], "rid": rid,
                "status": fr.get("status"), "st": fr.get("start"),
                "startISO": start_iso,
                "endISO": end_iso,
                "openlibingUrl": fr.get("openlibingUrl"),
                "detailUrl": fr.get("detailUrl"),
                "collectedAt": now,
                "pipelineMeta": {
                    "product": pmeta[key]["product"],
                    "projectId": pmeta[key]["projectId"],
                    "pipelineId": pmeta[key]["pipelineId"],
                    "pipeline": pmeta[key]["pipelineShortName"],
                    "pipelineName": pmeta[key]["pipelineName"],
                    "platform": pmeta[key].get("platform"),
                },
                "tasks": _strip_cached(tasks_by.get(k)),
                "testEvidence": s4_rows,
                "logEvidence": s5_by.get(k) or [],
                "github": s5c_by.get(k),
                "caseErrors": [e for e in ce_by.get(k) or []
                               if e.get("url") in url_set],
                "errors": errs_by_rn.get((key, fr["rn"])) or [],
            }
            entry["complete"] = _complete(entry, out_dir)
            cache["runs"][entry_key(key, rid)] = entry
            written += 1
            complete_n += bool(entry["complete"])
    cache["lastFetch"] = {
        "at": now, "start": start, "end": end,
        "runsWritten": written, "runsComplete": complete_n,
    }
    save_cache(cfg, cache)
    return written, complete_n, kept, len(dropped)
