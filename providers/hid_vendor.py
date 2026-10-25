"""通用「厂商 HID 私有协议」Provider —— 2.4G 接收器电量的接入点。

2.4G 没有标准，每家协议都不同，所以这里不硬编码任何厂商，而是把
「打开哪个接口、发什么帧、读第几个字节」全部放进 settings.json：

    "hid_devices": [
      {
        "name": "某键盘", "kind": "keyboard",
        "vid": "0x05AC", "pid": "0x024F",
        "usage_page": "0xFF60", "usage": "0x61",
        "path_hint": "",
        "report_len": 33,
        "request": "06 14",
        "via": "output",
        "response_offset": 1,
        "min_interval": 25,
        "enabled": true
      }
    ]

协议怎么定？用 tools/hid_probe.py 枚举接口 + 试发一帧看回应，
或用 USBPcap 抓官方驱动的报文照抄（见 tools/pcap_diff.py）。

安全约束（都是踩过的坑）：
* 只发配置里那一条帧，绝不扫命令空间 —— 同一接口可能带改配置/进 DFU 的命令；
* min_interval 强制冷却，多数固件对同一命令几十秒内不重复应答；
* 应答必须过校验（长度够、值在 0~100），否则如实报错，不猜。
"""
from __future__ import annotations

import ctypes
import threading
import time
import uuid
from ctypes import wintypes

from core.models import Device, Kind, Reading, Status, Transport
from providers.base import Provider

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid.dll", use_last_error=True)

# 所有 HID 打开/读写都过这把锁。
# Manager 的轮询线程和「智能测试」线程会同时碰同一个接收器，
# 两边各自 open-write-read-close 会互相打断设备会话，严重时直接把驱动搞崩。
HID_LOCK = threading.RLock()

HID_GUID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_ALL = 0x00000003
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
INVALID_HANDLE = ctypes.c_void_p(-1).value


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(wintypes.ULONG))]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.POINTER(wintypes.ULONG)),
                ("InternalHigh", ctypes.POINTER(wintypes.ULONG)),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


def _declare_signatures() -> None:
    """显式声明 ctypes 签名。

    不声明 restype 时 handle 按 c_int 返回，64 位下会被截断成 32 位；
    拿着残缺句柄去 ReadFile/WriteFile，轻则一直失败、重则整个进程崩溃。
    这是 1.0 遗留的隐患，2.0 的智能测试把它放大了，必须补上。
    """
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                      wintypes.LPCWSTR]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
    kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                             ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    try:
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    except AttributeError:
        pass

    hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
    hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
    hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]


_declare_signatures()


def _guid(text: str) -> GUID:
    u = uuid.UUID(text)
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


def enumerate_hid() -> list[dict]:
    """枚举所有 HID 接口，带 VID/PID/UsagePage/Usage 与报文长度。"""
    g = _guid(HID_GUID)
    handle = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                           DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if handle == INVALID_HANDLE:
        return []
    out: list[dict] = []
    did = SP_DEVICE_INTERFACE_DATA()
    did.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    buf = ctypes.create_string_buffer(2048)

    index = 0
    while setupapi.SetupDiEnumDeviceInterfaces(handle, None, ctypes.byref(g), index,
                                               ctypes.byref(did)):
        ctypes.memset(buf, 0, 2048)
        ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
        if setupapi.SetupDiGetDeviceInterfaceDetailW(handle, ctypes.byref(did), buf, 2048, None, None):
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            info = _probe_interface(path)
            if info:
                out.append(info)
        index += 1
    setupapi.SetupDiDestroyDeviceInfoList(handle)
    return out


def _probe_interface(path: str) -> dict | None:
    """用 HidD_GetAttributes / HidP_GetCaps 读 VID/PID/UsagePage。"""
    fh = kernel32.CreateFileW(path, 0, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None)
    if fh == INVALID_HANDLE or fh is None:
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(fh, ctypes.byref(attrs)):
            return None

        preparsed = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(fh, ctypes.byref(preparsed)):
            return {"path": path, "vid": attrs.VendorID, "pid": attrs.ProductID,
                    "usage_page": 0, "usage": 0, "in_len": 0, "out_len": 0, "feat_len": 0}
        try:
            caps = ctypes.create_string_buffer(512)
            hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
            vals = ctypes.cast(caps, ctypes.POINTER(wintypes.USHORT))
            return {
                "path": path,
                "vid": attrs.VendorID, "pid": attrs.ProductID,
                "usage": vals[0], "usage_page": vals[1],
                "in_len": vals[2], "out_len": vals[3], "feat_len": vals[4],
            }
        finally:
            hid.HidD_FreePreparsedData(preparsed)
    finally:
        kernel32.CloseHandle(fh)


def _open_rw(path: str):
    fh = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_ALL,
                              None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
    if fh == INVALID_HANDLE or fh is None:
        return None
    return fh


def _io(handle, data: bytes, write: bool, timeout_ms: int = 3000):
    event = kernel32.CreateEventW(None, True, False, None)
    if not event:
        return None
    ov = OVERLAPPED()
    ov.hEvent = event
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    done = wintypes.DWORD()
    fn = kernel32.WriteFile if write else kernel32.ReadFile
    ok = fn(handle, buf, len(data), ctypes.byref(done), ctypes.byref(ov))
    if not ok:
        err = ctypes.get_last_error()
        if err != ERROR_IO_PENDING:
            kernel32.CloseHandle(event)
            return None
        if kernel32.WaitForSingleObject(event, timeout_ms) != 0:
            # 超时**不能**直接关 event 就走：这次 I/O 还挂在驱动里，
            # 等它稍后完成时会往已经释放的 event 写完成状态 —— 表现就是随机崩溃。
            # 正确做法是先取消，再等取消真的落地，最后才关句柄。
            try:
                kernel32.CancelIoEx(handle, ctypes.byref(ov))
            except Exception:
                try:
                    kernel32.CancelIo(handle)
                except Exception:
                    pass
            kernel32.WaitForSingleObject(event, 800)
            kernel32.CloseHandle(event)
            return None
        if not kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(done), False):
            kernel32.CloseHandle(event)
            return None
    kernel32.CloseHandle(event)
    return (buf.raw[:done.value] if write is False else b"")


