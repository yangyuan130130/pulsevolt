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
