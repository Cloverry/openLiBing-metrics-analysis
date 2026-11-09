# -*- coding: utf-8 -*-
"""AI 根因结论写回（纯脚本，第 8 步的唯一出口）。

AI 只产出一份很小的结论 JSON，本脚本负责把它写回归档：
  <日>/<组件>/<流水线>/records/run_<构建号>.json   ← 页面真正读取的单条记录
  <日>/<组件>/<流水线>/step_6_records.json         ← 同目录汇总副本（保持一致）
写回后不需要重采集，重生成页面即可：python run_pipeline.py --only step9

结论文件格式（两种都接受）::

  {"items": [{"recordId": "Ascend-CANN/ops-math#136",
              "rootCause": "一句话根因（必须落到报错原文）",
              "advice": "可执行建议",
              "cites": ["job_logs/01_ops-math_136_test.log:173"],
              "source": "agent"}]}

  [ {...}, {...} ]            # 裸数组等价于 items

用法:
  python tools/write_analysis.py --file out/state/analysis_result.json
  python tools/write_analysis.py --file ... --dry-run     # 只校验不落盘
  python tools/write_analysis.py --file ... --date 2026-11-06   # 只在指定归档日内找

纪律：
  · 只改 analysis 字段，记录其余内容一律不动（证据/定性/计数保持原样）；
  · rootCause 为空、recordId 找不到、目标记录不是 pending 时报错并整条跳过，不静默写入；
  · 写盘先写临时文件再替换，避免中断留下半个 JSON。
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.paths import ARCHIVE_ROOT, iter_archive_days, rel  # noqa: E402


def load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save_json_atomic(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def norm_id(s):
    """recordId 归一化：真身是「组件|流水线#构建号」，同时容忍 / 与空白写法。"""
    return str(s or "").replace("/", "|").replace(" ", "")


def build_index(dates):
    """recordId -> (记录文件, 所在流水线目录)；同时带上 analysis.status 供校验。"""
    idx = {}
    for d in dates:
        day_dir = ARCHIVE_ROOT / d[:4] / d[:7] / d
        if not day_dir.is_dir():
            continue
        for comp in sorted(day_dir.iterdir()):
            if not comp.is_dir() or comp.name.startswith("_"):
                continue
            for pipe in sorted(comp.iterdir()):
                rd = pipe / "records"
                if not pipe.is_dir() or not rd.is_dir():
                    continue
                for f in sorted(rd.glob("run_*.json")):
                    try:
                        rec = load_json(f)
                    except (ValueError, OSError) as exc:
                        print("!! 记录读取失败 %s: %s" % (rel(f), exc))
                        continue
                    rid = str(rec.get("recordId") or "")
                    if rid:
                        idx[norm_id(rid)] = {"recordId": rid, "file": f, "dir": pipe,
                                             "status": (rec.get("analysis") or {}).get("status")}
    return idx


def normalize(payload):
    """接受 {"items": [...]} / {"analysis": [...]} / 裸数组，统一成 list。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "analysis", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    raise ValueError("结论文件格式不对：需要 {\"items\": [...]} 或裸数组")


def apply_one(item, idx, dry_run, now):
    """写回一条结论，返回 (ok, 说明)。"""
    rid = str(item.get("recordId") or item.get("id") or "").strip()
    if not rid:
        return False, "缺少 recordId"
    rc = str(item.get("rootCause") or "").strip()
    if not rc:
        return False, "%s: rootCause 为空（不允许写空结论）" % rid
    hit = idx.get(norm_id(rid))
    if not hit:
        return False, "%s: 归档里找不到该 recordId" % rid
    if hit["status"] != "pending":
        return False, "%s: analysis.status=%s（只有 pending 才需要写回）" % (rid, hit["status"])
    rid = hit["recordId"]

    rec = load_json(hit["file"])
    rec["analysis"] = {
        "status": "done",
        "rootCause": rc,
        "advice": str(item.get("advice") or "").strip() or None,
        "cites": [str(c) for c in (item.get("cites") or []) if str(c).strip()],
        "updatedAt": now,
        "source": str(item.get("source") or "agent"),
    }
    if not dry_run:
        save_json_atomic(hit["file"], rec)

        # 同目录汇总副本保持一致（页面不读它，但避免两份数据打架）
        agg = hit["dir"] / "step_6_records.json"
        if agg.exists():
            try:
                box = load_json(agg)
                for i, r in enumerate(box.get("records") or []):
                    if str(r.get("recordId")) == rid:
                        box["records"][i] = {**r, "analysis": rec["analysis"]}
                        break
                save_json_atomic(agg, box)
            except (ValueError, OSError) as exc:
                print("!! 汇总副本更新失败 %s: %s" % (rel(agg), exc))
    return True, "%s: 已写回（%d 字根因 / %d 条引用）" % (rid, len(rc),
                                                        len(rec["analysis"]["cites"]))


def main():
    ap = argparse.ArgumentParser(description="把 AI 根因结论写回归档记录")
    ap.add_argument("--file", required=True, help="AI 产出的结论 JSON")
    ap.add_argument("--date", help="只在指定归档日 yyyy-mm-dd 内查找 recordId")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不落盘")
    args = ap.parse_args()

    p = Path(args.file)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        print("!! 结论文件不存在: %s" % p)
        return 1
    try:
        items = normalize(load_json(p))
    except ValueError as exc:
        print("!! %s" % exc)
        return 1
    if not items:
        print("!! 结论文件里没有条目")
        return 1

    days = [args.date] if args.date else [d for d, _ in iter_archive_days()]
    idx = build_index(days)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ok = fail = 0
    for item in items:
        good, msg = apply_one(item, idx, args.dry_run, now)
        print(("  ok  " if good else "  !!  ") + msg)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print("== 写回 %d 条，失败 %d 条%s" % (ok, fail, "（dry-run，未落盘）" if args.dry_run else ""))
    if ok and not args.dry_run:
        print("== 下一步：python run_pipeline.py --only step9   # 重生成页面即可，无需重新采集")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
