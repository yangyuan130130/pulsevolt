"""迈从（MCHOSE）2.4G 外设 Provider。

2.4G 私有协议没有系统级标准，官方驱动 MCHOSE HUB（Electron）会把读到的
电量持久化在 userData 下的 electron-store JSON 里。这里直接读那份状态：

* 优点：零侵入、不需要逆向厂商 HID 报文、数值就是驱动界面上显示的那个；
* 缺点：依赖官方驱动在跑（退出后文件不再更新，用 mtime 判断新鲜度并提示）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from core.models import Device, Kind, Reading, Status, Transport
from providers.base import Provider

STORE = Path(os.environ.get("APPDATA", "")) / "MCHOSEHUB" / "files" / "config" / "mc_main_store_key.json"

# connectMode：1 = 2.4G 接收器（实测）；0 = 掉线占位
_CONNECT_MODE = {1: Transport.WIRELESS_24G, 2: Transport.BLE}


def _guess_kind(name: str, device_type) -> Kind:
    low = name.lower()
    if "键盘" in name or "keyboard" in low:
        return Kind.KEYBOARD
    if "耳机" in name or "headset" in low or "耳麦" in name:
        return Kind.HEADSET
    if "手柄" in name or "gamepad" in low or "controller" in low:
        return Kind.GAMEPAD
    if device_type == 2:  # 实测：G7 鼠标 deviceType=2
        return Kind.MOUSE
    return Kind.OTHER


class MchoseHubProvider(Provider):
    name = "mchose-hub"
    order = 20

    def probe(self) -> list[Device]:
        payload = self._load()
        if not payload:
            return []
        devices: list[Device] = []
        for item in payload.get("webIndexList", []):
            name = item.get("productName") or item.get("deviceName") or "MCHOSE 外设"
            key = item.get("storageKey") or name
            devices.append(Device(
                uid=f"mchose:{key}",
                name=name,
                kind=_guess_kind(name, item.get("deviceType")),
                transport=Transport.WIRELESS_24G,
                provider=self.name,
                native_id=key,
            ))
        return devices

    def read(self, device: Device) -> Reading:
        payload = self._load()
        if not payload:
            return Reading(device, None, Status.UNKNOWN, source="MCHOSE HUB",
                           detail="未找到 MCHOSE HUB 状态文件")
        age = time.time() - payload["_mtime"]
        stale_note = "" if age < 180 else f"数据已 {int(age // 60)} 分钟未更新，HUB 可能没在运行"

        for item in payload.get("webIndexList", []):
            key = item.get("storageKey") or item.get("productName")
            if f"mchose:{key}" != device.uid:
                continue

            raw = item.get("batteryLevel")
            transport = _CONNECT_MODE.get(item.get("connectMode"), Transport.WIRELESS_24G)
            device.transport = transport

            if not isinstance(raw, int):
                # "breakConnect" 等字符串代表设备未连接
                return Reading(device, None, Status.DISCONNECTED, source="MCHOSE HUB",
                               detail=f"connectMode={item.get('connectMode')}；{stale_note}".strip("；"))
            status = Status.CHARGING if item.get("chargeStatus") else Status.CONNECTED
            return Reading(
                device, max(0, min(100, raw)), status, source="MCHOSE HUB 状态文件",
                detail=stale_note or f"读数粒度 {item.get('batteryPrecision', '?')}%",
            )

        return Reading(device, None, Status.DISCONNECTED, source="MCHOSE HUB")

    # ------------------------------------------------------------------
    @staticmethod
    def _load() -> dict | None:
        try:
            stat = STORE.stat()
            with STORE.open("rb") as fh:
                data = json.loads(fh.read().decode("utf-8-sig", errors="replace"))
            if isinstance(data, dict):
                data["_mtime"] = stat.st_mtime
                return data
        except Exception:
            return None
        return None
