"""蓝牙低功耗（BLE）Provider：标准 GATT 电量服务 0x180F / 特征 0x2A19。

为什么这么写
------------
* **发现**走注册表 ``BTHPORT\\Parameters\\Devices``：只读一次注册表就能拿到
  所有已配对 BLE 设备的 MAC / 名称 / LEAppearance，不唤醒任何设备。
  （用 WinRT 的 DeviceWatcher 只能看到正在广播的设备，已连接设备反而不广播。）
* **读数**走 pywinrt 直连：``BluetoothLEDevice.from_bluetooth_address_async``
  不会建立连接，先看 ``connection_status``，只有已连接的才去读 GATT，
  避免把休眠设备反复唤醒耗电。
* 未连接的设备不硬读，退回 Windows 缓存值（最后一次连接时的百分比）。
"""
from __future__ import annotations

import asyncio
import time
import winreg
from uuid import UUID

from core.models import Device, Kind, Reading, Status, Transport
from providers.base import Provider

BTHPORT_KEY = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
BATTERY_SVC = UUID("0000180f-0000-1000-8000-00805f9b34fb")
BATTERY_CHR = UUID("00002a19-0000-1000-8000-00805f9b34fb")

# LEAppearance -> 设备类型
_APPEARANCE_KIND = {
    0x03C1: Kind.KEYBOARD,
    0x03C2: Kind.MOUSE,
    0x03C3: Kind.GAMEPAD,   # joystick
    0x03C4: Kind.GAMEPAD,   # gamepad
    0x0840: Kind.HEADSET,   # wearable headset
    0x0841: Kind.HEADSET,
    0x0842: Kind.HEADSET,
    0x0941: Kind.HEADSET,
}

# appearance 缺失（很多外设不填）时的名称兜底
_NAME_HINTS = (
    ("keyboard", Kind.KEYBOARD), ("键盘", Kind.KEYBOARD), ("kbd", Kind.KEYBOARD), ("bt5.0", Kind.KEYBOARD),
    ("mouse", Kind.MOUSE), ("鼠标", Kind.MOUSE), ("mchose", Kind.MOUSE), ("g7", Kind.MOUSE),
    ("pad", Kind.GAMEPAD), ("controller", Kind.GAMEPAD), ("手柄", Kind.GAMEPAD), ("xbox", Kind.GAMEPAD),
    ("headset", Kind.HEADSET), ("buds", Kind.HEADSET), ("耳机", Kind.HEADSET), ("soundcore", Kind.HEADSET),
)
_PERIPHERAL_HINTS = ("keyboard", "mouse", "pad", "controller", "headset", "buds", "耳机", "键盘", "鼠标", "手柄",
                     "mchose", "langtu", "logitech", "razer", "keychron", "8bitdo", "bt5.0")


def _kind_from_name(name: str) -> Kind:
    low = name.lower()
    for token, kind in _NAME_HINTS:
        if token in low:
            return kind
    return Kind.OTHER


def _decode(raw) -> str:
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw.strip("\x00 ")
    try:
        return bytes(raw).rstrip(b"\x00").decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _read_paired_ble() -> list[dict]:
    """读注册表拿到所有已配对 BLE 设备（无 I/O 唤醒）。"""
    rows: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BTHPORT_KEY) as root:
            total = winreg.QueryInfoKey(root)[0]
            for i in range(total):
                mac = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, mac) as sub:
                        def gv(name):
                            try:
                                return winreg.QueryValueEx(sub, name)[0]
                            except OSError:
                                return None

                        le_name = gv("LEName")
                        appearance = gv("LEAppearance")
                        # 只有带 LE 特征的才是 BLE；纯经典蓝牙设备没有 LEName
                        if not le_name and not appearance:
                            continue
                        rows.append({
                            "mac": mac.upper(),
                            "name": _decode(le_name) or _decode(gv("Name")) or mac.upper(),
                            "appearance": int(appearance or 0),
                        })
                except OSError:
                    continue
    except OSError:
        pass
    return rows


async def _read_gatt(address: int) -> tuple[int | None, str]:
    """返回 (电量, 来源说明)。不抛异常。"""
    try:
        from winrt.windows.devices.bluetooth import (
            BluetoothConnectionStatus,
            BluetoothLEDevice,
        )
    except Exception as exc:
        return None, f"pywinrt 不可用: {exc}"

    dev = None
    try:
        dev = await BluetoothLEDevice.from_bluetooth_address_async(address)
        if dev is None:
            return None, "无法取到设备对象"
        if dev.connection_status != BluetoothConnectionStatus.CONNECTED:
            return None, "未连接"
        svc_res = await dev.get_gatt_services_for_uuid_async(BATTERY_SVC)
        if int(svc_res.status) != 0 or svc_res.services.size == 0:
            return None, "设备无标准电量服务"
        svc = svc_res.services.get_at(0)
        chr_res = await svc.get_characteristics_for_uuid_async(BATTERY_CHR)
        if int(chr_res.status) != 0 or chr_res.characteristics.size == 0:
            return None, "无电量特征 0x2A19"
        ch = chr_res.characteristics.get_at(0)
        res = await ch.read_value_async()
        if int(res.status) != 0 or res.value is None:
            return None, f"读取失败 status={res.status}"
        data = bytes(res.value)
        if not data:
            return None, "空数据"
        return data[0], "GATT 0x2A19"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass


class BleGattProvider(Provider):
    name = "ble-gatt"
    order = 10
    wants_cache = True

    def probe(self) -> list[Device]:
        devices: list[Device] = []
        for row in _read_paired_ble():
            name = row["name"]
            kind = _APPEARANCE_KIND.get(row["appearance"]) or _kind_from_name(name)
            cached = self.cache.get(row["mac"]) if self.cache else None
            has_cache = isinstance((cached or {}).get("battery"), int)
            looks_like_peripheral = kind is not Kind.OTHER or any(
                hint in name.lower() for hint in _PERIPHERAL_HINTS)
            if not (looks_like_peripheral or has_cache):
                continue  # 手机/平板等与电量面板无关的已配对设备
            devices.append(Device(
                uid=f"ble:{row['mac']}",
                name=name,
                kind=kind,
                transport=Transport.BLE,
                provider=self.name,
                native_id=row["mac"],
            ))
        return devices

    def read(self, device: Device) -> Reading:
        mac = device.native_id
        try:
            address = int(mac, 16)
        except ValueError:
            return Reading(device, None, Status.ERROR, detail="MAC 解析失败")

        started = time.time()
        percent, note = asyncio.run(_read_gatt(address))
        elapsed = time.time() - started

        if percent is not None:
            return Reading(
                device, percent, Status.CONNECTED,
                source=note, detail=f"耗时 {elapsed * 1000:.0f} ms",
            )

        # 读不到就用系统缓存值，并如实标注状态
        cached = self.cache.get(mac) if self.cache else None
        cached_pct = (cached or {}).get("battery")
        if isinstance(cached_pct, int) and 0 <= cached_pct <= 100:
            return Reading(
                device, cached_pct, Status.DISCONNECTED,
                source="Windows 蓝牙缓存",
                detail=f"{note}；数值为最后一次连接时的记录",
            )
        return Reading(device, None, Status.DISCONNECTED, source=note)
