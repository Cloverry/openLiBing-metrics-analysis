# -*- coding: utf-8 -*-
"""AI 根因结论写回（纯脚本，第 8 步的唯一出口）。

AI 只产出一份很小的结论 JSON，本脚本负责把它写回归档：
  <日>/<组件>/<流水线>/records/run_<构建号>.json   ← 页面真正读取的单条记录
  <日>/<组件>/<流水线>/step_6_records.json         ← 同目录汇总副本（保持一致）
写回后不需要重采集，重生成页面即可：python run_pipeline.py --only step9

结论文件格式（两种都接受）::

  {"items": [{"recordId": "Ascend-CANN/ops-math#136",
              "keyError": "最有价值的那条报错原文（AI 从日志/报告里筛出来的失败点）",
              "rootCause": "根因正文：单一成因就 1~2 句「在哪失败 → 为什么 → 所以谁去修」；"
                           "只有成因确有共性且分属多类时才分段写「第 N 类（M 个用例，共性）：」",
              "advice": "动手做什么（换凭据 / 改配置 / 修代码）\\n\\n验证方式：复跑什么、通过标准是什么",
              "cites": ["job_logs/01_ops-math_136_test.log:173"],
              "source": "agent"}]}

  [ {...}, {...} ]            # 裸数组等价于 items

正文写法（页面只展示 rootCause / advice 两段，不再有「出处」块）：
  · 不写报错码（DEV-CODECI-35002、error code is 4 之类的码对读者没有信息量，写它代表的机制）；
  · 不写 文件名:行号 —— 出处只放 cites，页面的「证据」弹窗首行「AI 定位」会用它定位原始行；
  · 单一成因不要拆成「第一类/第二类」，一条能说清就写一条；页面渲染时还会再过一遍 rcBody
    兜底剥掉残留的封装码与 file:line，但正文里本来就不该出现。

possibilities（可选，仅归档保留，页面已不再读）：
  · 页面「定性」列不再用这份字段，也不再回退脚本规则定性（verdict.category + 规则置信度）；
  · 现在页面直接从 rootCause 正文里推：正文按空行分段，每段形如
    「第一类（N 条，…）：…更像 X，而非 Y」，就用「N 条」当权重归一成百分比，按高到低最多 3 条；
  · 不分段的单一成因显示成一条 100%；正文里一条都抽不出来时，定性列显示「待 AI 根因分析」。

用法:
  python tools/write_analysis.py --file out/state/analysis_result.json
  python tools/write_analysis.py --file ... --dry-run     # 只校验不落盘
  python tools/write_analysis.py --file ... --date 2026-11-06   # 只在指定归档日内找
  python tools/write_analysis.py --file ... --force       # 允许覆盖已 done 的旧结论（重分析）

纪律：
  · 只改 analysis 字段，记录其余内容一律不动（证据/定性/计数保持原样）；
  · rootCause 为空、recordId 找不到、目标记录不是 pending（且未给 --force）时报错并整条跳过，不静默写入；
  · keyError 必须能落到 cites 指向的那一行日志里：cites 全部可读、却一行都对不上时报错并跳过
    （页面「错误信息」列展示的就是 keyError，对不上证据等于给不出出处）；
  · 写盘先写临时文件再替换，避免中断留下半个 JSON。
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.paths import (ARCHIVE_ROOT, iter_archive_days,  # noqa: E402
                       iter_scoped_pipelines, rel)


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
    """recordId -> (记录文件, 所在流水线目录)；同时带上 analysis.status 供校验。

    只索引当日 _day.json 口径内的目录：旧版本/改名前残留的目录即使有同 recordId 的记录，
    也不写回，避免结论落到不在展示口径里的历史副本上。
    """
    idx = {}
    for d in dates:
        day_dir = ARCHIVE_ROOT / d[:4] / d[:7] / d
        if not day_dir.is_dir():
            continue
        for comp_name, pipe_name, pipe in iter_scoped_pipelines(day_dir):
            rd = pipe / "records"
            if not rd.is_dir():
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


def norm_possibilities(raw):
    """可能性分布归一化：{cause, prob} → 按 prob 降序、最多 3 条、prob 压到 0~1。

    页面的「定性」列直接渲染这份数据（百分数由前端换算），所以这里必须收拾干净：
      · prob 支持 0.62 / 62 / "62%" 三种写法；
      · 同一句原因重复出现只留一条，避免「62% 凭证失效 / 38% 凭证失效」这种自相矛盾；
      · 只给前两条主要可能（和 < 1）是正常的，不强行拉满到 100%；超过 1 则按比例归一。
    """
    if isinstance(raw, dict):
        raw = [{"cause": k, "prob": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for p in raw:
        if isinstance(p, str):
            cause, prob = p, None
        elif isinstance(p, dict):
            cause = p.get("cause") or p.get("name") or p.get("reason") or p.get("原因")
            prob = p.get("prob", p.get("probability", p.get("pct", p.get("可能性"))))
        else:
            continue
        cause = str(cause or "").strip()
        if not cause:
            continue
        try:
            num = float(str(prob).replace("%", "").replace("％", "").strip())
        except (TypeError, ValueError):
            continue                      # 没给百分比的可能性没法排序，直接丢
        if num > 1:
            num /= 100.0
        num = max(0.0, min(1.0, num))
        if (cause, round(num, 3)) in seen:
            continue
        seen.add((cause, round(num, 3)))
        out.append({"cause": cause, "prob": round(num, 3)})
    out.sort(key=lambda x: -x["prob"])
    out = out[:3]
    total = sum(x["prob"] for x in out)
    if total > 1.0 + 1e-6:
        out = [{"cause": x["cause"], "prob": round(x["prob"] / total, 3)} for x in out]
    return out


CITE_RE = re.compile(r"^(?P<path>.+?):L?(?P<l1>\d+)(?:\s*[-~]\s*L?(?P<l2>\d+))?$")


def _squash(s):
    """比对用的归一化：去掉全部空白（日志里的空格/换行宽度常被 AI 改写）。"""
    return re.sub(r"\s+", "", str(s or ""))


def _loose_hit(a, b):
    """a、b 是否指向同一句话：包含，或前 24 个字符完全一致（AI 常把行尾截断/改写）。"""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 24


def read_cite(base, cite):
    """读 cites 里的「文件:行号」，返回 (原文, 是否可读)。读不到一律 (None, False)。"""
    m = CITE_RE.match(str(cite or "").strip())
    if not m:
        return None, False
    rel_path = m.group("path").strip().strip('"').strip("'")
    l1 = int(m.group("l1"))
    l2 = int(m.group("l2") or l1)
    if l2 < l1 or l2 - l1 > 50:            # 只读具体行，别把整份日志拖进来
        l2 = l1
    try:
        p = (Path(base) / rel_path).resolve()
        p.relative_to(Path(base).resolve())   # 只允许流水线目录内的相对路径
    except (ValueError, OSError):
        return None, False
    if not p.is_file():
        return None, False
    try:
        buf = []
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            for no, line in enumerate(fh, 1):
                if no > l2:
                    break
                if no >= l1:
                    buf.append(line)
    except OSError:
        return None, False
    return "\n".join(buf), True


def check_key_error(key_err, cites, base):
    """错误信息列的 keyError 能不能在 cites 指向的行里找到。

    返回 (ok, 说明)：cites 为空 → (False, '无出处')；全部不可读 → (True, '不可核验')；
    可读的行里有一行对得上 → (True, '命中');否则 (False, '对不上')。
    """
    if not key_err:
        return True, "无 keyError"
    if not cites:
        return False, "无 cites"
    nke = _squash(key_err)
    readable = skipped = 0
    for c in cites:
        text, got = read_cite(base, c)
        if not got:
            skipped += 1
            continue
        readable += 1
        if _loose_hit(nke, _squash(text)):
            return True, "命中 %s" % c
    if not readable:
        return True, "cites 均不可核验（%d 条，日志可能已清理）" % skipped
    return False, "对不上 %d 条可读引用（另有 %d 条不可读）" % (readable, skipped)


def apply_one(item, idx, dry_run, now, force=False):
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
    if hit["status"] != "pending" and not force:
        return False, ("%s: analysis.status=%s（只有 pending 才需要写回；重分析请加 --force）"
                       % (rid, hit["status"]))
    rid = hit["recordId"]

    rec = load_json(hit["file"])
    # 已被重分析覆盖过的记录，保留第一次的分析时间，便于区分「原始分析」与「重分析」
    old = rec.get("analysis") or {}
    key_err = str(item.get("keyError") or "").strip() or None
    cites = [str(c).strip() for c in (item.get("cites") or []) if str(c).strip()]
    # 页面的「错误信息」列展示的就是 keyError，用户点「证据」必须能在同一行日志里找到它，
    # 所以写回前先对账：cites 全部可读却一行都对不上，说明 AI 指错了位置（或编了报错），整条跳过
    good, why = check_key_error(key_err, cites, hit["dir"])
    if not good:
        return False, ("%s: keyError 与 cites 对不上（%s）——错误信息列会展示这句话，"
                       "必须能在引用的日志行里找到，请修正 keyError/cites 后重写" % (rid, why))
    rec["analysis"] = {
        "status": "done",
        # AI 从本次执行的日志/报告里筛出的「最有价值报错点」：页面的错误信息列优先展示它，
        # 归档的 errorMessages 常是一串同构噪音（重试行、多卡重复打印、封装码），不适合直接当结论
        "keyError": key_err,
        "rootCause": rc,
        "advice": str(item.get("advice") or "").strip() or None,
        "cites": cites,
        # 定性列用：根因分析里的各种可能性 + 准确度百分比（已归一成 0~1、降序、最多 3 条）
        "possibilities": norm_possibilities(item.get("possibilities")),
        "keyErrorCheck": why,
        "updatedAt": now,
        "firstAnalyzedAt": old.get("firstAnalyzedAt") or old.get("updatedAt") or now,
        "revisions": int(old.get("revisions") or (1 if old else 0))
                     + (1 if (force and old) else (0 if old else 1)),
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
    return True, "%s: 已写回（%d 字根因 / %d 条引用 / %d 种可能性 / keyError %s）" % (
        rid, len(rc), len(rec["analysis"]["cites"]),
        len(rec["analysis"]["possibilities"]), why)


def main():
    ap = argparse.ArgumentParser(description="把 AI 根因结论写回归档记录")
    ap.add_argument("--file", required=True, help="AI 产出的结论 JSON")
    ap.add_argument("--date", help="只在指定归档日 yyyy-mm-dd 内查找 recordId")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不落盘")
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖 analysis.status=done 的旧结论（按新格式重分析旧数据时用）")
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
        good, msg = apply_one(item, idx, args.dry_run, now, force=args.force)
        print(("  ok  " if good else "  !!  ") + msg)
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print("== 写回 %d 条，失败 %d 条%s" % (ok, fail, "（dry-run，未落盘）" if args.dry_run else ""))
    if ok and not args.dry_run:
        print("== 下一步：python run_pipeline.py --only step9   # 重生成页面即可，无需重新采集")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
