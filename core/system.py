"""环境能力：权限、USBPcap 位置、提权重启、开机自启。

这些是「工具能不能干活」的前提条件，单独抽出来，页面顶部那条状态栏
（是否管理员 / 有无 USBPcap / 几个可用集线器）就是读这里。
"""
from __future__ import annotations

import ctypes
import os
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

USBPCAP_DIRS = [
    r"C:\Program Files\USBPcap",
    r"C:\Program Files (x86)\USBPcap",
]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_KEY = "PulseVolt"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def usbpcap_cmd() -> Path | None:
    for d in USBPCAP_DIRS:
        p = Path(d) / "USBPcapCMD.exe"
        if p.exists():
            return p
    return None


def usbpcap_installed() -> bool:
    return usbpcap_cmd() is not None


def probe_hubs(max_index: int = 8) -> list[dict]:
    """探测 \\\\.\\USBPcapN 能打开的几个。

    普通用户打开会拿到 ERROR_ACCESS_DENIED，这**不代表集线器不存在**，
    所以这里只做「设备名能不能被创建」的判定，并把权限状态一并返回。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value
    admin = is_admin()
    hubs = []
    for i in range(1, max_index + 1):
        name = rf"\\.\USBPcap{i}"
        handle = kernel32.CreateFileW(name, 0x80000000 | 0x40000000, 3, None, 3, 0, None)
        err = ctypes.get_last_error()
        exists = handle not in (invalid, 0, None)
        if exists:
            kernel32.CloseHandle(handle)
        if exists:
            hubs.append({"index": i, "text": f"USBPcap{i}", "openable": True})
        elif err == 5 and admin:
            # 已是管理员还被拒：说明该编号没有对应集线器
            continue
        elif err == 5:
            # 非管理员：可能是权限问题，也可能是编号不存在；留给抓包时判定
            hubs.append({"index": i, "text": f"USBPcap{i}", "openable": False})
        elif err in (2, 3):            # 文件/路径不存在
            continue
        else:
            continue
    return hubs


def relaunch_as_admin(extra_args: list[str] | None = None) -> bool:
    """以管理员身份重新拉起自己。返回 False 表示已经在管理员权限下。"""
    if is_admin():
        return False
    args = ["--panel"] + (extra_args or [])
    if not getattr(sys, "frozen", False):
        args = [str(Path(__file__).resolve().parent.parent / "main.py")] + args
    params = " ".join(f'"{a}"' if " " in a else a for a in args)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1)
        return rc > 32
    except Exception:
        return False


def autostart_state() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_KEY)
            return True
    except OSError:
        return False


def set_autostart(enable: bool) -> bool:
    exe = sys.executable
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                command = f'"{exe}" --tray'
                winreg.SetValueEx(key, APP_KEY, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_KEY)
                except OSError:
                    pass
        return True
    except OSError:
        return False


def open_path(path: str | Path) -> None:
    """用系统默认程序打开文件/目录（只用于展示，不执行程序）。"""
    try:
        os.startfile(str(path))   # noqa: S606
    except OSError:
        pass
