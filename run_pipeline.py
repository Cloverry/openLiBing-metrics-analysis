# -*- coding: utf-8 -*-
"""openLiBing 夜间流水线根因分析：唯一入口 —— 第 1 段（第 1~7 步采集归档）+ 第 3 段（第 9 步生成展示页）。

本 skill 是「取数 → AI 根因 → 出页」的完整流程，共三段，不可中途汇报完成:
  第 1 段 本脚本（默认参数）取数归档 + 出静态壳；退出码 0 表示采集完成，必须接着做第 2 段。
  第 2 段 agent 执行（脚本不做）: tools/list_pending.py → 分析 → tools/write_analysis.py 原子写回。
  第 3 段 本脚本 --only step9 重出页面。
  完成判据: list_pending.py 待分析 0 条 + 已执行 --only step9 + 无报错。
退出码 42 表示当天无任何待分析数据，是唯一允许提前结束的情况。

用法:
  python run_pipeline.py                                    # 全流程：默认取「前一个工作日」单日、全部产品、全部流水线（含达标）
  python run_pipeline.py --start 2026-10-21 --end 2026-10-27 --products MindStudio,Ascend-CANN
  python run_pipeline.py --from step4                        # 从指定步骤续跑（前面步骤的产物直接复用）
  python run_pipeline.py --only step2                        # 只跑单步（也支持 --only 4 / --only testreport）
  python run_pipeline.py --only step9                        # 只重生成展示页 out/html/
  python run_pipeline.py --fresh                             # 跳过增量缓存，全部重新抓取（结果仍写回缓存）
  python run_pipeline.py --retries 2                         # 每步失败自动重试次数（默认 1，无人值守一把过）
  python run_pipeline.py --serve                             # 起本地 http 服务预览展示页（默认 8765 端口）
  python run_pipeline.py --no-launch-browser                 # 采集前不自动拉起 Chrome（已手工开好调试端口时）

浏览器自举: 采集步骤需要 CDP 调试端口（默认 9222）。端口不通时按 config.chrome 自动拉起
  Chrome 并导航到看板页，无需人工「先打开浏览器再跑」；端口已就绪则直接复用（含手工开的）。

产物（数据与 HTML 完全隔离）:
  out/archive/<年>/<年-月>/<日>/<开源组件名>/<流水线名>/   ← 第 7 步逐层归档（最终交付物，只增不删）
      _manifest.json / step_1_lights.json … step_6_records.json / records/run_<构建号>.json
      job_logs/ case_logs/ github_logs/                     ← 证据原文
  out/html/   第 9 步产出的静态壳 index.html / detail.html（不含业务数据，运行时读 out/archive/）
  out/work/    第 1~6 步中间产物（每次重跑覆盖写入，归档后即可丢弃）
  out/state/   run_pipeline.log、cache/collection.json

口径: 所有日期归属、筛选、归档一律取【执行结束时间】
（openLiBing N2 pipelineRunEndtime / GitHub run.updated_at），与页面日期筛选组件一致。

输出纪律（无人值守防中断）:
  各步骤的完整 stdout/stderr 全部落盘 out/state/run_pipeline.log（每次全流程覆盖写入），
  终端只透传步骤头与错误关键行——避免 agent 工具输出超长触发「请输入继续」阻塞。
  需要看实时进度: Get-Content out\\state\\run_pipeline.log -Tail 50 -Wait

增量模式（默认开启）: 上一次已取证的运行直接复用（构建列表/用例日志/全量日志/解析结果），
  仅补抓上次之后的新运行；复用校验优先命中归档目录，其次中间产物目录。
数据保留: 不设滚动清理、不删任何采集数据与证据；out/archive/ 只增不删，
  每日归档长期保留供回看（第 0 步滚动清理已移除）。
第 8 步（第 2 段 AI 根因分析）由执行本 skill 的 agent 按 agent/agent_task.md 逐条记录完成，不是脚本；
  它前面没有「可以停下」的分界，采集完成后必须立即执行，并以 --only step9 收尾。
"""
import argparse
import collections
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lib.paths import ARCHIVE_ROOT, HTML_DIR, OUT_DIR, STATE_DIR, WORK_DIR  # noqa: E402
from lib.browser import ensure_browser  # noqa: E402
from lib.cdp_exec import (date_range_source, load_config,  # noqa: E402
                          normalize_date_range)

