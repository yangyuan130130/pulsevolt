"""电之脉动 · PulseVolt 2.0 —— 入口。

双击后的行为：起本地服务 → 开一个独立窗口（Edge/Chrome 的 app 模式，
没有地址栏和标签页，看起来就是个桌面应用）→ 托盘常驻。
关掉窗口程序不会退出，托盘图标还在；要彻底退出用托盘菜单或页面里的「退出」。

命令行：
    PulseVolt.exe                 双击的默认行为（开窗口 + 托盘）
    PulseVolt.exe --tray          只托盘，不开窗口（给开机自启用）
    PulseVolt.exe --no-window     只服务，不弹窗口
    PulseVolt.exe --port 47820    指定端口（默认随机空闲端口）
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
from pathlib import Path

from core.paths import app_dir, ensure_runtime_dirs

INSTANCE = app_dir() / "instance.json"
CREATE_NO_WINDOW = 0x08000000

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
]


def open_panel(url: str) -> str:
    """用 app 模式开一个无地址栏的窗口；找不到浏览器就退回默认浏览器。"""
    for exe in BROWSERS:
        if not Path(exe).is_file():
            continue
        try:
            subprocess.Popen(
                [exe, f"--app={url}", "--window-size=1320,900", "--window-position=120,80"],
                creationflags=CREATE_NO_WINDOW,
            )
            return "app"
        except Exception:
            continue
    webbrowser.open(url)
    return "browser"


def port_alive(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.35)
        return s.connect_ex(("127.0.0.1", port)) == 0


def existing_instance() -> dict | None:
    """单实例：已经在跑就只把窗口叫出来，不重复起一个托盘。"""
    try:
        data = json.loads(INSTANCE.read_text("utf-8"))
        port = int(data.get("port", 0))
    except Exception:
        return None
    if port and port_alive(port):
        return data
    return None


def cleanup() -> None:
    try:
        INSTANCE.unlink()
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="电之脉动 PulseVolt 2.0")
    parser.add_argument("--panel", action="store_true", help="打开控制台窗口（提权重启时用）")
    parser.add_argument("--tray", action="store_true", help="只驻留托盘，不开窗口")
    parser.add_argument("--no-window", action="store_true", help="只起服务，不弹窗口")
    parser.add_argument("--port", type=int, default=0, help="指定端口（默认随机）")
    args = parser.parse_args()

    ensure_runtime_dirs()

    running = existing_instance()
    if running:
        open_panel(f"http://127.0.0.1:{running['port']}/")
        return 0

    import panel.server as srv

    httpd, port = srv.serve(args.port)
    url = f"http://127.0.0.1:{port}/"
    try:
        INSTANCE.write_text(json.dumps({"pid": os.getpid(), "port": port}), "utf-8")
    except OSError:
        pass

    if not args.tray and not args.no_window:
        open_panel(url)

    from ui.tray2 import run_tray

    quitting = {"done": False}

    def quit_app():
        if quitting["done"]:
            return
        quitting["done"] = True
        try:
            if srv.CTX:
                srv.CTX.capture.stop()
                srv.CTX.manager.stop()
        except Exception:
            pass
        cleanup()
        os._exit(0)

    tray = run_tray(url, srv.CTX.manager, quit_app)   # noqa: F841

    try:
        while True:
            time.sleep(0.8)
    except KeyboardInterrupt:
        quit_app()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        try:
            (app_dir() / "error.log").write_text(traceback.format_exc(), "utf-8")
        except OSError:
            pass
        raise
