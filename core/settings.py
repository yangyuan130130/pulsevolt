"""配置读写：默认值 + 用户 settings.json 覆盖。"""
from __future__ import annotations

import json
import sys

from core.paths import app_dir

SETTINGS_PATH = app_dir() / "settings.json"

DEFAULTS: dict = {
    "refresh_interval": 15,          # 秒，整轮刷新周期
    "bt_cache_ttl": 60,              # 秒，Windows 蓝牙缓存重读周期
    "low_battery_threshold": 20,     # 低于等于该值提醒
    "low_battery_hysteresis": 5,     # 回升到 阈值+该值 以上才重新武装提醒
    "alert_cooldown_min": 30,        # 同一设备最短提醒间隔（分钟）
    "notify_on_device_change": True, # 设备上下线是否弹通知
    "providers": {
        "ble-gatt": True,
        "bt-classic": True,
        "mchose-hub": True,
        "xinput": True,
        "hid-vendor": True,
    },
    # 2.4G 私有协议设备（协议确定后填这里即可，格式见 providers/hid_vendor.py）
    "hid_devices": [],
    "pin_device": "",                # 托盘图标固定显示某台设备（uid），空=自动选最低电量
    "hide_unknown": False,           # 隐藏读不到电量的设备
    "show_disconnected": False,      # 是否显示已断开设备的「上次电量」（默认关：避免把缓存值当实时值）
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # 深拷贝
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text("utf-8"))
            for key, value in user.items():
                if key == "providers" and isinstance(value, dict):
                    cfg["providers"].update(value)
                else:
                    cfg[key] = value
        except Exception as exc:
            print(f"[settings] 读取失败，用默认值: {exc}", file=sys.stderr)
    else:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception as exc:
        print(f"[settings] 保存失败: {exc}", file=sys.stderr)
