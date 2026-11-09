# -*- coding: utf-8 -*-
"""第 6 步 证据合并 → 逐条执行记录（一条执行 = 一条记录，不做汇总报告）。

记录来源 = 第 2 步 runHistory（区间内**全部运行**，成功 + 失败），保证「每天的记录都完整」：
  成功运行：只落轻量记录（状态/起止时间/编译与可用口径），verdict.category=success，
            analysis.status=na，不带证据，不进入第 7 步根因分析；
  失败/取消运行：走第 3~5c 步证据链，附定性 + 置信度 + 证据文件。
  版本可用度 / 编译成功率一律按这批全量记录重算（第 7 步覆盖归档比率）。

AI 分析范围（analysis.status）：只分析非 1 指标。
  该流水线当日两个指标都为 1（达标）时，失败运行也只落 na，不做根因分析（证据仍保留）；
  vllm-ascend 的指标在第 2 步已按 scheduled 运行重算（不看板值是否为 1、只统计含 scheduled 的运行），
  因此达标判定用的就是重算值，无需额外分支。

输入: out/work/ 下第 1~5c 步产物（step_1_bad_pipelines / step_2_failed_runs / step_3_params /
      step_4_test_evidence / step_4_case_errors / step_5_log_evidence / step_5c_github_evidence）
输出: out/work/step_6_records.json  {_doc, meta, coverage, notes, records[]}

记录字段（与展示层一一对应，方便逐条核对，防止口径被挑战）:
  recordId      记录唯一键 = <流水线 key>#<构建号>
  date          归属日期 = 执行结束时间（pipelineRunEndtime）的日期
  component     开源组件名（= 产品名，归档第一层目录）
  pipeline      流水线名（归档第二层目录）
  buildNumber   构建编号（pipelineRunNumber），报告里的「构建」列
  tasks[]       任务与证据（test: 失败用例/错误消息；log: 全量日志/错误行；github: job 证据）
  cases[]       失败用例（跨任务展平，便于直接看用例级证据）
  errorMessages[] 错误信息（跨任务展平，带来源文件与行号）
  evidence      证据清单（落盘文件 + 页面链接）
  verdict       定性（规则判定）+ 置信度（含加分依据，可复核）
  suggestions   建议（一律留空，由第 7 步 agent 逐条补，不做任何结论复用）
  analysis      第 7 步 agent 的分析结论（rootCause/advice/cites 写回位）

定性/置信度口径（全部由证据推导，可逐条复核）:
  success          执行成功：运行状态非失败（成功运行的记录不取证，置信度固定 1.00）
  compile_failure  编译失败：Build* 任务失败，且子任务失败或日志命中编译报错特征
  test_failure     用例失败：test 任务取到失败用例且有用例级 ERROR/FAIL 消息
  env_not_ready    用例未拉起：test 任务测试结果为空（已回退整 job 日志）
  job_failure      任务失败：日志取证任务命中错误行（非编译特征）
  cancelled        运行或任务被取消，无失败证据可取
  evidence_missing 证据缺失：既无日志也无用例证据（如 github 侧 404 未留存）
  unknown          证据不足以定性，需人工查看
置信度 = 0.30 基线 + 证据加分（日志落盘 +0.25 / 错误行 +0.15 / 用例消息 +0.20 /
        子任务日志 +0.10）− 证据缺口扣分（每个缺口 −0.20），区间 [0.10, 0.95]。
"""
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.cdp_exec import OUT_DIR, load_json, save_json
from lib.paths import DATE_FIELD, DATE_RULE, date_of, doc, safe_name

# 编译报错特征：命中即认定「编译失败」而非普通任务失败
COMPILE_HINT = re.compile(
    r"(error:|fatal error|undefined reference|compilation (?:failed|terminated)|"
    r"ninja: build stopped|make(?:\[\d+\])?: \*\*\*|CMake Error|collect2|"
    r"nvcc (?:fatal|error)|clang(?:\+\+)?: error|ld: )", re.I)

# pytest 只留一行汇总日志时的特征：「Test FAILED due to ERROR/FAIL logs: <testcase>::<用例>」。
# 这类行只说明「哪些用例失败」，不含断言原因，真因在测试报告的 description / deepHits 里。
SUMMARY_MSG_RE = re.compile(r"^\s*Test FAILED due to\b", re.I)

