# -*- coding: utf-8 -*-
"""归档与路径约定 —— 采集数据 / 展示层 / 中间产物的唯一路径出口。

目录布局（数据与 HTML 完全隔离）::

  out/
  ├─ archive/                             归档区：按天保留、逐层可查（每日一个目录，可整日删除）
  │  └─ <yyyy>/<yyyy-mm>/<yyyy-mm-dd>/    按【执行结束时间】分层
  │     ├─ _day.json                      当日汇总（产品级比率 + 流水线索引）
  │     └─ <开源组件名>/<流水线名>/
  │        ├─ _manifest.json              目录清单：每步来源/口径/计数/文件
  │        ├─ step_1_lights.json          第 1 步 该流水线的筛选结果
  │        ├─ step_2_runs.json            第 2 步 …
  │        ├─ step_3_tasks.json
  │        ├─ step_4_cases.json
  │        ├─ step_5_logs.json
  │        ├─ step_5b_subtasks.json
  │        ├─ step_5c_github.json
  │        ├─ step_6_records.json         每条执行一条记录（清单）
  │        ├─ records/run_<rn>.json       单条执行记录（agent 逐条分析的最小单位）
  │        ├─ job_logs/  case_logs/  github_logs/
  ├─ work/                                中间产物（页面脚本产物/分片/gen.js），每次重跑覆盖写入
  ├─ state/                               跨日期状态：cache/、run_pipeline.log
  └─ html/                                展示层（只有 HTML，不含采集数据）

口径（全局唯一）：所有日期归属、筛选、分页一律取【执行结束时间】
（openLiBing N2 字段 pipelineRunEndtime / GitHub Actions run.updated_at）。
"""
import datetime
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = SKILL_ROOT / "out"

ARCHIVE_ROOT = OUT_DIR / "archive"
WORK_DIR = OUT_DIR / "work"
STATE_DIR = OUT_DIR / "state"
HTML_DIR = OUT_DIR / "html"

CACHE_DIR = STATE_DIR / "cache"
GEN_DIR = WORK_DIR / "_gen"
TPL_DIR = SKILL_ROOT / "tpl"
LIB_DIR = SKILL_ROOT / "lib"

# 全局口径：数据按执行结束时间归属日期
DATE_FIELD = "pipelineRunEndtime"
DATE_RULE = "按执行结束时间归属日期（结束时间在 [start 00:00:00, end 23:59:59] 内的执行计入该日期）"

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def safe_name(s, fallback="unknown"):
    """把组件名/流水线名规整为可用作目录名的安全串（保留中文与常见符号）。"""
    s = str(s or "").strip()
    s = ILLEGAL.sub("_", s).strip(" .")
    s = re.sub(r"_{2,}", "_", s)
    return s[:120] or fallback


def date_of(value):
    """从 'yyyy-mm-dd' / 'yyyy-mm-dd HH:MM:SS' / ISO 串取日期部分；无效返回 None。"""
    s = str(value or "").strip()
    if len(s) < 10:
        return None
    s = s[:10]
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return None
    return s


def date_parts(value):
    """日期 -> (年, 年-月, 日期) 三级目录名。"""
    d = date_of(value)
    if not d:
        raise ValueError("非法日期: %r" % (value,))
    return d[:4], d[:7], d


def archive_day_dir(date):
    y, ym, d = date_parts(date)
    return ARCHIVE_ROOT / y / ym / d


def archive_dir(date, component, pipeline):
    """某日 / 某组件 / 某流水线 的归档目录。"""
    return archive_day_dir(date) / safe_name(component, "unknown_component") \
        / safe_name(pipeline, "unknown_pipeline")


