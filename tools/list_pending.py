# -*- coding: utf-8 -*-
"""待 AI 分析清单（只读）—— 第 8 步（AI）的唯一入口索引。

职责划分：
  脚本负责「哪些记录需要分析」+「每条记录的证据在哪」；
  AI 只负责读证据、写根因（写回用 tools/write_analysis.py），不碰筛选逻辑。

口径（与 steps/step_6_records.py 一致）：
  analysis.status=pending → 需要 AI 分析（非 1 指标的失败/取消运行）；
  analysis.status=na      → 不做 AI 分析（成功运行 / 当日两指标都为 1 的达标日）。
  vllm-ascend 的指标在第 2 步已按 scheduled 运行重算，达标判定用的就是重算值。
  遍历口径以当日 _day.json 的 pipelines 为准：磁盘上多出的旧目录（改名前/旧版本残留）
  会跳过并提示，不参与 pending 统计，也不需要手工删目录。

用法:
  python tools/list_pending.py                     # 最近一个归档日的待分析记录
  python tools/list_pending.py --date 2026-11-06   # 指定归档日
  python tools/list_pending.py --all               # 全部归档日的待分析记录
  python tools/list_pending.py --status na         # 反向查看被排除的记录
  python tools/list_pending.py --show <recordId>   # 单条记录的分析输入摘要（不含日志正文）
  python tools/list_pending.py --json out/state/pending.json   # 另存机器可读清单
  python tools/list_pending.py --strict            # 仍有待分析记录时退出码 2（校验 skill 是否真跑完）

输出纪律：单次回显 ≤ 100 行（超出只提示总数，用 --date/--limit 收窄），避免触发输出过长中断。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.paths import (ARCHIVE_ROOT, day_pipeline_scope, iter_archive_days,  # noqa: E402
                       rel, stale_pipeline_dirs)

DEFAULT_LIMIT = 40
SHOW_LIST_MAX = 12          # --show 时每类列表最多打印条数
SHOW_TEXT_MAX = 200         # --show 时单条文本截断长度


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def iter_records(day_dir, allowed=None):
    """遍历某归档日的全部单条记录：(组件目录名, 流水线目录名, 记录, 文件)。

    allowed 非空时只遍历集合内的 (组件, 流水线) 目录。
    """
    for comp in sorted(day_dir.iterdir()):
        if not comp.is_dir() or comp.name.startswith("_"):
            continue
        for pipe in sorted(comp.iterdir()):
            rd = pipe / "records"
            if not pipe.is_dir() or not rd.is_dir():
                continue
            if allowed is not None and (comp.name, pipe.name) not in allowed:
                continue
            for f in sorted(rd.glob("run_*.json")):
                try:
                    rec = load_json(f)
                except (ValueError, OSError) as exc:
                    print("!! 记录读取失败 %s: %s" % (rel(f), exc))
                    continue
                yield comp.name, pipe.name, rec, f


def collect(dates, status, stale=None):
    """按日期收集指定 analysis.status 的记录。

    遍历口径以当日 _day.json 为准；磁盘上多出的历史残留目录不计入，
    其路径追加到 stale（传空列表时）供调用方提示。
    """
    got = []
    for d in dates:
        day_dir = ARCHIVE_ROOT / d[:4] / d[:7] / d
        if not day_dir.is_dir():
            continue
        allowed = day_pipeline_scope(day_dir)
        if stale is not None:
            for comp, pipe, _ in stale_pipeline_dirs(day_dir):
                stale.append("%s/%s/%s" % (d, comp, pipe))
        for comp, pipe, rec, f in iter_records(day_dir, allowed):
            an = rec.get("analysis") or {}
            if not status or status == "all":
                pass  # 不过滤
            elif status == "pending":
                if an.get("status") != "pending":
                    continue
            elif status == "na":
                if an.get("status") != "na":
                    continue
            elif an.get("status") != status:
                continue
            got.append({"date": d, "component": comp, "pipeline": pipe,
                        "record": rec, "file": f})
    got.sort(key=lambda x: (x["date"], x["component"], x["pipeline"],
                            int(x["record"].get("buildNumber") or 0)))
    return got


def lights_text(rec):
    lt = rec.get("light") or {}
    def pct(v):
        try:
            return "%.0f%%" % (float(v) * 100)
        except (TypeError, ValueError):
            return "--"
    return "VA %s / BS %s" % (pct(lt.get("vaRate")), pct(lt.get("bsRate")))


def pad(s, n):
    """按显示宽度左对齐（中文按 2 列计，避免表格错位）。"""
    s = str(s)
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + " " * max(0, n - w) if w <= n else cut(s, n)


def cut(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:max(0, n - 1)] + "…"


def cmd_list(args):
    days = [d for d, _ in iter_archive_days()]
    if args.date:
        days = [args.date]
    elif not args.all and days:
        days = days[-1:]
    if not days:
        print("!! 没有归档日目录（先跑 python run_pipeline.py）")
        return 1

    stale = []
    rows = collect(days, args.status, stale=stale)
    label = ("待 AI 根因分析" if args.status == "pending" else "不做 AI 分析" if args.status == "na"
             else "全部记录" if not args.status else args.status)
    scope = "全部归档日" if (args.all or args.date) else "最近归档日"
    print("== %s：%d 条（%s %s；口径：只分析非 1 指标，vllm-ascend 用重算值）"
          % (label, len(rows), scope, (days[0] if len(days) == 1 else "%s~%s" % (days[0], days[-1]))))
    if stale:
        print("   注：%d 个目录不在当日 _day.json 口径内（旧版本/改名前残留），已跳过不计：%s"
              % (len(stale), ", ".join(stale[:5]) + ("…" if len(stale) > 5 else "")))
    if not rows:
        print("   （无）")
        return 0

    shown = rows if args.limit <= 0 else rows[:args.limit]
    # 「归档分类 / 规则置信度」只给排查用：页面定性列已改为按 AI 根因正文推可能性，不再展示这两个值
    print("%-4s %-18s %-26s %-7s %-19s %-17s %-6s %s"
          % ("#", "组件", "流水线", "构建号", "结束时间", "归档分类", "规则置信度", "记录文件"))
    for i, x in enumerate(shown, 1):
        rec = x["record"]
        v = rec.get("verdict") or {}
        print("%-4d %s %s %-7s %-19s %s %-6s %s"
              % (i, pad(x["component"], 18), pad(x["pipeline"], 26),
                 str(rec.get("buildNumber")), pad(rec.get("endedAt") or "--", 19),
                 pad(v.get("category") or "--", 17),
                 str(v.get("confidence") if v.get("confidence") is not None else "--"),
                 rel(x["file"])))
    if len(shown) < len(rows):
        print("… 共 %d 条，已显示前 %d 条（--limit 0 显示全部 / --date 收窄日期）"
              % (len(rows), len(shown)))

    rc = 0
    if args.strict:
        # 严格模式：有 pending 就非 0 退出，供「完成判据」做机器校验（本 skill 三段里第 2 段没做完）
        print("!! 流程未完成：还有 %d 条待 AI 根因分析，做完并 --only step9 收尾后才算跑完" % len(rows))
        rc = 2

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"date": x["date"], "component": x["component"],
                    "pipeline": x["pipeline"], "recordId": x["record"].get("recordId"),
                    "buildNumber": x["record"].get("buildNumber"),
                    "file": rel(x["file"])} for x in rows]
        out.write_text(json.dumps({"_doc": "待 AI 分析清单（tools/list_pending.py 产出）",
                                   "status": args.status, "count": len(payload),
                                   "records": payload}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print("== 清单已另存: %s" % rel(out))
    return rc


def norm_id(s):
    """recordId 归一化：真身是「组件|流水线#构建号」，同时容忍 / 与空白写法。"""
    return str(s or "").replace("/", "|").replace(" ", "")


