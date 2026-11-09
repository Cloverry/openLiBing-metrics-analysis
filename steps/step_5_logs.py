# -*- coding: utf-8 -*-
"""第 5 步【T5+E】log 路线取证：整 job 全量日志导出落盘 + 分页兜底 + 行级错误扫描。

主路径: POST /gateway/openlibing-cicd/project/pipeline/exec-log/export（页面「下载」按钮同款）
        → GET /gateway/openlibing-framework/export/detail 轮询 → /export/download-url 拿 OBS 直链
        → Python 流式下载整 job 日志落盘（150MB+ 不截断）
兜底:   导出失败/超时 → 原分页接口 POST /project/pipeline/logs（有 400 页/24MB 截断上限，标记 truncated）
范围:   log 路线任务 + 第 4 步空测试结果任务回退（test 非空任务的用例日志已在第 4 步落盘，不重复导出）
后处理: 全文件错误行扫描（带行号，L<行号>: 原文）；≥阈值大日志生成摘录（头 N 行+错误行+尾 M 行）
产物:   out/job_logs/NN_<流水线>_<构建号>_<任务>.log（+ _excerpt.log 摘录）/
        out/job_logs/manifest.json / out/step_5_log_evidence.json
"""
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, chunks, load_config, load_json,
                          run_page_js, save_json)
from lib.cache import (entry_key, load_cache, next_job_log_index,
                       write_cache_from_outputs)

LOG_DIR = OUT_DIR / "job_logs"
SAN_RE = re.compile(r'[\\/:*?"<>|\s]+')
ERR_LINE_RE = re.compile(r"\[ERROR\]|\[FAIL|ERROR|FAIL|失败|Traceback|AssertionError|Exception", re.I)


def sanitize(s):
    return SAN_RE.sub("_", str(s or "")).strip("._")[:80] or "job"