CATEGORY_TEXT = {
    "success": "执行成功",
    "compile_failure": "编译失败",
    "test_failure": "用例失败",
    "env_not_ready": "用例未拉起（测试环境异常）",
    "job_failure": "任务失败",
    "cancelled": "运行被取消",
    "evidence_missing": "证据缺失",
    "unknown": "证据不足待人工确认",
}


def load_opt(name):
    p = OUT_DIR / name
    return load_json(p) if p.exists() else None


def _is_one(v):
    """指标值是否等于 1（取不到 / 非数值一律视为「非 1」）。"""
    try:
        return float(v) == 1.0
    except (TypeError, ValueError):
        return False


def lights_ok(va, bs):
    """两个指标都为 1 才算达标。只分析非 1 指标 = 达标日不需要 AI 根因。"""
    return _is_one(va) and _is_one(bs)


def iso_of(s):
    """'yyyy-mm-dd HH:MM:SS' -> ISO（归档内时间统一为 ISO 串）。"""
    s = str(s or "").strip()
    return s.replace(" ", "T") if s else None


def text_of(obj):
    """把字符串/字典列表压平成一段可正则匹配的文本。"""
    out = []
    for x in obj or []:
        if isinstance(x, dict):
            for k in ("errorMessage", "msg", "text", "message"):
                if x.get(k):
                    out.append(str(x[k]))
        elif x:
            out.append(str(x))
    return "\n".join(out)


def task_corpus(t):
    """单任务的全部错误文本（用于编译特征判定与指纹）。"""
    parts = [text_of(t.get("stepErrMsgs")), t.get("jobErrMsg") or "",
             text_of(t.get("errorLines"))]
    for c in t.get("cases") or []:
        parts.append(text_of(c.get("errorMessages")))
        parts.append(text_of(c.get("deepHits")))
    for st in t.get("subTasks") or []:
        parts.append(text_of(st.get("errorLines")))
        parts.append(st.get("jobErrMsg") or "")
    return "\n".join(p for p in parts if p)


def _err_rank(level, is_summary=False):
    """错误信息优先级：行级/用例级真因在前，平台封装文案/汇总行在后。"""
    if is_summary:
        return 7  # 「Test FAILED due to ...」只见失败用例名，不含原因，让位于断言原文与日志行
    lv = (level or "").upper()
    if lv == "LOG":
        return 0
    if lv in ("ERROR", "FAIL"):
        return 1
    if lv == "CASE":
        return 2
    if lv == "SUBTASK":
        return 3
    if lv == "JOB":
        return 8
    if lv == "STEP":
        return 9
    return 5


def _is_summary_msg(m):
    """是否是 pytest 的失败汇总行（只列失败用例名，不含失败原因）。"""
    txt = m.get("msg") if isinstance(m, dict) else m
    return bool(SUMMARY_MSG_RE.match(str(txt or "")))


EXC_HEAD_RE = re.compile(
    r"^\s*(?:[A-Za-z_][\w.]*(?:Error|Exception|Warning)|Traceback|FAILED|ERROR|E\s{2,})\b")
WHERE_HEAD_RE = re.compile(r"^\s*\+?\s*where\b", re.I)
# pytest 断言尾巴：「... ；+  where False = AtkTaskResult(success=False, ...)」，只保留 where 之前的结论
WHERE_TAIL_RE = re.compile(r"[;,]?\s*\+\s*where\b")


def _cut(t, limit):
    return (t[:limit] + " ...[truncated]") if len(t) > limit else t


def _cut_where(text):
    """去掉断言里的「+  where False = AtkTaskResult(...)」尾巴（真正的结论在它前面）。"""
    m = WHERE_TAIL_RE.search(text)
    return text[:m.start()].rstrip(" ;,") if m and m.start() > 0 else text


def _quoted(text, key, quote="'"):
    """取 key=<quote>...<quote> 的值，返回 (值, 是否被截断)；取不到返回 (None, False)。

    description 在采集侧按字符数截断，perf 类用例（excel_file 路径极长）会把 error_msg 的
    收尾引号切掉，因此不能用「找下一个引号」的方式取值，只能按「截断到串尾」兼容。
    """
    m = re.search(re.escape(key) + quote + "([^" + quote + "\n]*)", text)
    if not m:
        return None, False
    val = m.group(1).strip()
    if not val:
        return None, False
    closed = re.search(re.escape(key) + quote + "[^" + quote + "\n]*" + quote, text)
    return val, not closed