def rel(p):
    """相对 out/ 的 POSIX 路径（写入 JSON，便于移动 out 整体）。"""
    p = Path(p).resolve()
    try:
        return p.relative_to(OUT_DIR.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def record_path(arch_dir, rn):
    return Path(arch_dir) / "records" / ("run_%02d.json" % int(rn))


def iter_archive_days():
    """遍历已有归档日目录，返回 [(date_str, Path)]，按日期升序。"""
    out = []
    if not ARCHIVE_ROOT.exists():
        return out
    for y in sorted(ARCHIVE_ROOT.iterdir()):
        if not y.is_dir() or not y.name.isdigit():
            continue
        for ym in sorted(y.iterdir()):
            if not ym.is_dir():
                continue
            for d in sorted(ym.iterdir()):
                if d.is_dir() and date_of(d.name):
                    out.append((d.name, d))
    return out


def iter_archive_pipelines(day_dir):
    """遍历某归档日下的 (组件, 流水线, 目录)。"""
    out = []
    day_dir = Path(day_dir)
    if not day_dir.exists():
        return out
    for comp in sorted(day_dir.iterdir()):
        if not comp.is_dir() or comp.name.startswith("_"):
            continue
        for pipe in sorted(comp.iterdir()):
            if pipe.is_dir():
                out.append((comp.name, pipe.name, pipe))
    return out


# ---------------------------------------------------------------- 步骤文档信封
# 每一步归档文件的题头：写清「这一步是什么、筛掉了什么、依据什么字段」，
# 让任一条结果的来龙去脉都可直接核对，防止口径被质疑。
STEP_DOCS = {
    "step_1_lights": {
        "title": "第 1 步 夜间统计与待分析圈定",
        "source": "openLiBing 夜间统计看板（产品维度指标）",
        "criteria": "默认取「前一个工作日」单日（跳过周末与法定节假日，config.date_range 留空时生效）："
                    "取 config.products 指定的开源组件在该日的版本可用度/编译成功率，"
                    "比率按我们采集到的执行记录重算（归档只存数值，不存黄/绿/红）；"
                    "本产品下任一指标未达 100%（或版本不可用）的流水线进入待分析集合",
        "date_field": "统计区间（start~end，含端点）",
    },
    "step_2_runs": {
        "title": "第 2 步 执行记录圈定（N2）",
        "source": "openLiBing 流水线 N2 执行列表",
        "criteria": DATE_RULE + "；仅保留工作日；VA 轨道=当日无 isVersionAvailable=true 的待查运行，"
                                "BS 轨道=豁免日补圈的 Build* 失败运行",
        "date_field": DATE_FIELD,
    },
    "step_3_tasks": {
        "title": "第 3 步 失败任务分流（T4）",
        "source": "openLiBing 流水线运行详情（任务列表）",
        "criteria": "仅取状态为失败的任务；UNSELECTED/CANCELED/IGNORED/INIT 跳过；"
                    "含测试结果的任务走测试取证（第 4 步），其余走日志取证（第 5 步），"
                    "github 平台运行改走第 5c 步",
        "date_field": DATE_FIELD,
    },
    "step_4_cases": {
        "title": "第 4 步 测试用例取证（T6）",
        "source": "openLiBing 测试报告页 + 用例日志下载",
        "criteria": "取失败任务的失败用例、错误信息、深度学习命中行，并下载用例日志到 case_logs/",
        "date_field": DATE_FIELD,
    },
    "step_5_logs": {
        "title": "第 5 步 任务日志取证（T5）",
        "source": "openLiBing 任务日志导出/分页接口",
        "criteria": "对每个失败任务导出整份日志落 job_logs/，并按关键字扫描错误行",
        "date_field": DATE_FIELD,
    },
    "step_5b_subtasks": {
        "title": "第 5b 步 buildflow 子任务下钻（T7）",
        "source": "openLiBing buildflow 子任务页",
        "criteria": "对 buildflow 任务的失败子任务逐个取真实编译报错与子任务日志",
        "date_field": DATE_FIELD,
    },
    "step_5c_github": {
        "title": "第 5c 步 GitHub 平台运行取证",
        "source": "GitHub Actions API（jobs / logs / 测试结果）",
        "criteria": "对 github 平台运行按 job 分流取日志，测试 job 解析测试结果定位到用例",
        "date_field": "run.updated_at（等价于执行结束时间）",
    },
    "step_6_records": {
        "title": "第 6 步 证据合并与执行记录",
        "source": "第 2~5c 步归档产物",
        "criteria": "以「一次执行」为最小单位合并任务、用例、错误信息、证据与构建编号，"
                    "形成可直接展示与逐条分析的记录",
        "date_field": DATE_FIELD,
    },
    "step_7_archive": {
        "title": "第 7 步 逐层归档",
        "source": "第 1~6 步中间产物（out/work/）",
        "criteria": "按执行结束时间逐层落到「年/年-月/日/开源组件名/流水线名」，"
                    "每步一份 JSON + records/run_<构建号>.json + 证据原文，并生成目录清单",
        "date_field": DATE_FIELD,
    },
    "step_9_pages": {
        "title": "第 9 步 生成展示页",
        "source": "out/archive/（_index.json / _day.json / _manifest.json / records/run_*.json）",
        "criteria": "只产出静态壳（样式 + 公共日期筛选组件 + 取数脚本）到 out/html/，"
                    "HTML 内不内嵌任何业务数据；页面运行时按【执行结束时间】区间读取归档 JSON，"
                    "首页与二级页共用 lib/ui.py 的同一份筛选组件，口径不会分叉",
        "date_field": DATE_FIELD,
    },
}


def doc(step, **kw):
    """生成步骤文档信封的 _doc 字段。"""
    base = dict(STEP_DOCS.get(step) or {})
    d = {
        "step": step,
        "title": base.get("title") or step,
        "source": base.get("source") or "",
        "criteria": base.get("criteria") or "",
        "dateField": base.get("date_field") or DATE_FIELD,
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for k, v in kw.items():
        if v is not None:
            d[k] = v
    return d


def ensure_dirs():
    for p in (ARCHIVE_ROOT, WORK_DIR, STATE_DIR, CACHE_DIR, HTML_DIR, GEN_DIR):
        p.mkdir(parents=True, exist_ok=True)


ensure_dirs()
