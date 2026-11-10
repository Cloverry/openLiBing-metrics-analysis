# -*- coding: utf-8 -*-
"""步骤脚本公共基础设施：配置加载、页面 JS 渲染执行、JSON 读写、日期区间归一化。"""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

from lib.paths import (ARCHIVE_ROOT, CACHE_DIR, GEN_DIR, HTML_DIR, LIB_DIR,
                       OUT_DIR as OUT_ROOT, SKILL_ROOT, STATE_DIR, TPL_DIR,
                       WORK_DIR)

# 各步骤的落盘根目录 = 中间产物区 out/work/（第 7 步归档时才分发到 out/archive/）。
# 这样全部步骤脚本无需感知归档结构，仍写 out/work/step_N_xxx.json。
OUT_DIR = WORK_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _deep_merge(base, over):
    """递归合并：over 的 dict 逐键合并，其余直接覆盖。用于本地私有配置叠加到 config.json。"""
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config():
    """读取 config.json；环境变量 RCA_START / RCA_END / RCA_PRODUCTS / RCA_FRESH 可临时覆盖（run_pipeline 传入）。

    私有凭据走 config.local.json（已在 .gitignore 中，不进仓库）：其任意字段递归覆盖 config.json；
    github.token 另支持环境变量 RCA_GH_TOKEN，优先级 config.local.json > RCA_GH_TOKEN > config.json。
    """
    with open(SKILL_ROOT / "config.json", encoding="utf-8") as f:
        cfg = json.load(f)
    local_path = SKILL_ROOT / "config.local.json"
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            _deep_merge(cfg, json.load(f))
    env = os.environ
    if env.get("RCA_START"):
        cfg.setdefault("date_range", {})["start"] = env["RCA_START"]
    if env.get("RCA_END"):
        cfg.setdefault("date_range", {})["end"] = env["RCA_END"]
    if env.get("RCA_PRODUCTS"):
        cfg["products"] = [x.strip() for x in env["RCA_PRODUCTS"].split(",") if x.strip()]
    if env.get("RCA_FRESH"):
        cfg.setdefault("cache", {})["enabled"] = False
    if env.get("RCA_GH_TOKEN") and not (cfg.get("github") or {}).get("token"):
        cfg.setdefault("github", {})["token"] = env["RCA_GH_TOKEN"].strip()
    return cfg


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def normalize_date_range(cfg):
    """返回 (start, end)。每日跑数：config 留空则取「前一个工作日」单日区间。

    默认区间 = 今天之前最近的一个工作日（跳过周末与法定节假日）：
    周一跑取上周五，节后首日跑取节前最后一个工作日，周末/节假日跑同样向前回溯。
    区间恒为单日（start == end）；config.date_range 显式填写时以配置为准。
    """
    dr = cfg.get("date_range") or {}
    start = str(dr.get("start") or "").strip()
    end = str(dr.get("end") or "").strip()
    if not start or not end:
        # 延迟导入：lib.workdays 反向依赖本模块的 SKILL_ROOT，顶层导入会循环
        from lib.workdays import previous_workday
        day = previous_workday(cfg=cfg)
        start, end = day, day
    return start, end


def date_range_source(cfg):
    """默认区间的来源说明，供各步骤日志打印，避免「日期到底怎么来的」被质疑。"""
    dr = cfg.get("date_range") or {}
    explicit = str(dr.get("start") or "").strip() and str(dr.get("end") or "").strip()
    return "config.date_range 显式指定" if explicit else "默认：前一个工作日（跳过周末与法定节假日）"


def unwrap_records(resp):
    """T1/T2 响应包装层剥壳：{code, data:{records:[...]}} -> records 列表。"""
    data = resp
    while isinstance(data, dict) and "records" not in data:
        if not isinstance(data.get("data"), (dict, list)):
            raise RuntimeError("响应无 records，可能未登录或接口报错：" + json.dumps(resp, ensure_ascii=False)[:400])
        data = data["data"]
    return data["records"] if isinstance(data, dict) else data


def run_page_js(tpl_name, params, out_name, cfg, label=""):
    """渲染页面模板（注入 const PARAMS）→ node cdp_run.js 执行 → 返回解析后的 JSON。

    模板内部业务失败（未登录等）通过 {"ok": false, "error": ...} 上抛。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    tpl = (TPL_DIR / tpl_name).read_text(encoding="utf-8")
    gen_js = GEN_DIR / (out_name.replace(".json", "") + ".gen.js")
    out_file = OUT_DIR / out_name
    # 外层 async IIFE 使 PARAMS 成为函数作用域局部量：避免 Runtime.evaluate 顶层 const
    # 持久驻留页面全局词法环境，导致同名变量二次声明报 SyntaxError
    gen_js.write_text(
        "(async () => {\nconst PARAMS = " + json.dumps(params, ensure_ascii=False)
        + ";\nreturn " + tpl + "\n})()",
        encoding="utf-8")

    ch = cfg.get("chrome", {})
    port = str(ch.get("debug_port", 9222))
    timeout = int(ch.get("request_timeout_ms", 300000))
    tab = str(ch.get("tab_url_substring", "openlibing"))
    cmd = ["node", str(LIB_DIR / "cdp_run.js"), str(gen_js),
           "--out", str(out_file), "--tab", tab, "--port", port, "--timeout", str(timeout)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(SKILL_ROOT),
                              timeout=timeout // 1000 + 60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("[%s] node 进程超时被杀" % (label or tpl_name))
    summary = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
                break
            except ValueError:
                pass
    if proc.returncode != 0 or not summary.get("ok"):
        detail = summary.get("error") or (proc.stderr or "")[:600] or ("exit=" + str(proc.returncode))
        raise RuntimeError("[%s] 页面脚本执行失败: %s" % (label or tpl_name, detail))
    result = load_json(out_file)
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError("[%s] %s" % (label or tpl_name, result.get("error")))
    return result


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def parse_rate(v, hundred_scale=False):
    """比率字段解析。hundred_scale=True 时 0-100 分制归一化为 0-1；空串/None 返回 None（=无数据）。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "":
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if hundred_scale and f > 1.5:
        f = f / 100.0
    return f


def pct(v):
    return "{:.2%}".format(v) if v is not None else "--"
