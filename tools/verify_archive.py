# -*- coding: utf-8 -*-
"""归档自检 —— 逐层核对 out/archive/ 是否「每条结论都能落到证据」。

用途: 归档完成后或交付前跑一遍，把可被挑战的点提前暴露：
  1) 结构层：<年>/<年-月>/<日>/<组件>/<流水线>/ 目录命名是否合规；
  2) 清单层：_day.json / _manifest.json 是否可解析，_manifest 列出的 step 文件是否都存在；
  3) 记录层：_manifest + step_6_records.json 引用的 records/run_*.json 是否都存在，
           每条记录的 date 是否等于所在日期目录（防止日期归属错层）；
  4) 证据层：所有 JSON 里引用的证据相对路径（job_logs/ case_logs/ github_logs/）是否都存在。

用法:
  python tools/verify_archive.py                  # 校验全部归档
  python tools/verify_archive.py --date 2026-11-03
  python tools/verify_archive.py --component MindStudio
  python tools/verify_archive.py --json out/state/verify_archive.json   # 另存机器可读报告
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lib.paths import (ARCHIVE_ROOT, DATE_FIELD, iter_archive_days,  # noqa: E402
                       iter_scoped_pipelines, rel, stale_pipeline_dirs)

EVIDENCE_PREFIX = ("job_logs/", "case_logs/", "github_logs/")
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JSON_SUFFIX = ".json"
# 引用里的行号定位后缀：:L138 / :138 / :L10-L20 / :10-L20
CITE_LINE_RE = re.compile(r":L?\d+(?:-L?\d+)?$")


def split_ref(s):
    """把证据引用拆成 (文件相对路径, 行号定位)：'job_logs/x.log:L138' -> ('job_logs/x.log', 'L138')。"""
    s = str(s or "")
    m = CITE_LINE_RE.search(s)
    if m:
        return s[:m.start()], s[m.start() + 1:]
    return s, ""


def load_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def iter_strings(obj, key=None):
    """递归产出 JSON 结构里的所有字符串。

    `_manifest.json` 的 evidence.renamed[] 里 `from` 是**改名前的原始路径**（归档里
    本来就不该存在），只校验 `to`（改名后的实际文件），否则会自造误报。
    """
    if isinstance(obj, str):
        if key != "from":
            yield obj
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v, key)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_strings(v, k)


def check_pipeline_dir(pdir):
    """校验单个 <组件>/<流水线>/ 目录，返回问题列表。"""
    problems, stats = [], {"records": 0, "evidenceRefs": 0, "evidenceMissing": 0}
    day = pdir.parent.parent.name
    man_p = pdir / "_manifest.json"
    if not man_p.exists():
        problems.append({"path": rel(pdir), "issue": "缺少 _manifest.json"})
        return problems, stats
    try:
        man = load_json(man_p)
    except Exception as exc:                                   # noqa: BLE001
        problems.append({"path": rel(man_p), "issue": "清单不可解析: %s" % exc})
        return problems, stats

    if man.get("date") != day:
        problems.append({"path": rel(man_p),
                         "issue": "清单 date=%r 与目录日期 %r 不一致（日期口径应为 %s）"
                                  % (man.get("date"), day, DATE_FIELD)})

    for st in man.get("steps") or []:
        f = pdir / (st.get("file") or "")
        if not st.get("file") or not f.exists():
            problems.append({"path": rel(pdir),
                             "issue": "清单登记的第 %s 步文件缺失: %r"
                                      % (st.get("step"), st.get("file"))})

    for r in man.get("records") or []:
        f = pdir / (r.get("file") or "")
        if not r.get("file") or not f.exists():
            problems.append({"path": rel(pdir),
                             "issue": "清单登记的执行记录缺失: recordId=%r"
                                      % r.get("recordId")})

    # 记录明细：日期必须等于目录日期
    rdir = pdir / "records"
    rec_files = sorted(rdir.glob("run_*.json")) if rdir.exists() else []
    stats["records"] = len(rec_files)
    seen_ids = set()
    for rf in rec_files:
        try:
            rec = load_json(rf)
        except Exception as exc:                               # noqa: BLE001
            problems.append({"path": rel(rf), "issue": "记录不可解析: %s" % exc})
            continue
        rid = rec.get("recordId")
        seen_ids.add(rid)
        if rec.get("date") != day:
            problems.append({"path": rel(rf),
                             "issue": "记录 date=%r 与目录日期 %r 不一致"
                                      % (rec.get("date"), day)})

    listed = {r.get("recordId") for r in man.get("records") or []}
    for rid in sorted(x for x in listed - seen_ids if x):
        problems.append({"path": rel(pdir), "issue": "清单有记录但文件不存在: %s" % rid})
    for rid in sorted(x for x in seen_ids - listed if x):
        problems.append({"path": rel(pdir), "issue": "有记录文件但清单未登记: %s" % rid})

    # 证据层：所有 JSON 里引用的证据路径都要在本目录内存在
    # 引用写法是「相对路径 + 行号定位」：job_logs/x.log:L138 / case_logs/y.html:L1662
    # 行号只是定位指针，不是路径的一部分，校验前必须剥掉，否则会把全部引用误判为缺失。
    refs = {}
    for jf in sorted(pdir.rglob("*" + JSON_SUFFIX)):
        if ".json" != jf.suffix:
            continue
        try:
            payload = load_json(jf)
        except Exception:                                       # noqa: BLE001
            continue
        for s in iter_strings(payload):
            if s.startswith(EVIDENCE_PREFIX):
                base, where = split_ref(s)
                refs.setdefault(base, set()).add(where)
    stats["evidenceRefs"] = len(refs)
    for ref in sorted(refs):
        if not (pdir / ref).exists():
            stats["evidenceMissing"] += 1
            where = sorted(x for x in refs[ref] if x)
            problems.append({"path": rel(pdir),
                             "issue": "证据文件缺失: %s（%d 处引用%s）"
                                      % (ref, len(where) or 1,
                                         "，如 " + where[0] if where else "")})
    return problems, stats


def main():
    ap = argparse.ArgumentParser(description="out/archive 归档自检")
    ap.add_argument("--date", help="只校验某一天（yyyy-mm-dd）")
    ap.add_argument("--component", help="只校验某个开源组件")
    ap.add_argument("--json", help="把报告另存为 JSON（相对路径按仓库根解析）")
    args = ap.parse_args()

    problems = []
    stale = []
    tot = {"days": 0, "pipelines": 0, "records": 0, "evidenceRefs": 0, "evidenceMissing": 0}
    for date, day_dir in iter_archive_days():
        if args.date and date != args.date:
            continue
        tot["days"] += 1
        dj = day_dir / "_day.json"
        if not dj.exists():
            problems.append({"path": rel(day_dir), "issue": "缺少 _day.json"})
        else:
            try:
                load_json(dj)
            except Exception as exc:                           # noqa: BLE001
                problems.append({"path": rel(dj), "issue": "日汇总不可解析: %s" % exc})
        if not DATE_DIR_RE.match(day_dir.name):
            problems.append({"path": rel(day_dir), "issue": "日期目录命名不合规"})
        for comp, pipe, pdir in stale_pipeline_dirs(day_dir):
            if not args.component or comp == args.component:
                stale.append(dict(path=rel(pdir), date=date, component=comp, pipeline=pipe))
        for comp, pipe, pdir in iter_scoped_pipelines(day_dir):
            if args.component and comp != args.component:
                continue
            tot["pipelines"] += 1
            ps, stats = check_pipeline_dir(pdir)
            problems += ps
            for k in ("records", "evidenceRefs", "evidenceMissing"):
                tot[k] += stats[k]

    ok = not problems
    print("== 归档自检 ==")
    print("日目录 %d | 流水线目录 %d | 执行记录 %d 条 | 证据引用 %d 个（缺失 %d）"
          % (tot["days"], tot["pipelines"], tot["records"],
             tot["evidenceRefs"], tot["evidenceMissing"]))
    if stale:
        print("注: %d 个目录不在当日 _day.json 口径内（旧版本/改名前残留），已跳过不计: %s"
              % (len(stale), ", ".join(s["path"] for s in stale[:5])
                 + ("…" if len(stale) > 5 else "")))
    print("结论: %s" % ("通过（结构与证据可逐条核对）" if ok else "发现 %d 个问题" % len(problems)))
    for p in problems[:30]:
        print("  !! %s -> %s" % (p["path"], p["issue"]))
    if len(problems) > 30:
        print("  ... 其余 %d 个问题见 JSON 报告" % (len(problems) - 30))

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"totals": tot, "ok": ok, "stale": stale,
                                   "problems": problems},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print("报告: %s" % out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
