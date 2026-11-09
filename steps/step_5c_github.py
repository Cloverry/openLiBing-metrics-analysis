# -*- coding: utf-8 -*-
"""第 5c 步【GitHub】platform=github 流水线失败运行取证（选择性日志采集）。

输入: out/step_2_failed_runs.json 中 platform=github 的 failedRuns
      （N2 pipelineRunId 即 GitHub Actions run id，workflowRunUrl 同源一致，零转换）
链路: GET /repos/{repo}/actions/runs/{id}/jobs   jobs 元数据（每运行 1 次，>100 自动翻页）
      → 决策树: 目标 job（失败优先，否则取消）1~5 个 → per-job 精取；>5 个 → 整包 zip
        （zip 一次下载全部 job 主日志 + system.txt，带宽远高于逐 job blob）
      → GET /repos/{repo}/actions/jobs/{job_id}/logs   302 → blob 纯文本
        GET /repos/{repo}/actions/runs/{run_id}/logs    302 → blob zip
      → zip 主日志条目名与 API job 名的对应规则（实测）: 去掉 .txt 后
        zip_name == api_name.replace('/', '_')，且 '"'、'\n'、'\r'、'\t' 移除
        （GitHub 对 matrix 参数名在 ~95 字符处 '...' 截断，API 与 zip 截断点一致）
      → zip 缺失目标 job 时逐 job 回退 per-job 端点（zip 不保证包含全部失败 job 日志）
下载: blob 分块读（1MB/块，单块 60s 超时防慢速卡死）+ Accept-Encoding gzip + 512MB 上限
      实测 per-job blob 域（productionresultssa*.blob）仅 ~7KB/s，zip 域 ~110KB/s，
      故目标 job >5 一律走 zip；分块读保证慢速场景下可观测、可超时重试
缺失: 取消 job 日志 GitHub 侧可能未留存（302 后 blob 404 BlobNotFound）→ 记 404_not_retained，
      报告标注「证据不完整」；其余错误重试 3 次后记录，ghComplete=false 下次重抓
测试: 测试 job（job 名 single-node/multi-node/double-node 开头）额外解析测试结果定位失败用例：
      accuracy 任务逐模型失败（Test failed for model + pytest FAILED/汇总）、aisbench 失败用例
      （case 明细 + reason）、Benchmark failed、FAIL_TAG ✗；剥离 ANSI 颜色码与时间戳、
      跳过 ##[group] 脚本回显块与 echo 回显，挂任务 testResults 供报告用例级定位
校准: 缓存复用前比对运行状态快照 runStatus（status/conclusion/updated_at/run_attempt，
      GET /repos/{repo}/actions/runs/{rid}），重试/重跑导致变更（或旧缓存无快照）→ 清目录重取；
      openlibing 侧 N2 状态校准在第 2 步完成（cached 标志）
缓存: 与主流程同 (key, rid) 粒度；entry_valid 命中的运行直接复用上次证据（不重复调 GitHub API）
鉴权: config.github.token（PAT），API 头带 token，blob 直链不带（302 短时效签名 URL）
产物: out/github_logs/gh_<rid>/{jobs.json, *.txt, _name_map.json, _done.json}
      out/step_5c_github_evidence.json（results 每运行一行 + errors）
"""
import gzip
import hashlib
import io
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import OUT_DIR, load_config, load_json, save_json
from lib.cache import (entry_key, entry_valid, load_cache,
                       write_cache_from_outputs)

GH_DIR = OUT_DIR / "github_logs"
INVALID_RE = re.compile(r'[<>:"/\\|?*]')
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*")
ERR_PAT = re.compile(
    r"^##\[error\]"
    r"|Traceback \(most recent"
    r"|fatal error:"
    r"|ImportError|ModuleNotFoundError"
    r"|EngineDeadError"
    r"|The following aisbench case failed"
    r"|Some errors happened to Aisbench runtime"
    r"|FAIL_TAG_\S+\.yaml.*✗"
    r"|\bERROR \[\w"
    r"|\bERROR root:"
    r"|No such file or directory"
    r"|exit code: [1-9]"
    r"|Some tests failed")