STEPS = [
    ("step_1_nightly", "第 1 步 夜间统计与待分析圈定"),
    ("step_2_runs", "第 2 步【N2】执行记录圈定"),
    ("step_3_tasks", "第 3 步【T4】失败任务分流（github 平台转第 5c 步）"),
    ("step_4_testreport", "第 4 步【T6】测试用例取证"),
    ("step_5_logs", "第 5 步【T5】任务日志取证"),
    ("step_5b_subtasks", "第 5b 步【T7】buildflow 子任务下钻"),
    ("step_5c_github", "第 5c 步【GitHub】github 平台运行日志取证"),
    ("step_6_records", "第 6 步 证据合并 → 逐条执行记录"),
    ("step_7_archive", "第 7 步 逐层归档（日期/组件/流水线）"),
    ("step_9_pages", "第 9 步 生成展示页（静态壳 → out/html/，数据运行时读归档）"),
]
EXIT_EARLY_OK = 42
KEY_LINE_MAX = 40          # 单步透传关键行上限，超出只计数防刷屏
TAIL_LINES_ON_FAIL = 30    # 步骤最终失败时打印的日志尾部行数
LOG_NAME = "run_pipeline.log"
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8765
# 需要真实浏览器（CDP 跑页面脚本取数）的步骤；其余步骤纯本地读写，不碰浏览器
NEEDS_BROWSER = {
    "step_1_nightly", "step_2_runs", "step_3_tasks", "step_4_testreport",
    "step_5_logs", "step_5b_subtasks", "step_5c_github",
}


def step_key(name):
    """步骤短号：step_5b_subtasks -> 5b。"""
    m = re.match(r"step_(\d+[a-z]?)", name)
    return m.group(1).lower() if m else name.lower()


def find_steps(arg):
    """按 4 / step4 / step_4 / step_4_testreport / testreport 定位步骤（精确优先，其次前缀/包含）。"""
    a = re.sub(r"^step[_-]?", "", (arg or "").strip().lower())
    if not a:
        return []
    exact = [(n, d) for n, d in STEPS
             if step_key(n) == a or n.lower() == arg.strip().lower()]
    if exact:
        return exact
    return [(n, d) for n, d in STEPS if step_key(n).startswith(a) or a in n.lower()]


def is_key_line(line):
    """需要透传到终端的行：步骤头（== 开头非纯分隔线）与显式告警（!! 开头）、Python 异常头。"""
    s = line.strip()
    if not s:
        return False
    if set(s) == {"="}:
        return False
    return s.startswith("==") or s.startswith("!!") or "Traceback (most recent call last)" in s


def run_step(name, desc, log_f):
    """执行单步：全部输出 tee 进日志文件，终端只透传关键行；返回 (退出码, 尾部行缓存)。"""
    log_f.write("\n" + "=" * 60 + "\n[%s] %s\n" % (time.strftime("%H:%M:%S"), desc)
                + "=" * 60 + "\n")
    log_f.flush()
    tail = collections.deque(maxlen=TAIL_LINES_ON_FAIL)
    n_key, n_total = 0, 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "steps" / (name + ".py"))],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in proc.stdout:
        line = line.rstrip("\n")
        n_total += 1
        log_f.write(line + "\n")
        log_f.flush()
        tail.append(line)
        if is_key_line(line):
            n_key += 1
            if n_key <= KEY_LINE_MAX:
                print("  | " + line, flush=True)
    proc.wait()
    if n_total > n_key:
        log_f.write("[%s] 本步共输出 %d 行（终端仅透传关键 %d 行，完整输出见本日志）\n"
                    % (name, n_total, min(n_key, KEY_LINE_MAX)))
        log_f.flush()
    return proc.returncode, tail


