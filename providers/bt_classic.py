"""经典蓝牙 Provider：耳机/音箱等。

经典蓝牙（A2DP/HFP）没有 GATT 电量服务，Windows 只在部分支持
「蓝牙电量上报」的设备上给出缓存值。这里只做两件事：

* 把系统缓存里**有电量**的经典蓝牙设备列出来（如耳机）；
* 对只有名字、没有电量的音频设备，仍然列出并标注「系统未提供电量」，
  让用户知道设备在线上、只是拿不到数值。
"""
from __future__ import annotations

from core.models import Device, Kind, Reading, Status, Transport
from providers.base import Provider

AUDIO_HINTS = ("headset", "headphone", "buds", "earbud", "speaker", "耳机", "耳麦",
               "音箱", "soundcore", "airpods", "wh-", "wf-", "freebuds")


def _kind_from_name(name: str) -> Kind:
    low = name.lower()
    if any(h in low for h in AUDIO_HINTS):
        return Kind.HEADSET
    return Kind.OTHER


class BtClassicProvider(Provider):
    name = "bt-classic"
    order = 5
    wants_cache = True

    def probe(self) -> list[Device]:
        if not self.cache:
            return []
        devices: list[Device] = []
        for mac, row in self.cache.items().items():
            if row.get("le"):
                continue
            name = (row.get("name") or "").strip()
            battery = row.get("battery")
            if not name:
                continue
            if battery is None and _kind_from_name(name) is Kind.OTHER:
                continue  # 与电量无关的经典蓝牙设备（手机/车机等）不列出
            devices.append(Device(
                uid=f"bt:{mac}",
                name=name,
                kind=_kind_from_name(name),
                transport=Transport.BT_CLASSIC,
                provider=self.name,
                native_id=mac,
            ))
        return devices

    def read(self, device: Device) -> Reading:
        row = self.cache.get(device.native_id) if self.cache else None
        if not row:
            return Reading(device, None, Status.DISCONNECTED, source="Windows 蓝牙缓存")
        battery = row.get("battery")
        last = row.get("last") or "未知"
        if isinstance(battery, int) and 0 <= battery <= 100:
            return Reading(device, battery, Status.CONNECTED, source="Windows 蓝牙缓存",
                           detail=f"最后一次连接 {last}")
        return Reading(device, None, Status.UNKNOWN, source="Windows 蓝牙缓存",
                       detail="经典蓝牙协议不带电量，系统未提供数值")
