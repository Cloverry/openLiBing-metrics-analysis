# -*- coding: utf-8 -*-
"""第 4 步【接口 T6】test 路线取证：测试结果过滤失败用例 → 下载用例日志 → 提取 ERROR/FAIL 消息。

接口: POST /gateway/openlibing-cicd/project/pipeline/test-report（state=['failed'] 数组）
回退标记: 失败用例为空（total=0，测试环境未拉起）的任务标记 empty，由第 5 步回退 T5 任务日志。
产物: out/step_4_test_evidence.json / out/step_4_case_urls.json / out/case_logs/
      / out/step_4_case_errors.json（每份日志提取的 ERROR/FAIL 消息）
重建: python steps/step_4_testreport.py --reuse-evidence
      复用既有 test 取证结果，只重跑「用例日志下载 + 解析」（不重新调 T6、不碰浏览器）
"""
import html as html_lib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import (OUT_DIR, chunks, load_config, load_json,
                          run_page_js, save_json)
from lib.cache import entry_key, load_cache

ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td>(.*?)</td>\s*<td class=['\"](\w+)['\"]>(\w+)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>",
    re.S)
MSG_RE = re.compile(r'<div class="msg-scroll">(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
CASE_ID_RE = re.compile(r"<p><strong>Case ID:</strong>\s*(.*?)</p>", re.S)
STATUS_RE = re.compile(r'<span class="status-badge">(\w+)</span>')
DUR_RE = re.compile(r"<p><strong>Duration:</strong>\s*(.*?)</p>")


def strip_tags(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = TAG_RE.sub("", s)
    return html_lib.unescape(s).strip()


def read_text(path, tail_bytes=None):
    """全文件读取（供行级扫描与行号定位）。tail_bytes 仅供超大文件的深挖兜底。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if tail_bytes and size > tail_bytes:
            f.seek(size - tail_bytes)
        return f.read().decode("utf-8", "ignore")


def line_no(text, pos):
    return text.count("\n", 0, pos) + 1


def deep_scan(text):
    """行级解析无果的兜底：Test FAILED / Traceback / AssertionError 摘要块。"""
    hits = []
    for m in re.finditer(r"Test FAILED:.*?</div>", text, re.S):
        t = strip_tags(m.group(0))
        if t and t not in hits:
            hits.append(t[:600])
    for m in re.finditer(r"Traceback:.*?</div>", text, re.S):
        t = strip_tags(m.group(0))
        if t and t not in hits:
            hits.append(t[:900])
    if not hits:
        for m in re.finditer(r"(AssertionError|RuntimeError|FAILED)[^\n<]{0,400}", text):
            t = strip_tags(m.group(0))
            if t and t not in hits:
                hits.append(t[:400])
    return hits[:4]


def parse_case_file(path, cfg):
    """全文件扫描（带行号）：agent 可凭行号回 case_logs/<file> 定位错误上下文。"""
    coll = cfg["collect"]
    text = read_text(path)
    cid, st, dur = CASE_ID_RE.search(text), STATUS_RE.search(text), DUR_RE.search(text)
    errs, deep_hits = [], []
    for m in ROW_RE.finditer(text):
        level = m.group(3).upper()
        if level not in ("ERROR", "FAIL"):
            continue
        raw = m.group(6)
        mm = MSG_RE.search(raw)
        msg = re.sub(r"[ \t]+", " ", strip_tags(mm.group(1) if mm else raw)).strip()
        if not msg or re.fullmatch(r"\d+", msg):
            continue
        if len(msg) > coll["max_error_msg_chars"]:
            msg = msg[:coll["max_error_msg_chars"]] + " ...[truncated]"
        errs.append({"time": m.group(1).strip(), "level": level,
                     "line": line_no(text, m.start()),
                     "loc": m.group(5).strip(), "msg": msg})
        if len(errs) >= coll["max_error_rows_per_case"]:
            break
    if not errs:
        deep_hits = deep_scan(text)
    return {
        "caseId": html_lib.unescape(cid.group(1)).strip() if cid else None,
        "status": st.group(1) if st else None,
        "duration": dur.group(1).strip() if dur else None,
        "fileBytes": os.path.getsize(path),
        "fileLines": text.count("\n") + 1,
        "errors": errs, "deepHits": deep_hits,
    }


def main():
    cfg = load_config()
    reuse = "--reuse-evidence" in sys.argv[1:]
    items = load_json(OUT_DIR / "step_3_params.json")["test"]
    new_items = [i for i in items if not i.get("cached")]
    cached_items = [i for i in items if i.get("cached")]
    print("== 第 4 步【T6】测试任务取证（%d 项：缓存复用 %d / 新抓取 %d）=="
          % (len(items), len(cached_items), len(new_items)))

    cache = load_cache(cfg)
    cached_results = []
    for it in cached_items:
        e = (cache.get("runs") or {}).get(entry_key(it["key"], it["rid"]))
        if not e:
            new_items.append(it)
            continue
        for r in e.get("testEvidence") or []:
            if r.get("jobId") == it.get("jobId"):
                cached_results.append(r)

    results, errors = list(cached_results), []
    if reuse:
        p4 = OUT_DIR / "step_4_test_evidence.json"
        if not p4.exists():
            print("!! --reuse-evidence 需要既有的 step_4_test_evidence.json，请先完整跑一次第 4 步")
            sys.exit(1)
        prev = load_json(p4)
        results = list(prev.get("results") or [])
        # 用例日志相关告警每次都重算，避免 --reuse-evidence 反复重跑时叠加历史旧告警
        errors = [e for e in (prev.get("errors") or [])
                  if not str(e.get("stage") or "").startswith("case_log")]
        new_items = []
        print("  --reuse-evidence：复用既有 test 取证 %d 项，只重建用例日志下载与解析" % len(results))
    batch_size = cfg["chrome"]["batch_size"]
    delay = cfg["chrome"]["inter_request_delay_ms"]
    if new_items:
        n_batches = (len(new_items) + batch_size - 1) // batch_size
        for i, batch in enumerate(chunks(new_items, batch_size)):
            print("  批次 %d/%d（%d 项）..." % (i + 1, n_batches, len(batch)))
            out = run_page_js("step_4_testreport.js",
                              {"items": batch, "pageSize": cfg["collect"]["t6_page_size"],
                               "delay": delay,
                               "conc": cfg["chrome"].get("request_concurrency", 4)},
                              "step_4_test_batch%d.json" % (i + 1), cfg, "T6")
            results.extend(out.get("results") or [])
            errors.extend(out.get("errors") or [])
    save_json(OUT_DIR / "step_4_test_evidence.json", {"results": results, "errors": errors})

    n_cases = sum(len(r.get("failedCases") or []) for r in results)
    n_empty = sum(1 for r in results if r.get("empty"))
    print("test 任务 %d 项 | 失败用例 %d 条 | 空测试结果 %d 项（第 5 步回退任务日志）| 采集错误 %d"
          % (len(results), n_cases, n_empty, len(errors)))

    # 用例日志：对**全部** test 任务产物里的 url 建索引（不只新抓取的）。
    # manifest 缺失/下载中断时也能凭 urls 重建映射，避免用例级错误被静默丢掉。
    urls, seen = [], set()
    for r in results:
        for c in r.get("failedCases") or []:
            u = c.get("resultDownloadUrl")
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
    save_json(OUT_DIR / "step_4_case_urls.json", {"urls": urls})

    case_dir = OUT_DIR / "case_logs"
    manifest = {}
    if case_dir.exists() and (case_dir / "manifest.json").exists():
        manifest = load_json(case_dir / "manifest.json")
    if urls:
        print("下载用例日志 %d 份（已在盘且映射有效的自动跳过）..." % len(urls))
        proc = subprocess.run(
            ["node", str(ROOT / "lib" / "download_case_logs.js"),
             str(OUT_DIR / "step_4_case_urls.json"), str(case_dir),
             str(cfg["collect"]["case_log_concurrency"]),
             str(cfg["collect"]["case_log_timeout_ms"])],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        print((proc.stdout or "").strip())
        mf = case_dir / "manifest.json"
        if mf.exists():
            manifest = load_json(mf)
        else:
            msg = ("用例日志 manifest.json 缺失（下载未收尾），%d 份日志的 url→文件映射无法回填"
                   % len(urls))
            errors.append({"stage": "case_log_download", "reason": msg})
            print("  !! " + msg)
        if proc.returncode not in (0, None):
            tail = (proc.stderr or "").strip().splitlines()
            msg = "用例日志下载进程退出码 %s%s" % (
                proc.returncode, ("：" + tail[-1][:200]) if tail else "")
            errors.append({"stage": "case_log_download", "reason": msg})
            print("  !! " + msg)

    # case_errors：先按 urls 逐条用 manifest + 本地 HTML 解析（幂等、可重建），
    # 再补缓存里既有但当前 urls 未覆盖的条目（缓存条目按运行存整段，按 URL 去重）
    case_errors, seen_runs, seen_urls = [], set(), set()
    for url in urls:
        m = manifest.get(url) or {}
        entry = {"url": url, "file": m.get("file"),
                 "localLog": "case_logs/" + m["file"] if m.get("file") else None,
                 "downloadStatus": m.get("status")}
        p = case_dir / (m.get("file") or "")
        if m.get("status") == "ok" and p.exists():
            entry.update(parse_case_file(p, cfg))
        seen_urls.add(url)
        case_errors.append(entry)
    for it in cached_items:
        rk = entry_key(it["key"], it["rid"])
        if rk in seen_runs:
            continue
        seen_runs.add(rk)
        e = (cache.get("runs") or {}).get(rk)
        if not e:
            continue
        for ce in e.get("caseErrors") or []:
            if ce.get("url") in seen_urls:
                continue
            seen_urls.add(ce.get("url"))
            case_errors.append(ce)

    bad_logs = [e for e in case_errors if e.get("downloadStatus") != "ok"]
    if bad_logs:
        msg = ("%d/%d 份用例日志未成功下载或缺失，用例级 ERROR 行无法提取（首例 %s）"
               % (len(bad_logs), len(case_errors), (bad_logs[0].get("url") or "")[:80]))
        errors.append({"stage": "case_log_parse", "reason": msg})
        print("  !! " + msg)
    save_json(OUT_DIR / "step_4_case_errors.json", case_errors)
    save_json(OUT_DIR / "step_4_test_evidence.json", {"results": results, "errors": errors})

    ok_logs = [e for e in case_errors if e.get("downloadStatus") == "ok"]
    total_msgs = sum(len(e.get("errors") or []) for e in ok_logs)
    total_deep = sum(len(e.get("deepHits") or []) for e in ok_logs)
    print("用例日志 %d 份（成功解析 %d 份）| 行级 ERROR/FAIL %d 条 | 深挖摘要 %d 段"
          % (len(case_errors), len(ok_logs), total_msgs, total_deep))
    print("-> out/step_4_test_evidence.json / out/step_4_case_errors.json")


if __name__ == "__main__":
    main()