BLOB_TIMEOUT_S = 60
BLOB_MAX_BYTES = 512 * (1 << 20)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TEST_JOB_RE = re.compile(r"^(single-node|multi-node|double-node)", re.IGNORECASE)
DONE_V = 2


def sanitize(name, limit=80):
    """落盘文件名净化：非法字符替换、空白折叠、超长截断 + 哈希防撞（Windows MAX_PATH）。"""
    s = INVALID_RE.sub("_", str(name or "job")).strip(". ")
    s = re.sub(r"\s+", " ", s)
    if len(s) > limit:
        s = s[:limit] + "~" + hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:6]
    return s or "job"


def zip_norm(api_name):
    """API job 名 → zip 主日志条目名（去 .txt）：/ → _，引号与换行制表符移除。"""
    return (api_name.replace("/", "_").replace('"', "")
            .replace("\n", "").replace("\r", "").replace("\t", ""))


def parse_repo(gh_repo, product, gh_cfg):
    """repo 归属解析：优先 N2 repoUrl（owner/repo），回退 config.github.repos[产品]。"""
    m = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$", str(gh_repo or ""))
    if m:
        return m.group(1)
    return (gh_cfg.get("repos") or {}).get(product)


class _NotRetained(Exception):
    """GitHub 侧日志未留存（API 404 或 blob BlobNotFound），确定性标记而非错误。"""


class GitHubClient:
    def __init__(self, gh_cfg):
        self.base = gh_cfg.get("api_base") or "https://api.github.com"
        self.headers = {"Authorization": "Bearer %s" % gh_cfg["token"],
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "openlibing-nightly-rca"}
        self.stats = {"calls": 0, "bytes": 0, "jobs": 0, "joblog": 0,
                      "zip": 0, "http404": 0}
        self._stats_lock = threading.Lock()

    def bump(self, key, n=1):
        """并发采集下线程安全的统计累加。"""
        with self._stats_lock:
            self.stats[key] += n

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    def __enter__(self):
        self._api = urllib.request.build_opener(self._NoRedirect())
        return self

    def __exit__(self, *a):
        return False

    def _open_api(self, url, timeout):
        return self._api.open(urllib.request.Request(url, headers=self.headers),
                              timeout=timeout)

    def api_get_json(self, url, tries=3):
        """API GET（200 JSON）；403/429 退避重试；404 抛 _NotRetained。"""
        last = None
        for i in range(tries):
            try:
                self.bump("calls")
                return json.loads(self._open_api(url, 60).read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise _NotRetained("api_404")
                if e.code in (403, 429):
                    last = e
                    time.sleep(5 * (i + 1))
                    continue
                raise
            except Exception as e:
                last = e
                time.sleep(2 * (i + 1))
        raise last

    def jobs_of_run(self, repo, rid):
        """run 的全部 job（>100 自动翻页）。"""
        out, page = [], 1
        while True:
            d = self.api_get_json("%s/repos/%s/actions/runs/%s/jobs"
                                  "?per_page=100&page=%d" % (self.base, repo, rid, page))
            batch = d.get("jobs") or []
            out.extend(batch)
            total = d.get("total_count") or len(out)
            if len(out) >= total or not batch:
                break
            page += 1
            if page > 20:
                break
        self.bump("jobs")
        return out

    def run_meta(self, repo, rid):
        """run 状态快照（缓存复用校准依据：重试/重跑后 conclusion/updated_at/run_attempt 变化）。"""
        d = self.api_get_json("%s/repos/%s/actions/runs/%s" % (self.base, repo, rid))
        return {"status": d.get("status"), "conclusion": d.get("conclusion"),
                "updatedAt": d.get("updated_at"), "attempt": d.get("run_attempt")}

    def api_location(self, url, tries=3):
        """API GET 期望 302，返回 Location（日志下载端点）。"""
        last = None
        for i in range(tries):
            try:
                self.bump("calls")
                self._open_api(url, 30)
                raise RuntimeError("expected 302, got 200: %s" % url[:80])
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 303, 307):
                    return e.headers["Location"]
                if e.code == 404:
                    raise _NotRetained("api_404")
                if e.code in (403, 429):
                    last = e
                    time.sleep(5 * (i + 1))
                    continue
                raise
            except _NotRetained:
                raise
            except Exception as e:
                last = e
                time.sleep(2 * (i + 1))
        raise last

    def blob_get(self, url, on_progress=None, timeout=BLOB_TIMEOUT_S, tries=3):
        """blob 直链分块下载（不带 token）；带 gzip 解压与 512MB 上限，单块超时防慢速卡死。

        per-job blob 域实测 ~7KB/s：整体 read() 会在大文件上长时间无磁盘动静，
        分块读保证慢速时仍可观测，单块 60s 无数据即超时重试（3 次）后报错。
        """
        last = None
        for i in range(tries):
            buf = io.BytesIO()
            total = 0
            try:
                resp = urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "openlibing-nightly-rca",
                                  "Accept-Encoding": "gzip"}), timeout=timeout)
                enc = (resp.headers.get("Content-Encoding") or "").lower()
                raw = resp if "gzip" not in enc else gzip.GzipFile(fileobj=resp)
                while True:
                    chunk = raw.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    buf.write(chunk)
                    if on_progress and total % (5 << 20) < (1 << 20):
                        on_progress(total)
                    if total > BLOB_MAX_BYTES:
                        raise RuntimeError("job 日志超过 %dMB 上限，中止下载" % (BLOB_MAX_BYTES >> 20))
                self.bump("bytes", total)
                return buf.getvalue()
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    self.bump("http404")
                    raise _NotRetained("blob_%d" % e.code)
                last = e
            except _NotRetained:
                raise
            except Exception as e:
                last = e
            if i < tries - 1:
                time.sleep(3 * (i + 1))
        raise last


