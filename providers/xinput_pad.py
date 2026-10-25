"""XInput Provider：Xbox 系列手柄（有线 / Xbox 无线适配器 / 蓝牙）。

XInput 的 ``XInputGetBatteryInformation`` 只给四档（空/低/中/满），
拿不到精确百分比，所以这里映射成代表值并在 detail 里注明「档位」，
UI 上显示为 ``低/中/满`` 而不是假装精确。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from core.models import Device, Kind, Reading, Status, Transport
from providers.base import Provider

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167
BATTERY_DEVTYPE_GAMEPAD = 0

LEVEL_PERCENT = {0: 0, 1: 15, 2: 55, 3: 100}
LEVEL_LABEL = {0: "空", 1: "低", 2: "中", 3: "满"}
TYPE_LABEL = {0: "未连接", 1: "有线", 2: "碱性电池", 3: "镍氢电池", 4: "未知"}


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


def _load_xinput():
    for dll in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            lib = ctypes.WinDLL(dll)
            lib.XInputGetBatteryInformation.argtypes = [
                wintypes.DWORD, ctypes.c_ubyte,
                ctypes.POINTER(XINPUT_BATTERY_INFORMATION),
            ]
            lib.XInputGetBatteryInformation.restype = wintypes.DWORD
            return lib
        except Exception:
            continue
    return None


class XInputProvider(Provider):
    name = "xinput"
    order = 30
    SLOTS = 4

    def __init__(self, settings: dict | None = None):
        super().__init__(settings)
        self._lib = _load_xinput()

    def probe(self) -> list[Device]:
        if self._lib is None:
            return []
        devices: list[Device] = []
        for slot in range(self.SLOTS):
            info = XINPUT_BATTERY_INFORMATION()
            rc = self._lib.XInputGetBatteryInformation(slot, BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(info))
            if rc != ERROR_SUCCESS or info.BatteryType == 0:
                continue
            devices.append(Device(
                uid=f"xinput:{slot}",
                name=f"Xbox 手柄 #{slot + 1}",
                kind=Kind.GAMEPAD,
                transport=Transport.USB if info.BatteryType == 1 else Transport.WIRELESS_24G,
                provider=self.name,
                native_id=str(slot),
            ))
        return devices

    def read(self, device: Device) -> Reading:
        if self._lib is None:
            return Reading(device, None, Status.ERROR, detail="xinput DLL 不可用")
        info = XINPUT_BATTERY_INFORMATION()
        rc = self._lib.XInputGetBatteryInformation(
            int(device.native_id), BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(info))
        if rc != ERROR_SUCCESS:
            return Reading(device, None, Status.DISCONNECTED, source="XInput",
                           detail=f"rc={rc}")
        pct = LEVEL_PERCENT.get(info.BatteryLevel)
        return Reading(
            device, pct,
            Status.CHARGING if info.BatteryType == 1 and info.BatteryLevel == 3 else Status.CONNECTED,
            source="XInput",
            detail=f"档位 {LEVEL_LABEL.get(info.BatteryLevel, '?')}（{TYPE_LABEL.get(info.BatteryType, '?')}），XInput 不提供精确百分比",
        )