def serve(port=SERVE_PORT, open_browser=True):
    """起本地静态服务预览 out/html/（file:// 下浏览器会拦截 fetch 读归档 JSON）。"""
    import functools
    import http.server
    import socketserver
    import threading
    import webbrowser

    if not (HTML_DIR / "index.html").exists():
        print("!! out/html/index.html 不存在，请先执行: python run_pipeline.py --only step9")
        return 1

    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                               directory=str(OUT_DIR))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = http.server.ThreadingHTTPServer((SERVE_HOST, port), handler)
    except OSError as exc:
        print("!! 端口 %d 不可用（%s），请换端口: python run_pipeline.py --serve --port 8766"
              % (port, exc))
        return 1

    url = "http://%s:%d/html/index.html" % (SERVE_HOST, port)
    print("== 展示页本地服务已启动（Ctrl+C 停止）")
    print("   首页   : %s" % url)
    print("   二级页 : http://%s:%d/html/detail.html?key=<流水线key>" % (SERVE_HOST, port))
    print("   根目录 : %s" % OUT_DIR)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n== 服务已停止")
    finally:
        httpd.server_close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="覆盖 config 的起始日期 yyyy-mm-dd")
    ap.add_argument("--end", help="覆盖 config 的结束日期 yyyy-mm-dd")
    ap.add_argument("--products", help="覆盖 config 的产品，逗号分隔；all 表示全部")
    ap.add_argument("--fresh", action="store_true",
                    help="跳过增量缓存复用，全部重新抓取（结果仍写回缓存）")
    ap.add_argument("--from", dest="from_step", help="从指定步骤开始（含），如 step4")
    ap.add_argument("--only", help="只执行单个步骤，如 step2")
    ap.add_argument("--retries", type=int, default=1,
                    help="每步失败后的自动重试次数（默认 1，适配无人值守）")
    ap.add_argument("--serve", action="store_true",
                    help="起本地 http 服务预览 out/html/（避免 file:// 拦截 fetch 读归档）")
    ap.add_argument("--port", type=int, default=SERVE_PORT,
                    help="--serve 的监听端口（默认 %d）" % SERVE_PORT)
    ap.add_argument("--no-browser", action="store_true", help="--serve 时不自动打开浏览器")
    ap.add_argument("--no-launch-browser", action="store_true",
                    help="采集前不自动拉起 Chrome（自己已开好调试端口时用）")
    args = ap.parse_args()

    if args.serve:
        sys.exit(serve(args.port, open_browser=not args.no_browser))

    if args.start:
        os.environ["RCA_START"] = args.start
    if args.end:
        os.environ["RCA_END"] = args.end
    if args.products:
        os.environ["RCA_PRODUCTS"] = "" if args.products.strip().lower() == "all" else args.products
    if args.fresh:
        os.environ["RCA_FRESH"] = "1"

    if args.only:
        todo = find_steps(args.only)
        if not todo:
            print("未知步骤: %s（可选: %s）" % (args.only, ", ".join(n for n, _ in STEPS)))
            sys.exit(1)
    else:
        todo = STEPS
        if args.from_step:
            hit = find_steps(args.from_step)
            if not hit:
                print("未知起始步骤: %s（可选: %s）"
                      % (args.from_step, ", ".join(n for n, _ in STEPS)))
                sys.exit(1)
            idx = next(i for i, (n, _) in enumerate(STEPS) if n == hit[0][0])
            todo = STEPS[idx:]

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = STATE_DIR / LOG_NAME
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)
    log_f.write("run_pipeline 启动 %s  参数: start=%s end=%s products=%s fresh=%s retries=%d\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), args.start or "(config)",
                   args.end or "(config)", args.products or "(config)",
                   args.fresh, args.retries))
    print("完整日志: out/state/%s（终端只显示关键行）" % LOG_NAME, flush=True)
    print("中间产物: out/work/  |  归档交付: out/archive/  |  状态缓存: out/state/", flush=True)

    cfg_now = load_config()
    d_start, d_end = normalize_date_range(cfg_now)
    line = "目标区间: %s ~ %s（%s）" % (d_start, d_end, date_range_source(cfg_now))
    print("== " + line, flush=True)
    log_f.write(line + "\n")
    log_f.flush()

    if any(name in NEEDS_BROWSER for name, _ in todo) and not args.no_launch_browser:
        if not ensure_browser(cfg_now):
            print("!! 浏览器未就绪：采集步骤大概率失败，可先 --only step9 预览已有数据", flush=True)
            log_f.write("浏览器未就绪，继续尝试执行（步骤内会再次报错并给出修复提示）\n")
            log_f.flush()

    t_all = time.time()
    for name, desc in todo:
        print("\n>>> %s（%s）" % (desc, name), flush=True)
        t0 = time.time()
        attempt, rc, tail = 0, None, None
        while True:
            attempt += 1
            rc, tail = run_step(name, desc, log_f)
            if rc in (0, EXIT_EARLY_OK):
                break
            if attempt <= args.retries:
                print(">>> %s 第 %d 次失败（退出码 %d），%.0fs 后自动重试..." %
                      (name, attempt, rc, 5.0), flush=True)
                log_f.write("[%s] 第 %d 次尝试失败（退出码 %d），自动重试\n" % (name, attempt, rc))
                time.sleep(5)
                print(">>> %s 重试 %d/%d ..." % (name, attempt, args.retries), flush=True)
                continue
            break
        print(">>> %s 完成，耗时 %.1fs" % (name, time.time() - t0), flush=True)

        if rc == EXIT_EARLY_OK:
            print("\n流程正常提前结束（无待分析数据），中间产物已留档 %s（总耗时 %.1fs）"
                  % (WORK_DIR, time.time() - t_all))
            log_f.close()
            return
        if rc != 0:
            print("\n!! %s 最终失败（已尝试 %d 次，退出码 %d）。日志末尾 %d 行：" %
                  (desc, attempt, rc, len(tail)), flush=True)
            for line in tail:
                print("  | " + line, flush=True)
            print("!! 修复后从该步骤续跑: python run_pipeline.py --from %s" % name)
            print("!! 完整日志: %s" % log_path)
            log_f.close()
            sys.exit(rc)

    names = [n for n, _ in todo]
    did_collect = "step_1_nightly" in names
    did_pages = "step_9_pages" in names

    print("\n" + "=" * 60)
    if did_collect:
        print("第 1 段完成：取数与归档已就绪，总耗时 %.1fs。" % (time.time() - t_all))
    elif did_pages:
        print("第 3 段完成：展示页已重出，总耗时 %.1fs。" % (time.time() - t_all))
    else:
        print("所选步骤已完成（%s），总耗时 %.1fs。" % (", ".join(names), time.time() - t_all))
    print("  归档目录（逐层可查）: %s" % ARCHIVE_ROOT)
    print("  单条记录: <年>/<年-月>/<日>/<开源组件名>/<流水线名>/records/run_<构建号>.json")
    print("  展示页（静态壳，数据运行时读归档）: %s" % (HTML_DIR / "index.html"))
    print("  预览: python run_pipeline.py --serve  → http://%s:%d/html/index.html"
          % (SERVE_HOST, SERVE_PORT))
    print("  完整采集日志: %s" % log_path)
    print("-" * 60)
    if did_collect:
        print("!! 流程还没跑完（第 1/3 段）。绝不能在这里汇报完成，必须接着做第 2、3 段：")
        print("  第 2 段 AI 根因分析（agent 执行，脚本不做）:")
        print("    1) python tools/list_pending.py            # 脚本列出待分析记录与证据摘要")
        print("    2) 按 agent/agent_task.md 逐条分析，写出 out/state/analysis_result.json")
        print("    3) python tools/write_analysis.py --file out/state/analysis_result.json --dry-run")
        print("    4) python tools/write_analysis.py --file out/state/analysis_result.json")
        print("  第 3 段 重出页面（脚本执行）:")
        print("    5) python run_pipeline.py --only step9     # 无需重新采集")
        print("  完成判据: list_pending.py --strict 退出码 0 + 已执行 --only step9 + 无报错。")
    elif did_pages:
        print("   收尾复核: python tools/list_pending.py --strict   # 退出码 0 才算跑完（非 0 = 还有待分析记录）")
    print("=" * 60)
    log_f.close()


if __name__ == "__main__":
    main()