def show_one(args):
    days = [args.date] if args.date else [d for d, _ in iter_archive_days()]
    rows = collect(days, "")  # 空状态 = 不过滤
    want = norm_id(args.show)
    hit = [x for x in rows if norm_id(x["record"].get("recordId")) == want]
    if not hit:
        # 退化为按构建号匹配（唯一时直接命中）
        hit = [x for x in rows if str(x["record"].get("buildNumber")) == str(args.show).lstrip("#")]
    if not hit:
        print("!! 未找到记录 %s（用 python tools/list_pending.py 查看可用 recordId）" % args.show)
        return 1
    if len(hit) > 1:
        print("!! 构建号 %s 命中 %d 条，请改用完整 recordId：" % (args.show, len(hit)))
        for x in hit:
            print("   %s  (%s)" % (x["record"].get("recordId"), rel(x["file"])))
        return 1

    rec = hit[0]["record"]
    v = rec.get("verdict") or {}
    an = rec.get("analysis") or {}
    ev = rec.get("evidence") or {}
    print("== 记录分析输入: %s" % rec.get("recordId"))
    print("  组件/流水线 : %s / %s（%s）" % (hit[0]["component"], hit[0]["pipeline"],
                                            rec.get("pipelineName")))
    print("  构建号/状态 : #%s %s（失败任务类型 %s）"
          % (rec.get("buildNumber"), rec.get("status"), rec.get("failedTaskType") or "--"))
    print("  起止时间    : %s → %s（归属日期 %s，按执行结束时间）"
          % (rec.get("startedAt") or "--", rec.get("endedAt") or "--", rec.get("date")))
    print("  指标        : %s（track=%s）" % (lights_text(rec), (rec.get("light") or {}).get("track")))
    print("  归档分类    : %s（规则置信度 %s，页面不展示；定性列按 AI 根因正文推可能性）"
          % (v.get("category"), v.get("confidence")))
    for b in (v.get("basis") or [])[:SHOW_LIST_MAX]:
        print("    · %s" % cut(b, SHOW_TEXT_MAX))
    print("  AI 状态     : %s（pending=待分析 / na=不做分析）" % an.get("status"))
    print("  计数        : %s" % json.dumps(rec.get("counts") or {}, ensure_ascii=False))
    missing = ev.get("missing") or []
    if missing:
        print("  证据缺口    : %s" % ", ".join(missing))
    tasks = rec.get("tasks") or []
    print("  任务/证据   : %d 个" % len(tasks))
    for t in tasks[:SHOW_LIST_MAX]:
        print("    · [%s] %s | 日志 %s | 错误行 %d"
              % (t.get("type"), t.get("jobName"), t.get("logFile") or "--",
                 len(t.get("errorLines") or [])))
    errs = rec.get("errorMessages") or []
    print("  错误信息    : %d 条（行号可直接回日志原文核对）" % len(errs))
    for e in errs[:SHOW_LIST_MAX]:
        print("    · %s%s | %s" % (e.get("source") or "", ":" + str(e.get("line") or ""),
                                   cut(e.get("text") or e.get("message") or "", SHOW_TEXT_MAX)))
    cases = rec.get("cases") or []
    if cases:
        print("  失败用例    : %d 条" % len(cases))
        for c in cases[:SHOW_LIST_MAX]:
            first = ((c.get("errorMessages") or [{}])[0] or {})
            print("    · %s [%s] %s" % (c.get("name"), c.get("state"),
                                        cut(first.get("text") or first.get("message") or "",
                                            SHOW_TEXT_MAX)))
    print("  记录文件    : %s（全文，含完整证据清单）" % rel(hit[0]["file"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="列出待 AI 分析的记录 / 单条记录的分析输入")
    ap.add_argument("--date", help="归档日 yyyy-mm-dd（默认最近一个归档日）")
    ap.add_argument("--all", action="store_true", help="全部归档日")
    ap.add_argument("--status", default="pending",
                    help="analysis.status 过滤：pending(默认) / na / all")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help="最多显示条数（0=全部，默认 %d）" % DEFAULT_LIMIT)
    ap.add_argument("--show", help="打印单条记录的分析输入（recordId 或构建号）")
    ap.add_argument("--json", help="把清单另存为 JSON（相对技能根目录或绝对路径）")
    ap.add_argument("--strict", action="store_true",
                    help="严格模式：仍有待分析记录时以退出码 2 结束（用于校验流程是否真跑完）")
    args = ap.parse_args()
    if args.status in ("all", "*"):
        args.status = ""
    return show_one(args) if args.show else cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