def _readable_desc(text):
    """把测试报告 description 提炼成一行可读原因。

    description 常以 pytest 的「+  where False = AtkTaskResult(...)」开头，真正可读的原因在
    error_msg / raw_output 里；提炼后仍保留原文（cases[].description 不动），不丢证据。
    """
    if EXC_HEAD_RE.match(text) or not WHERE_HEAD_RE.match(text):
        # 已是可读结论（如「AssertionError: xxx 测试失败: ... accuracy not passed」），
        # 只需去掉尾部 pytest 断言结构
        return _cut_where(text)
    parts = []
    val, cut = _quoted(text, "error_msg=")
    if val and (not cut or len(val) >= 8):
        parts.append(val + " ..." if cut else val)
    val, cut = _quoted(text, "raw_output=", '"')
    if val:
        val = re.sub(r"\s+", " ", val)[:200]
        # 截断后只剩几个字符的残片（如「Attri」）没有参考价值，直接丢掉
        if not cut or len(val) >= 8:
            parts.append(val + " ..." if cut else val)
    if parts:
        return _cut_where("；".join(parts))
    # error_msg / raw_output 都没取到（被截断在更靠前的位置）：给可读兜底，并带上测试报告线索
    hint = ""
    xls, _ = _quoted(text, "excel_file=")
    if xls:
        hint = "（测试报告：%s）" % Path(xls).name
    return "用例断言失败（断言原文被截断）%s，详见测试报告" % hint


def _case_assertion(case, limit=600):
    """取用例失败原因原文：deepHits（用例日志里扫到的异常文本）优先，其次 description 提炼。"""
    for h in case.get("deepHits") or []:
        t = _cut_where(str(h or "").strip())
        if t:
            return _cut(_readable_desc(t), limit), (case.get("localLog") or case.get("logUrl"))
    t = str(case.get("description") or "").strip()
    if t:
        return _cut(_readable_desc(t), limit), "测试报告"
    return None, None


def flatten_errors(tasks):
    """错误信息展平：每条带来源任务/文件/行号，保证任何一条都能回原文核对。

    排序即优先级：LOG（整 job 日志错误行）/ ERROR（用例日志 ERROR、FAIL 行）/ CASE（用例断言
    原文）在前，SUBTASK 次之，pytest 汇总行（「Test FAILED due to ...」，打 isSummary）与平台
    封装文案 STEP / JOB（打 isWrapper）排最后，这样页面「错误信息」列取第一条时拿到的是真因，
    而不是 DEV-CODECI-* 封装码或只列用例名的汇总行。
    """
    rows = []
    for t in tasks:
        src = t.get("logFile") or t.get("jobName")
        local = []
        for s in t.get("stepErrMsgs") or []:
            em = s.get("errorMessage") if isinstance(s, dict) else s
            if em:
                local.append({"task": t.get("jobName"), "source": src, "line": None,
                              "level": "STEP", "text": str(em), "isWrapper": True})
        if t.get("jobErrMsg"):
            local.append({"task": t.get("jobName"), "source": src, "line": None,
                          "level": "JOB", "text": str(t["jobErrMsg"]), "isWrapper": True})
        for e in t.get("errorLines") or []:
            if isinstance(e, dict):
                local.append({"task": t.get("jobName"), "source": src,
                              "line": e.get("line"), "level": "LOG",
                              "text": e.get("text") or ""})
            else:
                m = re.match(r"L(\d+):\s*(.*)", str(e))
                local.append({"task": t.get("jobName"), "source": src,
                              "line": int(m.group(1)) if m else None, "level": "LOG",
                              "text": m.group(2) if m else str(e)})
        for c in t.get("cases") or []:
            msgs = [m for m in (c.get("errorMessages") or []) if m]
            summary = [m for m in msgs if _is_summary_msg(m)]
            real = [m for m in msgs if not _is_summary_msg(m)]
            if not real:
                # 用例日志只剩「Test FAILED due to ...」汇总行（或压根没取到消息）时，
                # 真因是测试报告里的断言原文：deepHits 来自用例日志，description 来自测试报告
                text, origin = _case_assertion(c)
                if text:
                    local.append({"task": t.get("jobName"), "case": c.get("name"),
                                  "source": origin, "line": None, "level": "CASE",
                                  "text": text, "assertion": True})
            for em in (real or summary):
                if isinstance(em, dict):
                    row = {"task": t.get("jobName"), "case": c.get("name"),
                           "source": c.get("localLog") or c.get("logUrl"),
                           "line": em.get("line"), "level": em.get("level") or "ERROR",
                           "text": em.get("msg") or ""}
                else:
                    row = {"task": t.get("jobName"), "case": c.get("name"),
                           "source": c.get("localLog") or c.get("logUrl"),
                           "line": None, "level": "ERROR", "text": str(em)}
                if not real:
                    row["isSummary"] = True
                local.append(row)
        for st in t.get("subTasks") or []:
            for e in st.get("errorLines") or []:
                m = re.match(r"L(\d+):\s*(.*)", str(e))
                local.append({"task": t.get("jobName"), "subTask": st.get("name"),
                              "source": st.get("logFile"), "level": "SUBTASK",
                              "line": int(m.group(1)) if m else None,
                              "text": m.group(2) if m else str(e)})
        local.sort(key=lambda r: _err_rank(r.get("level"), r.get("isSummary")))
        rows.extend(local)
    return rows