def _parse_frame(text: str, length: int) -> bytes:
    raw = bytes.fromhex(text.replace(" ", "")) if text else b""
    return raw.ljust(length, b"\x00")[:length]


def _kind(name: str) -> Kind:
    low = name.lower()
    if "键" in name or "keyboard" in low:
        return Kind.KEYBOARD
    if "鼠" in name or "mouse" in low:
        return Kind.MOUSE
    if "耳" in name or "headset" in low:
        return Kind.HEADSET
    if "手柄" in name or "pad" in low:
        return Kind.GAMEPAD
    return Kind.OTHER


_KIND_BY_CONFIG = {
    "keyboard": Kind.KEYBOARD, "键盘": Kind.KEYBOARD,
    "mouse": Kind.MOUSE, "鼠标": Kind.MOUSE,
    "headset": Kind.HEADSET, "耳机": Kind.HEADSET,
    "gamepad": Kind.GAMEPAD, "手柄": Kind.GAMEPAD,
}


class HidVendorProvider(Provider):
    name = "hid-vendor"
    order = 25

    def __init__(self, settings: dict | None = None):
        super().__init__(settings)
        self.specs = [s for s in (settings or {}).get("hid_devices", []) if s.get("enabled", True)]
        self._last_read: dict[str, float] = {}
        self._cache: dict[str, Reading] = {}

    def _match(self, spec: dict) -> list[dict]:
        vid = int(str(spec.get("vid", "0")), 0)
        pid = int(str(spec.get("pid", "0")), 0)
        page = int(str(spec.get("usage_page", "0")), 0)
        usage = int(str(spec.get("usage", "0")), 0)
        hint = (spec.get("path_hint") or "").lower()
        result = []
        for info in enumerate_hid():
            if info["vid"] != vid or info["pid"] != pid:
                continue
            if page and info["usage_page"] != page:
                continue
            if usage and info["usage"] != usage:
                continue
            if hint and hint not in info["path"].lower():
                continue
            result.append(info)
        return result

    def probe(self) -> list[Device]:
        devices: list[Device] = []
        for spec in self.specs:
            kind = _KIND_BY_CONFIG.get(str(spec.get("kind", "")).lower()) or _kind(spec.get("name", ""))
            for info in self._match(spec):
                devices.append(Device(
                    uid=f"hidv:{info['vid']:04X}:{info['pid']:04X}:{info['path'][-8:]}",
                    name=spec.get("name") or f"HID {info['vid']:04X}:{info['pid']:04X}",
                    kind=kind,
                    transport=Transport.WIRELESS_24G,
                    provider=self.name,
                    native_id=info["path"],
                    extra={"spec": spec},
                ))
        return devices

    def read(self, device: Device) -> Reading:
        spec = device.extra.get("spec") or {}
        path = device.native_id
        interval = float(spec.get("min_interval", 20))
        now = time.time()
        if now - self._last_read.get(path, 0) < interval:
            cached = self._cache.get(path)
            if cached:
                cached.detail = f"限速中（{interval:.0f}s 一次），沿用上次读数"
                return cached

        length = int(spec.get("report_len", 33))
        request = _parse_frame(spec.get("request", ""), length)
        offset = int(spec.get("response_offset", 1))
        use_feature = str(spec.get("via", "output")).lower() == "feature"

        with HID_LOCK:
            handle = _open_rw(path)
            if handle is None:
                return Reading(device, None, Status.ERROR, source="HID 私有协议",
                               detail="打开接口失败（可能被厂商软件独占）")
            try:
                if use_feature:
                    inbuf = ctypes.create_string_buffer(bytes(request), length)
                    outbuf = ctypes.create_string_buffer(length)
                    if not hid.HidD_SetFeature(handle, inbuf, length):
                        return Reading(device, None, Status.ERROR, source="HID Feature",
                                       detail="SetFeature 失败")
                    if not hid.HidD_GetFeature(handle, outbuf, length):
                        return Reading(device, None, Status.ERROR, source="HID Feature",
                                       detail="GetFeature 失败")
                    response = outbuf.raw[:length]
                else:
                    _io(handle, request, write=True)
                    response = _io(handle, bytes(length), write=False)
            finally:
                kernel32.CloseHandle(handle)

        self._last_read[path] = now
        if not response:
            # 无应答通常意味着设备不在这个链路上（例如键盘切到蓝牙了），
            # 用 DISCONNECTED 让上层按「未连接」过滤，而不是报错刷屏
            return Reading(device, None, Status.DISCONNECTED, source="HID 私有协议",
                           detail="无应答（设备可能不在该链路，例如已切到蓝牙）")
        if len(response) <= offset:
            return Reading(device, None, Status.ERROR, source="HID 私有协议",
                           detail=f"应答过短：{response.hex(' ')}")
        pct = response[offset]
        if not 0 <= pct <= 100:
            return Reading(device, None, Status.ERROR, source="HID 私有协议",
                           detail=f"字节值 {pct} 不在 0~100，offset 可能配错了：{response.hex(' ')}")

        reading = Reading(device, pct, Status.CONNECTED, source="HID 私有协议",
                          detail=f"offset={offset} 原始帧 {response.hex(' ')}")
        self._cache[path] = reading
        return reading
