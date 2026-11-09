# -*- coding: utf-8 -*-
"""浏览器自举：确保本地 Chrome 已带调试端口启动并停在 openLiBing 看板页。

第 1~5c 步全部通过 CDP 在真实浏览器里跑页面脚本取数（复用浏览器里的登录态），
因此「打开浏览器」这一步也必须自己完成，不能靠人工：

  python run_pipeline.py            → 端口不通时按 config.chrome 自动拉起 Chrome
  python run_pipeline.py --no-launch-browser   → 完全不动浏览器（自己已开好调试端口时用）

判定口径：GET http://127.0.0.1:<port>/json/version 返回 200 即视为「调试端口已就绪」，
此时不再拉起任何进程，直接复用（包括用户手工开的 Chrome）。
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

DEFAULT_START_URL = "https://www.openlibing.com/apps/eCDashboard"
PROBE_TIMEOUT_S = 3
BOOT_TIMEOUT_S = 45


def _port_ready(port):
    """调试端口是否已就绪（有进程在监听且是 Chrome DevTools 协议）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port,
                                    timeout=PROBE_TIMEOUT_S) as r:
            if r.status != 200:
                return False
            json.loads(r.read().decode("utf-8") or "{}")
            return True
    except Exception:
        return False


def _chrome_candidates(cfg):
    """Chrome/Edge 可执行文件候选：config.chrome.chrome_path 优先，其次系统默认安装位置。"""
    ch = cfg.get("chrome") or {}
    ordered = []
    if ch.get("chrome_path"):
        ordered.append(str(ch["chrome_path"]))
    for exe in ("chrome", "msedge"):
        w = shutil.which(exe)
        if w:
            ordered.append(w)
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
             os.environ.get("LOCALAPPDATA")]
    for root in roots:
        if not root:
            continue
        ordered.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        ordered.append(str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
    return [p for p in ordered if p and Path(p).exists()]


def _default_profile_dir():
    """Chrome 默认用户目录（复用日常登录态）；找不到时退回技能内独立目录。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / "Google" / "Chrome" / "User Data"
        if p.exists():
            return str(p)
    from lib.paths import STATE_DIR
    return str(STATE_DIR / "chrome-profile")


def ensure_browser(cfg, start_url=None, on_log=print):
    """确保调试端口可用；必要时拉起 Chrome 并导航到看板。

    返回 True=端口可用（复用或新启动），False=无法拉起（调用方应决定是否继续）。
    端口已就绪时不做任何动作——用户手工开的带调试端口 Chrome 同样适用。
    """
    ch = cfg.get("chrome") or {}
    port = int(ch.get("debug_port", 9222))
    url = start_url or ch.get("start_url") or DEFAULT_START_URL

    if _port_ready(port):
        on_log("== 浏览器: 复用已就绪的调试端口 127.0.0.1:%d" % port)
        return True

    exes = _chrome_candidates(cfg)
    if not exes:
        on_log("!! 浏览器: 未找到 chrome.exe / msedge.exe。")
        on_log("!! 请手工启动: chrome.exe --remote-debugging-port=%d --user-data-dir=\"<用户目录>\" %s"
               % (port, url))
        return False

    profile = str(ch.get("user_data_dir") or "") or _default_profile_dir()
    Path(profile).mkdir(parents=True, exist_ok=True)
    args = [exes[0],
            "--remote-debugging-port=%d" % port,
            "--user-data-dir=%s" % profile,
            "--no-first-run", "--no-default-browser-check",
            "--new-window", url]
    on_log("== 浏览器: 调试端口未开，自动拉起 %s（profile=%s）" % (Path(exes[0]).name, profile))
    try:
        subprocess.Popen(args, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        on_log("!! 浏览器: 启动失败 %s" % exc)
        return False

    deadline = time.time() + BOOT_TIMEOUT_S
    while time.time() < deadline:
        if _port_ready(port):
            on_log("== 浏览器: 调试端口就绪 127.0.0.1:%d，已导航到 %s" % (port, url))
            return True
        time.sleep(1.0)

    on_log("!! 浏览器: %ds 内调试端口仍未就绪 127.0.0.1:%d。" % (BOOT_TIMEOUT_S, port))
    on_log("!! 常见原因: 该 profile 已被一个未开调试端口的 Chrome 占用 —— 请完全退出 Chrome 后重跑，")
    on_log("!! 或在 config.chrome.user_data_dir 指定一个独立目录（首次需在该窗口里登录一次）。")
    on_log("!! 也可手工执行: \"%s\" --remote-debugging-port=%d --user-data-dir=\"%s\" %s"
           % (exes[0], port, profile, url))
    return False