def flatten_cases(tasks):
    rows = []
    for t in tasks:
        for c in t.get("cases") or []:
            rows.append({
                "task": t.get("jobName"), "name": c.get("name"), "state": c.get("state"),
                "description": c.get("description"),
                "beginTime": c.get("beginTime"), "endTime": c.get("endTime"),
                "logUrl": c.get("logUrl"), "localLog": c.get("localLog"),
                "errorCount": len(c.get("errorMessages") or []),
                "errorMessages": c.get("errorMessages") or [],
                "deepHits": c.get("deepHits") or [],
            })
    return rows


def evidence_files(tasks):
    files = []
    for t in tasks:
        if t.get("logFile"):
            files.append(t["logFile"])
        for c in t.get("cases") or []:
            if c.get("localLog"):
                files.append(c["localLog"])
        for st in t.get("subTasks") or []:
            if st.get("logFile"):
                files.append(st["logFile"])
    return sorted(set(files))


def classify(run, tasks):
    """定性（规则可复核）：返回 (category, basis[]) —— 每条依据都是可在证据里核对的陈述。"""
    basis = []
    if not tasks:
        return "evidence_missing", ["该运行无 FAILED 任务证据（任务被取消或接口未返回失败任务）"]
    has_file = any(t.get("logFile") for t in tasks)
    if not has_file:
        # 无 job 日志落盘时也要先看用例级证据：test 任务只要取到失败用例（哪怕用例日志
        # 未下载、只剩 description），就已经有可核对的定性依据，不该判成 evidence_missing
        for t in tasks:
            if t.get("type") != "test" or not t.get("cases"):
                continue
            n_msg = sum(len(c.get("errorMessages") or []) for c in t["cases"])
            basis.append("test 任务 %s 取到 %d 条失败用例%s（无 job 日志，证据来自测试报告/用例日志）"
                         % (t["jobName"], len(t["cases"]),
                            ("、%d 条用例级 ERROR/FAIL 消息" % n_msg) if n_msg else ""))
            return "test_failure", basis
        note = [t.get("evidenceNote") for t in tasks if t.get("evidenceNote")]
        if run.get("status") == "CANCELED":
            return "cancelled", ["运行状态 CANCELED，运行平台侧未留存任务日志"] + note
        return "evidence_missing", ["全部失败任务均无日志落盘"] + note

    build_tasks = [t for t in tasks if (t.get("jobName") or "").lower().startswith("build")]
    for t in build_tasks:
        corpus = task_corpus(t)
        bad_sub = [st for st in t.get("subTasks") or [] if st.get("status") == "FAILURE"]
        if bad_sub:
            basis.append("Build 任务 %s 下钻到 %d 个失败子任务（%s）"
                         % (t["jobName"], len(bad_sub),
                            "、".join((st.get("name") or "?") for st in bad_sub[:3])))
            if COMPILE_HINT.search(corpus):
                basis.append("子任务日志/错误行命中编译报错特征（如 error: / fatal error / collect2）")
            return "compile_failure", basis
        if COMPILE_HINT.search(corpus):
            basis.append("Build 任务 %s 日志命中编译报错特征" % t["jobName"])
            return "compile_failure", basis

    for t in tasks:
        if t.get("type") != "test":
            continue
        if t.get("cases"):
            n_msg = sum(len(c.get("errorMessages") or []) for c in t["cases"])
            if n_msg:
                basis.append("test 任务 %s 取到 %d 条失败用例、%d 条用例级 ERROR/FAIL 消息"
                             % (t["jobName"], len(t["cases"]), n_msg))
                return "test_failure", basis
            basis.append("test 任务 %s 取到 %d 条失败用例（无 ERROR/FAIL 行，见 deepHits）"
                         % (t["jobName"], len(t["cases"])))
            return "test_failure", basis

    empty_test = [t for t in tasks if t.get("type") == "test" and t.get("emptyTestResult")]
    if empty_test:
        basis.append("test 任务 %s 测试结果为空（用例未拉起），已回退整 job 日志"
                     % empty_test[0]["jobName"])
        return "env_not_ready", basis

    for t in tasks:
        if t.get("errorLines"):
            basis.append("任务 %s 日志命中 %d 条错误行（非编译特征），见 %s"
                         % (t["jobName"], len(t["errorLines"]), t.get("logFile")))
            return "job_failure", basis

    if run.get("status") == "CANCELED":
        return "cancelled", ["运行状态 CANCELED"]
    basis.append("失败任务已取到日志但未命中错误特征，需按日志尾部人工确认")
    return "unknown", basis


