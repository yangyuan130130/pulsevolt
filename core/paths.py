"""路径解析：区分「源码运行」和「PyInstaller 打包后运行」。

打包成单文件 exe 后，``__file__`` 指向的是临时解包目录 ``_MEIPASS``，
每次启动都不一样 —— 配置写在那里会丢。所以运行时数据（settings.json、
plugins/、diag.txt）一律放在 **exe 所在目录**，做成绿色版；只读资源才用
_MEIPASS。
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """可写的工作目录：exe 旁边 / 源码工程根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录（打包后是 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return app_dir()


def plugin_dir() -> Path:
    """插件目录：优先 exe 旁边的 plugins/，没有再退到内置资源。"""
    local = app_dir() / "plugins"
    if local.is_dir():
        return local
    return resource_dir() / "plugins"


def ensure_runtime_dirs() -> None:
    """首次运行时把可写目录建好，用户能直接往里丢插件。"""
    for d in (app_dir() / "plugins", protocol_dir(), capture_dir()):
        try:
            d.mkdir(exist_ok=True)
        except OSError:
            pass


ASSET_FILES = ("index.html", "app.css", "app.js")


def ensure_assets(version: str = "") -> bool:
    """把打包进 exe 的界面文件另存一份到程序旁边。

    为什么需要这一步
    ----------------
    单文件版每次启动都会把自身解压到系统临时目录（_MEIxxxx），界面文件也在那里。
    火的/360 之类清理工具一删，进程还活着但首页永远 404 —— 用户看到的就是
    「打不开控制台」，而且自己完全没法修。

    所以在启动时把资源复制到 ``<程序目录>/panel/static``，之后一律优先读这份。
    它跟 exe 放在一起，不会被当垃圾清掉，用户换了机器连文件夹一起拷也还在。

    返回 True 表示本地副本可用。
    """
    import shutil

    src = resource_dir() / "panel" / "static"
    dst = app_dir() / "panel" / "static"
    try:
        if src.resolve() == dst.resolve():      # 源码运行，本身就是同一份
            return (dst / "index.html").is_file()

        dst.mkdir(parents=True, exist_ok=True)
        stamp = dst / ".version"
        current = ""
        if stamp.is_file():
            try:
                current = stamp.read_text("utf-8").strip()
            except OSError:
                current = ""

        # 版本变了才整体覆盖，避免每次启动都重写（也不干扰用户手改样式）
        refresh = bool(version) and current != version
        for name in ASSET_FILES:
            s = src / name
            if not s.is_file():
                continue
            d = dst / name
            if refresh or not d.is_file():
                shutil.copy2(s, d)
        if version:
            try:
                stamp.write_text(version, "utf-8")
            except OSError:
                pass
    except OSError:
        pass
    return (dst / "index.html").is_file()


def protocol_dir() -> Path:
    """协议库目录：一条协议一个 json，用户可以整包拷走/分享。"""
    d = app_dir() / "protocols"
    try:
        d.mkdir(exist_ok=True)
    except OSError:
        pass
    return d


def capture_dir() -> Path:
    """抓包文件目录。"""
    d = app_dir() / "captures"
    try:
        d.mkdir(exist_ok=True)
    except OSError:
        pass
    return d
