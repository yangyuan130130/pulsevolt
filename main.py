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
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from core.paths import app_dir, ensure_assets, ensure_runtime_dirs

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


def http_ok(port: int, path: str, timeout: float = 2.5) -> bool:
    """探测本地服务是否真能把页面给出来（不只是端口在监听）。

    必须显式禁用代理：系统若配了 http_proxy，urllib 会把 127.0.0.1 也带过去。
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def kill_pid(pid: int) -> bool:
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def alert(message: str, title: str = "电之脉动") -> None:
    """windowed 模式没有控制台，出问题只能弹窗。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000010)
    except Exception:
        pass


def existing_instance() -> dict | None:
    """单实例：已经在跑**且健康**，就只把窗口叫出来。

    这里加了一道健康检查。原因是踩过一次坑：单文件版把自身解压到系统临时
    目录，被清理软件删掉后，进程还在、端口还在监听，但首页永远 404。
    这种「半死」的进程如果被当成正常实例，双击就只会白开一个错误页——
    用户完全没法自救。所以宁可把它结束掉，让本次启动重新解压一份干净的。
    """
    try:
        data = json.loads(INSTANCE.read_text("utf-8"))
        port = int(data.get("port", 0))
    except Exception:
        return None

    if not (port and port_alive(port)):
        return None

    if http_ok(port, "/api/env") and http_ok(port, "/"):
        return data

    # 端口活着但页面出不来 —— 结束它，走全新启动
    pid = int(data.get("pid", 0) or 0)
    if pid:
        kill_pid(pid)
        time.sleep(1.2)
    try:
        INSTANCE.unlink()
    except OSError:
        pass
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

    # 先把界面文件落到程序旁边，摆脱「临时目录被清理就 404」的老毛病
    ensure_assets(srv.APP_VERSION)

    # 启动自检：界面文件是启动后才会被反复读取的，缺了就等于没法用。
    # 与其开出一个空白窗口让用户猜，不如直接说清楚。
    missing = srv.static_dir() / "index.html"
    if not missing.is_file():
        alert(
            "界面文件缺失，程序无法正常显示。\n\n"
            "通常是系统临时目录被清理软件删掉了。\n"
            "请点确定后，重新双击 PulseVolt.exe（程序会重新解压一份）。\n\n"
            f"缺失位置：\n{missing}"
        )
        cleanup()
        return 3

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