def scan_error_lines(path, max_lines):
    """错误行扫描：剥离时间戳、跳过 ##[group] 脚本回显块（假命中来源），超限保头 10 + 尾部。"""
    hits, in_group = [], False
    with open(path, "rb") as f:
        for ln, raw in enumerate(f, 1):
            text = TS_RE.sub("", raw.decode("utf-8", "ignore").rstrip("\r\n"))
            if text.startswith("##[group]"):
                in_group = True
                continue
            if text.startswith("##[endgroup]"):
                in_group = False
                continue
            if in_group:
                continue
            if ERR_PAT.search(text):
                hits.append((ln, text.strip()[:400]))
    if len(hits) > max_lines:
        hits = hits[:10] + hits[-(max_lines - 10):]
    return ["L%d: %s" % (ln, t) for ln, t in hits]


def parse_test_results(fpath):
    """测试 job 日志的测试结果定位：模型级失败（accuracy 任务）、aisbench 失败用例、
    Benchmark failed、FAIL_TAG ✗、pytest FAILED 与汇总行，供根因定位到具体用例。
    与错误行扫描同规格：剥离时间戳与 ANSI 颜色码，跳过 ##[group] 回显块与 echo 回显行；
    各类条目按内容去重并设上限，全空返回 None。"""
    res = {"failedModels": [], "aisbenchFailed": [], "benchmarkFailed": [],
           "failedTagTests": [], "pytestFailed": [], "pytestSummaries": []}
    seen = {k: set() for k in res}
    cur_py, cur_sum, in_group = None, None, False
    with open(fpath, "rb") as f:
        for ln, raw in enumerate(f, 1):
            text = ANSI_RE.sub("", TS_RE.sub("", raw.decode("utf-8", "ignore"))).strip()
            if text.startswith("##[group]"):
                in_group = True
                continue
            if text.startswith("##[endgroup]"):
                in_group = False
                continue
            if in_group or 'echo "' in text or "$model" in text:
                continue
            m = re.match(r"^FAILED (\S+::\S+) - (.+)$", text)
            if m:
                cur_py = {"line": ln, "test": m.group(1), "error": m.group(2)[:300]}
                key = (m.group(1), m.group(2)[:80])
                if key not in seen["pytestFailed"] and len(res["pytestFailed"]) < 40:
                    seen["pytestFailed"].add(key)
                    res["pytestFailed"].append(cur_py)
                continue
            m = re.match(r"^=+ ([1-9]\d* failed[^=]*) =+$", text)
            if m:
                cur_sum = m.group(1).strip()[:120]
                if cur_sum not in seen["pytestSummaries"] and len(res["pytestSummaries"]) < 20:
                    seen["pytestSummaries"].add(cur_sum)
                    res["pytestSummaries"].append({"line": ln, "summary": cur_sum})
                continue
            m = re.match(r"^Test failed for model: (.+)$", text)
            if m:
                if len(res["failedModels"]) < 40:
                    res["failedModels"].append(
                        {"line": ln, "model": m.group(1).strip()[:120],
                         "pytest": cur_py, "summary": cur_sum})
                cur_py = cur_sum = None
                continue
            m = re.search(r"The following aisbench case failed: (\{.*?\}), reason is (.+)$", text)
            if m and len(res["aisbenchFailed"]) < 40:
                case, reason = m.group(1)[:300], m.group(2)[:200]
                key = (case[:100], reason[:60])
                if key not in seen["aisbenchFailed"]:
                    seen["aisbenchFailed"].add(key)
                    res["aisbenchFailed"].append({"line": ln, "case": case, "reason": reason})
                continue
            m = re.search(r"Benchmark failed: (\S+), reason: (.+)$", text)
            if m and len(res["benchmarkFailed"]) < 40:
                key = (m.group(1), m.group(2)[:60])
                if key not in seen["benchmarkFailed"]:
                    seen["benchmarkFailed"].add(key)
                    res["benchmarkFailed"].append(
                        {"line": ln, "name": m.group(1), "reason": m.group(2)[:200]})
                continue
            m = re.search(r"(FAIL_TAG_\S+\.yaml) ✗ (.*)$", text)
            if m and len(res["failedTagTests"]) < 40:
                if m.group(1) not in seen["failedTagTests"]:
                    seen["failedTagTests"].add(m.group(1))
                    res["failedTagTests"].append(
                        {"line": ln, "tag": m.group(1), "msg": m.group(2)[:120]})
    return res if any(res.values()) else None


