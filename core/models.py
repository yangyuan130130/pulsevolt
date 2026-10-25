"""数据模型：设备、读数、枚举与中文标签。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    GAMEPAD = "gamepad"
    HEADSET = "headset"
    OTHER = "other"


class Transport(str, Enum):
    BLE = "ble"            # 蓝牙低功耗
    BT_CLASSIC = "bt"      # 经典蓝牙
    WIRELESS_24G = "24g"   # 2.4G 私有协议接收器
    USB = "usb"
    UNKNOWN = "unknown"


class Status(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CHARGING = "charging"
    UNKNOWN = "unknown"
    ERROR = "error"


KIND_LABEL = {
    Kind.KEYBOARD: "键盘",
    Kind.MOUSE: "鼠标",
    Kind.GAMEPAD: "手柄",
    Kind.HEADSET: "耳机",
    Kind.OTHER: "外设",
}

TRANSPORT_LABEL = {
    Transport.BLE: "蓝牙",
    Transport.BT_CLASSIC: "蓝牙(经典)",
    Transport.WIRELESS_24G: "2.4G",
    Transport.USB: "USB",
    Transport.UNKNOWN: "未知",
}

STATUS_LABEL = {
    Status.CONNECTED: "已连接",
    Status.DISCONNECTED: "未连接",
    Status.CHARGING: "充电中",
    Status.UNKNOWN: "未知",
    Status.ERROR: "读取失败",
}


@dataclass(slots=True)
class Device:
    """一台外设。uid 在同一 Provider 内稳定，用于跨轮次识别。"""

    uid: str
    name: str
    kind: Kind = Kind.OTHER
    transport: Transport = Transport.UNKNOWN
    provider: str = ""
    native_id: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(slots=True)
class Reading:
    """一次电量读数。percent 为 None 表示拿不到数值（例如只支持档位）。"""

    device: Device
    percent: int | None = None
    status: Status = Status.UNKNOWN
    source: str = ""
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def summary(self) -> str:
        if self.percent is None:
            pct = "—"
        else:
            pct = f"{self.percent}%"
        return f"{self.device.name} {pct} · {STATUS_LABEL.get(self.status, self.status)}"