def download_file(url, path, timeout_ms):
    """OBS 直链流式下载（浏览器外直接拉，绕开 OBS 无 CORS 头的限制）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=timeout_ms // 1000) as resp, \
            open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)


def scan_error_lines(path, max_lines):
    """全文件错误行扫描，返回 ['L<行号>: <原文>', ...]；超上限保留首 10 + 末尾。"""
    hits = []
    with open(path, "rb") as f:
        for ln, raw in enumerate(f, 1):
            try:
                text = raw.decode("utf-8", "ignore").rstrip("\r\n")
            except Exception:
                continue
            if ERR_LINE_RE.search(text):
                hits.append((ln, text.strip()[:400]))
    if len(hits) > max_lines:
        hits = hits[:10] + hits[-(max_lines - 10):]
    return ["L%d: %s" % (ln, t) for ln, t in hits]


def make_excerpt(path, cfg):
    """≥阈值大日志生成摘录：头 N 行 + 错误命中行(≤K) + 尾 M 行，行号前缀 L<n>: 便于回全量核对。"""
    coll = cfg["collect"]
    if os.path.getsize(path) < coll["excerpt_threshold_bytes"]:
        return None
    head_n, err_max, tail_n = (coll["excerpt_head_lines"],
                               coll["excerpt_error_max_lines"],
                               coll["excerpt_tail_lines"])
    head, err_hits, total = [], [], 0
    with open(path, "rb") as f:
        for ln, raw in enumerate(f, 1):
            total = ln
            line = raw.decode("utf-8", "ignore").rstrip("\r\n")
            if ln <= head_n:
                head.append("L%d: %s" % (ln, line))
            if ERR_LINE_RE.search(line) and len(err_hits) < err_max:
                err_hits.append("L%d: %s" % (ln, line.strip()[:400]))
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 2 * 1024 * 1024))
        tail_all = f.read().decode("utf-8", "ignore").splitlines()
    tail_off = total - len(tail_all) + 1
    tail = ["L%d: %s" % (tail_off + i, t) for i, t in enumerate(tail_all[-tail_n:])]

    out = [">>> 头部 %d 行" % head_n, *head,
           ">>> 错误命中行（≤%d，行号不连续，引用前回全量日志 grep -n 核对）" % err_max, *err_hits,
           ">>> 尾部 %d 行" % tail_n, *tail]
    ep = path.with_suffix(".excerpt.log")
    ep.write_text("\n".join(out), encoding="utf-8")
    return ep.name


def main():
    cfg = load_config()
    coll = cfg["collect"]
    items = load_json(OUT_DIR / "step_3_params.json")["log"]

    evid_file = OUT_DIR / "step_4_test_evidence.json"
    evid = load_json(evid_file) if evid_file.exists() else {"results": []}
    fallback_items, seen = [], {(it["rid"], it["jobId"]) for it in items}
    for r in evid.get("results") or []:
        if not r.get("empty"):
            continue
        k = (r["rid"], r["jobId"])
        if k in seen:
            continue
        seen.add(k)
        fallback_items.append({
            "key": r["key"], "pipeline": r["pipeline"], "pipelineName": r.get("pipelineName"),
            "projectId": r["projectId"], "pipelineId": r["pipelineId"],
            "rid": r["rid"], "rn": r["rn"], "st": r["st"],
            "jobId": r["jobId"], "jobName": r["jobName"], "stepIds": r["stepIds"],
            "steps": r.get("steps") or [], "jobErrMsg": r.get("jobErrMsg"),
            "jobLastDispatchId": r.get("jobLastDispatchId"),
            "route": "log", "fallback": True,
        })
    items.extend(fallback_items)

    # 缓存复用：上次已取证的（rid, jobId）直接复用，本次仅新抓取
    cache = load_cache(cfg)
    cached_rows, to_fetch = [], []
    for it in items:
        row = None
        e = (cache.get("runs") or {}).get(entry_key(it["key"], it["rid"]))
        if e:
            for r in e.get("logEvidence") or []:
                if r.get("rid") == it["rid"] and r.get("jobId") == it.get("jobId"):
                    row = r
                    break
        if row is not None:
            cached_rows.append(row)
        else:
            to_fetch.append(it)

    print("== 第 5 步【T5+E】任务日志取证（log 路线 %d 项 + 空测试回退 %d 项：缓存复用 %d / 新抓取 %d）=="
          % (len(items) - len(fallback_items), len(fallback_items),
             len(cached_rows), len(to_fetch)))
    if not to_fetch:
        results = list(cached_rows)
        save_json(OUT_DIR / "step_5_log_evidence.json", {"results": results, "errors": []})
        n_c = write_cache_from_outputs(cfg, OUT_DIR)
        print("全部命中缓存，本次未调用导出/分页接口")
        print("log 任务 %d 项（回退 %d）| 全部缓存复用"
              % (len(results), sum(1 for r in results if r.get("fallback"))))
        print("增量缓存: 写入 %d 条（完整 %d）| 保留 %d 条 | 剔除区间外 %d 条" % n_c)
        print("-> out/step_5_log_evidence.json + out/cache/collection.json")
        return

    delay = cfg["chrome"]["inter_request_delay_ms"]
    batch_size = max(1, min(cfg["chrome"]["batch_size"], 6))
    export_ok, errors = {}, []

    # 阶段 1：导出任务创建 + 轮询 + 直链
    for i, batch in enumerate(chunks(to_fetch, batch_size)):
        print("  导出批次 %d/%d（%d 项）..." % (
            i + 1, (len(to_fetch) + batch_size - 1) // batch_size, len(batch)))
        out = run_page_js("step_5_export.js",
                          {"items": batch, "delay": delay,
                           "conc": cfg["chrome"].get("request_concurrency", 4),
                           "pollTimeoutMs": coll["export_poll_timeout_ms"]},
                          "step_5_export_batch%d.json" % (i + 1), cfg, "T5-EXPORT")
        for r in out.get("results") or []:
            if r.get("export") == "ok":
                export_ok[(r["rid"], r["jobId"])] = r
            else:
                errors.append({"key": r.get("key"), "rn": r.get("rn"),
                               "jobName": r.get("jobName"),
                               "err": "export " + r.get("export", "?") + ": " + str(r.get("reason"))[:200]})

    # 阶段 2：OBS 直链下载落盘（文件序号预分配续号，避免覆盖缓存文件；并发下载）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_meta, dl_failed = {}, []
    idx = next_job_log_index(LOG_DIR) - 1
    dl_jobs = []
    for it in to_fetch:
        k = (it["rid"], it["jobId"])
        if k not in export_ok:
            dl_failed.append(it)
            continue
        idx += 1
        fname = "%02d_%s_%s_%s.log" % (idx, sanitize(it["pipeline"]),
                                       it["rn"], sanitize(it["jobName"]))
        dl_jobs.append((k, it, export_ok[k], fname))

    def _dl(job):
        k, it, r, fname = job
        path = LOG_DIR / fname
        try:
            download_file(r["url"], path, coll["export_download_timeout_ms"])
            return (job, None)
        except Exception as e:
            return (job, e)

    dl_conc = max(1, int(coll.get("log_download_concurrency", 4)))
    with ThreadPoolExecutor(max_workers=dl_conc) as ex:
        for job, err in ex.map(_dl, dl_jobs):
            k, it, r, fname = job
            path = LOG_DIR / fname
            if err is None:
                log_meta[k] = {"it": it, "file": fname, "download": "export",
                               "truncated": False, "exportId": r.get("exportId")}
                print("    [export] %s %s bytes" % (fname, os.path.getsize(path)))
            else:
                errors.append({"key": it["key"], "rn": it["rn"], "jobName": it["jobName"],
                               "err": "download: " + str(err)[:200]})
                dl_failed.append(it)
    print("全量日志导出 %d/%d 项成功" % (len(log_meta), len(to_fetch)))

    # 阶段 3：导出失败项走分页兜底（有截断上限）
    if dl_failed:
        print("  分页兜底 %d 项（分页接口有页数/字节上限，标记 truncated）..." % len(dl_failed))
        bs = cfg["chrome"]["batch_size"]
        for i, batch in enumerate(chunks(dl_failed, bs)):
            out = run_page_js("step_5_logs.js",
                              {"items": batch, "logLimit": coll["t5_log_limit"],
                               "maxErrLines": 40, "delay": delay,
                               "conc": cfg["chrome"].get("request_concurrency", 4)},
                              "step_5_paging_batch%d.json" % (i + 1), cfg, "T5-PAGING")
            for r in out.get("results") or []:
                it = next((x for x in dl_failed
                           if x["rid"] == r["rid"] and x["jobId"] == r["jobId"]), None)
                if it is None:
                    continue
                idx += 1
                fname = "%02d_%s_%s_%s.log" % (idx, sanitize(it["pipeline"]),
                                              it["rn"], sanitize(it["jobName"]))
                text = r.get("logTail") or ""
                (LOG_DIR / fname).write_text(text, encoding="utf-8", errors="replace")
                log_meta[(it["rid"], it["jobId"])] = {
                    "it": it, "file": fname, "download": "paging",
                    "truncated": True, "hasMore": r.get("has_more")}

    # 阶段 4：全文件错误行扫描 + 大日志摘录
    for k, m in log_meta.items():
        path = LOG_DIR / m["file"]
        m["bytes"] = os.path.getsize(path)
        m["lines"] = sum(1 for _ in open(path, "rb"))
        m["errorLines"] = scan_error_lines(path, coll["job_log_max_error_lines"])
        m["excerpt"] = make_excerpt(path, cfg)

    manifest = {("%s|%s" % k): {"file": m["file"], "bytes": m["bytes"],
                                "lines": m["lines"], "download": m["download"],
                                "truncated": m["truncated"],
                                "excerpt": m.get("excerpt"),
                                "rn": m["it"]["rn"], "jobName": m["it"]["jobName"]}
                for k, m in log_meta.items()}
    if (LOG_DIR / "manifest.json").exists():
        manifest = {**load_json(LOG_DIR / "manifest.json"), **manifest}
    save_json(LOG_DIR / "manifest.json", manifest)

    results = []
    for it in to_fetch:
        k = (it["rid"], it["jobId"])
        m = log_meta.get(k)
        if not m:
            results.append({
                "route": "log", "key": it["key"], "pipeline": it["pipeline"],
                "rn": it["rn"], "st": it["st"], "rid": it["rid"],
                "jobId": it["jobId"], "jobName": it["jobName"],
                "fallback": bool(it.get("fallback")),
                "stepErrMsgs": [s.get("errMsg") for s in it.get("steps") or []],
                "jobErrMsg": it.get("jobErrMsg"),
                "logFile": None, "errorLines": [],
            })
            continue
        results.append({
            "route": "log", "key": it["key"], "pipeline": it["pipeline"],
            "rn": it["rn"], "st": it["st"], "rid": it["rid"],
            "jobId": it["jobId"], "jobName": it["jobName"],
            "fallback": bool(it.get("fallback")),
            "stepErrMsgs": [s.get("errMsg") for s in it.get("steps") or []],
            "jobErrMsg": it.get("jobErrMsg"),
            "logFile": "job_logs/" + m["file"], "logBytes": m["bytes"],
            "logLines": m["lines"], "download": m["download"],
            "truncated": m["truncated"], "excerpt": m.get("excerpt"),
            "errorLines": m["errorLines"],
        })
    results.extend(cached_rows)
    save_json(OUT_DIR / "step_5_log_evidence.json", {"results": results, "errors": errors})

    n_exp = sum(1 for m in log_meta.values() if m["download"] == "export")
    n_pg = sum(1 for m in log_meta.values() if m["download"] == "paging")
    n_ep = sum(1 for m in log_meta.values() if m.get("excerpt"))
    total_bytes = sum(m["bytes"] for m in log_meta.values())
    print("log 任务 %d 项（回退 %d，新抓取 %d / 缓存复用 %d）| 导出 %d / 分页兜底 %d | 摘录 %d 份 | 新下载 %.1f MB | 采集错误 %d"
          % (len(results), sum(1 for r in results if r.get("fallback")),
             len(to_fetch), len(cached_rows), n_exp, n_pg, n_ep,
             total_bytes / 1048576, len(errors)))
    n_c = write_cache_from_outputs(cfg, OUT_DIR)
    print("增量缓存: 写入 %d 条（完整 %d）| 保留 %d 条 | 剔除区间外 %d 条" % n_c)
    print("-> out/job_logs/（全量日志落盘）+ out/step_5_log_evidence.json + out/cache/collection.json")


if __name__ == "__main__":
    main()