def extract_zip(rdir, zbytes):
    """整包 zip 逐条目提取（文件名截断+哈希），返回 {zip 原始条目名: 落盘文件名}。

    条目规则（实测）: 根级 '<seq>_<job 名>.txt' 为该 job 全量主日志；
    '<job 名>/system.txt' 为系统日志（runner 元数据/调度过程，取消时间线取证用）。
    """
    mapping = {}
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        used = set()
        for n in z.namelist():
            if n.endswith("/"):
                continue
            m = re.match(r"^(\d+)_(.+)\.txt$", n)
            ms = re.match(r"^(.+)/system\.txt$", n)
            if m:
                out = "%s_%s.txt" % (m.group(1), sanitize(m.group(2)))
            elif ms:
                out = "sys_%s.txt" % sanitize(ms.group(1))
            else:
                out = sanitize(n) + ".txt"
            while out in used:
                out = "z" + out
            used.add(out)
            (rdir / out).write_bytes(z.read(n))
            mapping[n] = out
    return mapping


def jid_of(j):
    try:
        return int(j["id"])
    except (TypeError, ValueError):
        return j.get("id")


def _job_row(j, rel_path, fpath, coll):
    """目标 job 证据行：logFile 相对 out/ 路径 + 行数 + errorLines（与 step5 格式一致）；
    测试 job（single/multi/double-node 开头）附带 testResults 用例级测试结果定位。"""
    try:
        n_lines = sum(1 for _ in open(fpath, "rb"))
    except OSError:
        n_lines = 0
    row = {
        "name": j["name"], "id": jid_of(j), "conclusion": j.get("conclusion"),
        "startedAt": j.get("started_at"), "completedAt": j.get("completed_at"),
        "logFile": rel_path, "logBytes": fpath.stat().st_size, "logLines": n_lines,
        "errorLines": scan_error_lines(fpath, coll.get("job_log_max_error_lines", 200)),
        "githubSteps": [{"name": s["name"], "conclusion": s["conclusion"]}
                        for s in j.get("steps") or []
                        if s.get("conclusion") not in ("success", "skipped", None)],
    }
    if TEST_JOB_RE.match(j["name"]):
        row["isTestJob"] = True
        row["testResults"] = parse_test_results(fpath)
    return row