def confidence_of(run, tasks, basis):
    """置信度（可复核的加分/扣分项）。"""
    score, why = 0.30, ["基线 0.30"]
    files = evidence_files(tasks)
    if files:
        score += 0.25
        why.append("证据文件落盘 %d 份 +0.25" % len(files))
    if any(t.get("errorLines") for t in tasks):
        score += 0.15
        why.append("任务日志命中错误行 +0.15")
    n_case_msg = sum(len(c.get("errorMessages") or [])
                     for t in tasks for c in t.get("cases") or [])
    if n_case_msg:
        score += 0.20
        why.append("用例级错误消息 %d 条 +0.20" % n_case_msg)
    if any(st.get("logFile") for t in tasks for st in t.get("subTasks") or []):
        score += 0.10
        why.append("失败子任务日志落盘 +0.10")
    gaps = [t["jobName"] for t in tasks
            if not t.get("logFile") and not t.get("cases") and not t.get("subTasks")]
    for g in gaps:
        score -= 0.20
        why.append("任务 %s 无任何证据 −0.20" % g)
    score = max(0.10, min(0.95, round(score, 2)))
    return score, why


def main():
    s1p = load_json(OUT_DIR / "step_1_products.json")
    s1b = load_json(OUT_DIR / "step_1_bad_pipelines.json")
    s2 = load_json(OUT_DIR / "step_2_failed_runs.json")
    s3 = load_opt("step_3_params.json") or {}
    s4 = load_opt("step_4_test_evidence.json") or {"results": [], "errors": []}
    s5 = load_opt("step_5_log_evidence.json") or {"results": [], "errors": []}
    s5c = load_opt("step_5c_github_evidence.json") or {"results": [], "errors": []}
    case_errors = load_opt("step_4_case_errors.json") or []
    err_by_url = {e["url"]: e for e in case_errors if e.get("downloadStatus") == "ok"}

    test_idx, log_idx = {}, {}
    for r in s4.get("results") or []:
        test_idx.setdefault((r["key"], r["rn"]), []).append(r)
    for r in s5.get("results") or []:
        log_idx.setdefault((r["key"], r["rn"]), []).append(r)
    gh_idx = {r.get("rid"): r for r in s5c.get("results") or []}

    records = []
    n_success = 0
    for b in s1b["pipelines"]:
        key = b["key"]
        info = s2.get(key) or {"failedRuns": [], "runHistory": []}
        failed_by_rn = {fr["rn"]: fr for fr in (info.get("failedRuns") or [])}
        # 记录来源 = 区间内**全部运行**（成功 + 失败）：失败的走证据/定性/置信度，
        # 成功的只落轻量记录（状态/起止时间/编译与可用口径），保证「每天的记录都完整」
        # AI 只分析非 1 指标：当日两个指标都为 1 的流水线，其失败运行照旧取证定性，
        # 但不进入 AI 根因（analysis.status=na）。vllm-ascend 的指标在 step_2 已按
        # scheduled 运行重算（不看板值是否为 1），所以同一口径对它自动生效。
        ok_day = lights_ok(b.get("versionAvailabilityRate"), b.get("buildSuccessRate"))
        for run in info.get("runHistory") or []:
            rn = run["rn"]
            fr = failed_by_rn.get(rn) or {}
            needs = bool(fr)
            analyze = needs and not ok_day
            tasks = []
            for r in test_idx.get((key, rn), []):
                cases = []
                for c in r.get("failedCases") or []:
                    ce = err_by_url.get(c.get("resultDownloadUrl") or "")
                    cases.append({
                        "name": c.get("name"), "state": c.get("state"),
                        "description": c.get("description"),
                        "beginTime": c.get("beginTime"), "endTime": c.get("endTime"),
                        "logUrl": c.get("resultDownloadUrl"),
                        "localLog": (ce or {}).get("localLog"),
                        "errorMessages": (ce.get("errors") if ce else []) or [],
                        "deepHits": (ce.get("deepHits") if ce else []) or [],
                    })
                tasks.append({
                    "jobName": r["jobName"], "type": "test",
                    "summary": r.get("summary"), "cases": cases,
                    "emptyTestResult": bool(r.get("empty")),
                    "stepErrMsgs": r.get("stepErrMsgs") or [],
                })
            for r in log_idx.get((key, rn), []):
                tasks.append({
                    "jobName": r["jobName"], "type": "log",
                    "fallback": bool(r.get("fallback")),
                    "logFile": r.get("logFile"),
                    "logBytes": r.get("logBytes"), "logLines": r.get("logLines"),
                    "download": r.get("download"), "truncated": r.get("truncated"),
                    "excerpt": r.get("excerpt"),
                    "stepErrMsgs": r.get("stepErrMsgs") or [],
                    "jobErrMsg": r.get("jobErrMsg"),
                    "errorLines": r.get("errorLines") or [],
                    "subTasks": r.get("subTasks") or [],
                })
            gh_row = gh_idx.get(run.get("rid"))
            if gh_row:
                for j in gh_row.get("jobs") or []:
                    t = {
                        "jobName": j["name"], "type": "log", "platform": "github",
                        "logFile": j.get("logFile"),
                        "logBytes": j.get("logBytes"), "logLines": j.get("logLines"),
                        "download": "github-%s" % gh_row.get("mode"), "truncated": False,
                        "stepErrMsgs": [], "jobErrMsg": None,
                        "errorLines": j.get("errorLines") or [],
                        "githubJob": {"id": j.get("id"), "conclusion": j.get("conclusion"),
                                      "startedAt": j.get("startedAt"),
                                      "completedAt": j.get("completedAt"),
                                      "steps": j.get("githubSteps") or []},
                    }
                    if j.get("isTestJob"):
                        t["isTestJob"] = True
                        t["testResults"] = j.get("testResults")
                    tasks.append(t)
                for m in gh_row.get("missing") or []:
                    if m.get("job") == "*":
                        continue
                    tasks.append({
                        "jobName": m.get("job"), "type": "log", "platform": "github",
                        "logFile": None, "errorLines": [],
                        "download": "github-404", "truncated": False,
                        "stepErrMsgs": [], "jobErrMsg": None,
                        "evidenceNote": "运行平台侧日志未留存（404），证据不完整；"
                                        "定性依据 jobs 元数据（conclusion/时长/失败步骤）",
                    })

            end_raw = run.get("end") or ""
            start_raw = run.get("start") or ""
            end_iso = iso_of(end_raw) or ""
            date = date_of(end_raw) or date_of(start_raw) or ""
            if not date:
                print("!! 跳过无结束时间的运行（无法按结束时间归档）: %s#%s"
                      % (b["pipelineShortName"], rn))
                continue

            if needs:
                # 失败 / 取消运行：走第 3~5c 步证据链，定性 + 置信度逐条可核
                category, basis = classify(fr, tasks)
                score, why = confidence_of(fr, tasks, basis)
                track = fr.get("track") or "va"
                ftt = fr.get("ftt") or run.get("ftt")
                links = {
                    "openlibing": fr.get("openlibingUrl") or run.get("url"),
                    "detail": fr.get("detailUrl"),
                    "github": ("https://github.com/%s/actions/runs/%s"
                               % (gh_row.get("repo"), gh_row.get("runId"))) if gh_row else None,
                }
            else:
                # 成功运行：不取证、不分析，只落轻量记录，保证「每天全部构建记录」完整
                n_success += 1
                category = "success"
                basis = ["运行状态 %s，任务未失败，无需根因分析" % (run.get("status") or "-")]
                score, why = 1.0, []
                track, ftt = "-", None
                links = {
                    "openlibing": run.get("url"),
                    "detail": None,
                    "github": ("https://github.com/%s/actions/runs/%s"
                               % (gh_row.get("repo"), gh_row.get("runId"))) if gh_row else None,
                }
            cases = flatten_cases(tasks)
            errors = flatten_errors(tasks)
            files = evidence_files(tasks)

            # 失败/取消运行一律待第 7 步 agent 逐条分析，不复用任何历史结论；
            # 指标全为 1 的达标日不做 AI 分析（status=na），证据仍然保留可查
            if analyze:
                analysis = {"status": "pending", "rootCause": None, "advice": None,
                            "cites": [], "updatedAt": None, "source": None}
            else:
                # analysis.status=na：成功运行 / 指标达标日不进入第 7 步根因分析
                analysis = {"status": "na", "rootCause": None, "advice": None,
                            "cites": [], "updatedAt": None, "source": None}
            record = {
                "recordId": "%s#%s" % (key, rn),
                "date": date,
                "component": safe_name(b["product"], "unknown_component"),
                "pipeline": safe_name(b["pipelineShortName"], "unknown_pipeline"),
                "pipelineName": b["pipelineName"],
                "platform": b.get("platform") or "openlibing",
                "buildNumber": rn,
                "pipelineRunId": run.get("rid"),
                "status": run.get("status"),
                "failedTaskType": ftt,
                "startedAt": iso_of(start_raw),
                "endedAt": end_iso,
                "dateBasis": {"field": DATE_FIELD, "value": end_iso, "rule": DATE_RULE},
                "light": {
                    "vaRate": b.get("versionAvailabilityRate"),
                    "bsRate": b.get("buildSuccessRate"),
                    "dims": b.get("dims") or [], "track": track,
                },
                "links": links,
                "tasks": tasks,
                "cases": cases,
                "errorMessages": errors,
                "evidence": {
                    "files": files,
                    "missing": sorted({t["jobName"] for t in tasks
                                       if not t.get("logFile") and not t.get("cases")}),
                    "github": {
                        "runId": gh_row.get("runId"), "repo": gh_row.get("repo"),
                        "displayTitle": gh_row.get("displayTitle"),
                        "mode": gh_row.get("mode"), "totalJobs": gh_row.get("totalJobs"),
                        "failedJobs": gh_row.get("failedJobs") or [],
                        "missing": gh_row.get("missing") or [],
                        "complete": gh_row.get("ghComplete"),
                    } if gh_row else None,
                },
                "counts": {
                    "tasks": len(tasks),
                    "testTasks": sum(1 for t in tasks if t.get("type") == "test"),
                    "logTasks": sum(1 for t in tasks if t.get("type") == "log"),
                    "buildTasks": sum(1 for t in tasks
                                      if (t.get("jobName") or "").lower().startswith("build")),
                    "cases": len(cases),
                    "errorMessages": len(errors),
                    "evidenceFiles": len(files),
                },
                "verdict": {
                    "category": category, "text": CATEGORY_TEXT[category],
                    "confidence": score,
                    "level": "高" if score >= 0.75 else ("中" if score >= 0.5 else "低"),
                    "basis": basis + why,
                },
                "suggestions": [],
                "analysis": analysis,
            }
            records.append(record)

    records.sort(key=lambda r: (r["date"], r["component"], r["pipeline"], r["buildNumber"]))
    coverage = {
        "records": len(records),
        "days": len({r["date"] for r in records}),
        "components": len({r["component"] for r in records}),
        "pipelines": len({(r["component"], r["pipeline"]) for r in records}),
        "tasks": sum(r["counts"]["tasks"] for r in records),
        "cases": sum(r["counts"]["cases"] for r in records),
        "errorMessages": sum(r["counts"]["errorMessages"] for r in records),
        "evidenceFiles": sum(r["counts"]["evidenceFiles"] for r in records),
        "evidenceMissingRecords": sum(1 for r in records
                                      if r["verdict"]["category"] == "evidence_missing"),
        # 待第 7 步 AI 分析的记录数（只含非 1 指标的失败运行）
        "analysisPending": sum(1 for r in records if r["analysis"]["status"] == "pending"),
        "byCategory": {k: sum(1 for r in records if r["verdict"]["category"] == k)
                       for k in CATEGORY_TEXT},
    }

    notes, _seen = [], set()
    for n in list(s1p.get("notes") or []) + list(s1b.get("notes") or []):
        if n not in _seen:
            _seen.add(n)
            notes.append(n)
    notes.append("记录粒度：一条执行（流水线 + 构建号）一条记录，按执行结束时间归属日期；"
                 "记录来源为区间内全部运行（成功 + 失败），成功运行只落轻量记录"
                 "（verdict.category=success、analysis.status=na），失败/取消运行才带证据与根因；"
                 "定性/置信度由第 1~5c 步证据按固定规则推导（verdict.basis 逐条可核）；"
                 "根因与建议一律由第 7 步 agent 逐条补齐（analysis.status=pending），不复用历史结论")
    notes.append("AI 分析范围（analysis.status）：只分析非 1 指标 —— 该流水线当日两个指标"
                 "（版本可用度、编译成功率）都为 1 的达标日不进入 AI 根因（status=na），"
                 "失败运行的证据照旧保留可查；vllm-ascend 的指标已在第 2 步按 scheduled 运行重算"
                 "（不看板值是否为 1、只统计含 scheduled 的运行），达标判定同样用重算值")
    notes.append("证据行号：errorMessages[].line / errorLines 的 L<n> 均为原文行号，"
                 "可直接回 evidence.files 中的文件按行核对；github 侧 404 未留存的 job 记入 evidence.missing")
    wd = s2.get("_meta") or {}
    if wd:
        notes.append("区间内 %d 个工作日，已跳过非工作日运行 %d 次、无结束时间运行 %d 次"
                     % (wd.get("workdayCount") or 0, wd.get("skippedNonWorkdayRuns") or 0,
                        wd.get("skippedRunningRuns") or 0))
        if wd.get("dashFailed"):
            notes.append("N2 接口失败的流水线（无执行记录，无法圈定待查运行）：%s"
                         % "、".join(wd["dashFailed"]))

    out = {
        "_doc": doc("step_6_records", recordCount=len(records),
                    inputFiles=["step_1_bad_pipelines.json", "step_2_failed_runs.json",
                                "step_3_params.json", "step_4_test_evidence.json",
                                "step_4_case_errors.json", "step_5_log_evidence.json",
                                "step_5c_github_evidence.json"]),
        "meta": {
            "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dateRange": s1p.get("dateRange"),
            "components": [p["product"] for p in s1p["products"] if p.get("analyze")],
            "skill": "openlibing-nightly-rca",
        },
        "coverage": coverage,
        "notes": notes,
        "records": records,
    }
    save_json(OUT_DIR / "step_6_records.json", out)

    print("== 第 6 步 逐条执行记录（一条执行一条记录）==")
    print("记录 %d 条（执行成功 %d / 失败或取消 %d）| 覆盖 %d 天 / %d 个组件 / %d 条流水线"
          % (coverage["records"], n_success, coverage["records"] - n_success,
             coverage["days"], coverage["components"], coverage["pipelines"]))
    print("待 AI 根因 %d 条（只含非 1 指标的失败运行；达标日 status=na）"
          % coverage["analysisPending"])
    print("任务 %d 个（test %d / log %d / Build* %d）| 失败用例 %d 条 | 错误信息 %d 条 | 证据文件 %d 份"
          % (coverage["tasks"],
             sum(r["counts"]["testTasks"] for r in records),
             sum(r["counts"]["logTasks"] for r in records),
             sum(r["counts"]["buildTasks"] for r in records),
             coverage["cases"], coverage["errorMessages"], coverage["evidenceFiles"]))
    print("定性分布: " + " | ".join("%s %d" % (CATEGORY_TEXT[k], v)
                                    for k, v in coverage["byCategory"].items() if v))
    n_pending = sum(1 for r in records if r["analysis"]["status"] == "pending")
    print("待第 7 步分析 %d 条 / 无需分析（成功）%d 条" % (n_pending, n_success))
    print("-> out/work/step_6_records.json")


if __name__ == "__main__":
    main()
