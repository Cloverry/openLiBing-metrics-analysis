# -*- coding: utf-8 -*-
"""从 out/ 现有产物构建增量缓存（首次启用增量模式时用，无需重新抓取）。

缓存条目按既有产物的分析区间写入（取 step_1_products.json 的 dateRange），
之后跑 run_pipeline.py 即可复用；本次区间外的旧条目会在下次运行时自动剔除。
用法: python bootstrap_cache.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lib.cache import write_cache_from_outputs
from lib.cdp_exec import OUT_DIR, load_config, load_json

REQUIRED = [
    "step_1_bad_pipelines.json", "step_2_failed_runs.json", "step_3_params.json",
    "step_4_test_evidence.json", "step_4_case_errors.json", "step_5_log_evidence.json",
]


def main():
    cfg = load_config()
    missing = [f for f in REQUIRED if not (OUT_DIR / f).exists()]
    if missing:
        print("缺少上次采集产物，无法建缓存: %s" % ", ".join(missing))
        print("请先完整跑一次 run_pipeline.py（可加 --fresh），缓存会自动生成")
        sys.exit(1)
    dr = load_json(OUT_DIR / "step_1_products.json").get("dateRange") or {}
    start, end = str(dr.get("start") or ""), str(dr.get("end") or "")
    if not start or not end:
        print("step_1_products.json 缺少 dateRange，无法确定既有数据区间")
        sys.exit(1)
    n = write_cache_from_outputs(cfg, OUT_DIR, start, end)
    print("既有产物区间 %s ~ %s" % (start, end))
    print("缓存已建立: 写入 %d 条（完整 %d）| 保留 %d 条 | 剔除区间外 %d 条" % n)
    print("缓存文件: %s" % (OUT_DIR / "cache" / "collection.json"))
    incomplete = [k for k, v in load_json(OUT_DIR / "cache" / "collection.json")["runs"].items()
                  if not v.get("complete")]
    if incomplete:
        print("注意: %d 条上次采集存在错误/证据不全，下次运行会自动重新抓取" % len(incomplete))


if __name__ == "__main__":
    main()