def collect_run(client, repo, run, coll):
    """采集单个 github 运行；_done.json（_v 匹配）存在直接复用（断点续跑幂等）。"""
    rid = str(run["rid"])
    rdir = GH_DIR / ("gh_%s" % rid)
    done_path = rdir / "_done.json"
    if done_path.exists():
        try:
            row = json.loads(done_path.read_text(encoding="utf-8"))
        except Exception:
            row = None
        if row and row.get("_v") == DONE_V:
            return row
        shutil.rmtree(rdir, ignore_errors=True)  # 旧格式断点产物，重采
    rdir.mkdir(parents=True, exist_ok=True)

    # 0) 运行状态快照（缓存复用校准依据：重试/重跑后 conclusion/updated_at/run_attempt 变化）
    try:
        run_status = client.run_meta(repo, rid)
    except Exception:
        run_status = None

    # 1) jobs 元数据（分流与结论的权威来源；>100 自动翻页）
    jf = rdir / "jobs.json"
    if jf.exists():
        jlist = json.loads(jf.read_text(encoding="utf-8"))
    else:
        jlist = client.jobs_of_run(repo, rid)
        jf.write_text(json.dumps(jlist, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [j for j in jlist if j.get("conclusion") == "failure"]
    cancelled = [j for j in jlist if j.get("conclusion") == "cancelled"]
    targets = failed or cancelled

    if not targets:
        mode, why = "none", "全部 job 成功/跳过"
    elif len(targets) <= 5:
        mode, why = "perjob", "%s job %d 个（≤5，逐 job 精取）" % (
            "失败" if failed else "取消", len(targets))
    else:
        mode, why = "zip", "%s job %d 个（>5，整包一次下载更省时）" % (
            "失败" if failed else "取消", len(targets))

    # 2) zip（>5 目标或需 system.txt 的取消场景；提取后按名匹配目标 job 主日志）
    job_rows, missing, system_logs, mapping = [], [], [], {}
    if mode == "zip":
        zpath = rdir / "logs.zip"
        try:
            if not zpath.exists():
                loc = client.api_location(
                    "%s/repos/%s/actions/runs/%s/logs" % (client.base, repo, rid))
                zpath.write_bytes(client.blob_get(loc))
                client.bump("zip")
            mapping = extract_zip(rdir, zpath.read_bytes())
            (rdir / "_name_map.json").write_text(
                json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
            system_logs = ["github_logs/%s/%s" % (rdir.name, f)
                           for o, f in mapping.items() if f.startswith("sys_")]
        except _NotRetained:
            missing.append({"job": "*", "status": "404_not_retained",
                            "detail": "run 日志整包未留存"})

    zip_main_by_norm = {}
    for o, f in mapping.items():
        m = re.match(r"^(\d+)_(.+)\.txt$", o)
        if m:
            zip_main_by_norm[m.group(2)] = f

    # 3) 逐目标取证：zip 命中 → 直接用 zip 主日志；否则 per-job 精取（zip 不保证全）
    for j in targets:
        zf = zip_main_by_norm.get(zip_norm(j["name"])) if mode == "zip" else None
        if zf:
            job_rows.append(_job_row(j, "github_logs/%s/%s" % (rdir.name, zf),
                                     rdir / zf, coll))
            continue
        jid = jid_of(j)
        fname = "%s_%s.txt" % (jid, sanitize(j["name"]))
        fpath = rdir / fname
        if not fpath.exists():
            try:
                loc = client.api_location(
                    "%s/repos/%s/actions/jobs/%d/logs" % (client.base, repo, jid))
                t0 = time.time()
                body = client.blob_get(
                    loc, on_progress=lambda n: print("      … [%s] %s %.1f MB @ %.0fs"
                                                     % (rid, j["name"][:40], n / 1048576,
                                                        time.time() - t0)))
                fpath.write_bytes(body)
                client.bump("joblog")
            except _NotRetained:
                missing.append({"job": j["name"], "id": jid,
                                "status": "404_not_retained"})
                continue
        job_rows.append(_job_row(j, "github_logs/%s/%s" % (
            rdir.name, fname), fpath, coll))

    # 4) 完整性：目标 job 全部有日志或确定性 404 才算完整（否则下次重抓）
    n_ok = sum(1 for r in job_rows if r["logFile"])
    has_hard_missing = any(m for m in missing
                           if not str(m.get("status")).startswith("404"))
    gh_complete = (len(targets) == 0) or (
        n_ok + len([m for m in missing if str(m.get("status")).startswith("404")])
        >= len(targets) and not has_hard_missing)

    row = {
        "route": "github", "key": run["key"], "rn": run["rn"], "rid": rid,
        "st": run.get("start"), "runId": int(rid), "repo": repo,
        "displayTitle": run.get("ghTitle"), "event": run.get("ghEvent"),
        "status": run["status"], "duration": run.get("ghDuration"),
        "runStatus": run_status, "_v": DONE_V,
        "mode": mode, "why": why, "totalJobs": len(jlist),
        "failedJobs": [{"name": j["name"], "id": jid_of(j),
                        "steps": [{"name": s["name"], "conclusion": s["conclusion"]}
                                  for s in j.get("steps") or []
                                  if s.get("conclusion") not in ("success", "skipped", None)]}
                       for j in failed],
        "cancelledJobs": [{"name": j["name"], "id": jid_of(j)} for j in cancelled],
        "jobs": job_rows, "missing": missing, "systemLogs": system_logs,
        "ghComplete": gh_complete,
    }
    done_path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    return row


def main():
    cfg = load_config()
    coll = cfg.get("collect") or {}
    gh_cfg = cfg.get("github") or {}
    runs_info = load_json(OUT_DIR / "step_2_failed_runs.json")
    s1b = load_json(OUT_DIR / "step_1_bad_pipelines.json")
    pmeta = {b["key"]: b for b in s1b["pipelines"]}

    gh_runs = []
    for key, info in runs_info.items():
        if key == "_meta":
            continue
        is_gh = (pmeta.get(key, {}).get("platform") or "").lower() == "github"
        for fr in info.get("failedRuns") or []:
            if is_gh or fr.get("platform") == "github":
                gh_runs.append({**fr, "key": key,
                                "product": pmeta[key]["product"]})
    print("== 第 5c 步【GitHub】github 平台运行取证（%d 次）==" % len(gh_runs))
    if not gh_runs:
        save_json(OUT_DIR / "step_5c_github_evidence.json",
                  {"results": [], "errors": []})
        print("无 github 平台运行，跳过")
        return

    if not (gh_cfg.get("token") or "").strip():
        print("!! config.github.token 未配置，无法采集 %d 个 github 运行的日志" % len(gh_runs))
        sys.exit(1)

    cache = load_cache(cfg)
    results, errors, n_reuse, n_recal = [], [], 0, 0
    to_fetch, to_verify = [], []
    for run in gh_runs:
        e = (cache.get("runs") or {}).get(entry_key(run["key"], run["rid"]))
        if run.get("cached") and entry_valid(e, OUT_DIR) and e.get("github"):
            to_verify.append((run, e["github"]))
        else:
            to_fetch.append(run)

    print("缓存复用候选 %d / 新抓取 %d" % (len(to_verify), len(to_fetch)))
    if to_verify or to_fetch:
        gh_conc = max(1, int(gh_cfg.get("concurrency", 6)))
        with GitHubClient(gh_cfg) as client:
            # 状态校准：缓存条目与运行状态快照比对（conclusion/updated_at/run_attempt），
            # 变更（重试/重跑）或旧缓存无快照 → 清目录重取；校准接口失败则复用上次证据（下轮再校准）。
            # 校准快照并发查询，比对决策仍在主线程按原序执行
            def _cal(pair):
                run, row = pair
                repo = parse_repo(run.get("ghRepo"), run["product"], gh_cfg)
                if not repo:
                    print("  run %s (%s) repo 无法解析（无法校准），复用上次证据"
                          % (run["rid"], run["rn"]))
                    return None
                try:
                    return client.run_meta(repo, run["rid"])
                except Exception as ex:
                    print("  run %s 状态校准接口失败（复用上次证据）: %s"
                          % (run["rid"], repr(ex)[:80]))
                    return None

            if to_verify:
                with ThreadPoolExecutor(max_workers=gh_conc) as ex:
                    cal = list(ex.map(_cal, to_verify))
                for (run, row), fresh in zip(to_verify, cal):
                    if row.get("runStatus") and fresh and row["runStatus"] == fresh:
                        results.append({**row, "cached": True})
                        n_reuse += 1
                    elif fresh is None:
                        # repo 无法解析或校准接口失败 → 无法比对，复用上次证据（下轮再校准）
                        results.append({**row, "cached": True})
                        n_reuse += 1
                    else:
                        n_recal += 1
                        rdir = GH_DIR / ("gh_%s" % run["rid"])
                        if rdir.exists():
                            shutil.rmtree(rdir, ignore_errors=True)
                        to_fetch.append(run)
                        print("  run %s 运行状态已变更（重试/重跑）或缓存无状态快照，重新获取" % run["rid"])

            if to_fetch:
                # 新抓取按运行粒度并发：各运行的 jobs/zip/逐 job blob 互不重叠，
                # 慢速 per-job blob 域（~7KB/s）是耗时主体，并发接近线性压缩采集时长
                def _collect(run):
                    repo = parse_repo(run.get("ghRepo"), run["product"], gh_cfg)
                    if not repo:
                        return ("norepo", run, None)
                    try:
                        return ("ok", run, collect_run(client, repo, run, coll))
                    except Exception as e:
                        return ("fail", run, e)

                print("  并发采集 %d 个运行（并发 %d）..." % (len(to_fetch), gh_conc))
                with ThreadPoolExecutor(max_workers=gh_conc) as ex:
                    for kind, run, obj in ex.map(_collect, to_fetch):
                        if kind == "norepo":
                            errors.append({"key": run["key"], "rn": run["rn"],
                                           "rid": run["rid"],
                                           "err": "repo 无法解析（N2 repoUrl 与 config.github.repos 均缺失）"})
                            print("  run %s (%s) repo 无法解析，跳过" % (run["rid"], run["rn"]))
                        elif kind == "ok":
                            row = obj
                            row["cached"] = False
                            results.append(row)
                            n_log = sum(1 for r in row["jobs"] if r["logFile"])
                            print("  run %s（%s）[%s] %s | 日志 %d/%d 缺失 %d | 错误行 %d"
                                  % (run["rid"], (run.get("ghTitle") or "")[:26], row["mode"],
                                     row["why"], n_log, n_log + len(row["missing"]),
                                     len(row["missing"]),
                                     sum(len(r["errorLines"]) for r in row["jobs"])))
                        else:
                            errors.append({"key": run["key"], "rn": run["rn"], "rid": run["rid"],
                                           "err": "collect: %s" % repr(obj)[:200]})
                            print("  run %s 采集失败: %s" % (run["rid"], repr(obj)[:120]))
        stats = client.stats
        print("GitHub API: %d 次调用（状态校准+jobs）| 下载 %.1f MB | blob 404（未留存）%d"
              % (stats["calls"], stats["bytes"] / 1048576, stats["http404"]))

    results.sort(key=lambda r: (r["key"], str(r["rn"])))
    save_json(OUT_DIR / "step_5c_github_evidence.json",
              {"results": results, "errors": errors})
    n_jobs = sum(len(r["jobs"]) for r in results)
    n_missing = sum(len(r["missing"]) for r in results)
    n_errlines = sum(len(j["errorLines"]) for r in results for j in r["jobs"])
    n_testjobs = sum(1 for r in results for j in r["jobs"] if j.get("isTestJob"))
    n_casefails = sum(sum(len(v) for v in (j.get("testResults") or {}).values())
                      for r in results for j in r["jobs"])
    print("github 运行 %d 次（缓存 %d / 状态校准失效重取 %d）| 失败 job 日志 %d 份 | 404 未留存 %d 个 | "
          "错误行 %d 条 | 测试 job %d 个（用例级失败定位 %d 项）| 采集错误 %d"
          % (len(results), n_reuse, n_recal, n_jobs, n_missing, n_errlines,
             n_testjobs, n_casefails, len(errors)))
    n_c = write_cache_from_outputs(cfg, OUT_DIR)
    print("增量缓存: 写入 %d 条（完整 %d）| 保留 %d 条 | 剔除区间外 %d 条" % n_c)
    print("-> out/github_logs/ + out/step_5c_github_evidence.json")


if __name__ == "__main__":
    main()
