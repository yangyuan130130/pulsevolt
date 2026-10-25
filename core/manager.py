"""调度核心：发现设备 → 读取电量 → 变化检测 → 低电量提醒。

设计取舍
--------
* **单线程轮询**足够：设备数量是个位数，整轮耗时通常 < 2s；
  比多线程简单，也不会有 Provider 并发访问 HID/蓝牙栈的问题。
* **立即唤醒**：``request_refresh()`` 会打断 sleep，用于用户点「立即刷新」
  或 PnP 设备变更事件，避免等满一个周期。
* **提醒去抖**：同一台设备只在「跨过阈值」时提醒一次，回升到
  阈值+滞回 之上才重新武装，并受冷却时间约束，避免一直滴滴响。
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time

from core.btcache import BtBatteryCache
from core.models import Kind, Reading, Status, Transport
from core.paths import plugin_dir

_KIND_ORDER = {Kind.KEYBOARD: 0, Kind.MOUSE: 1, Kind.GAMEPAD: 2, Kind.HEADSET: 3, Kind.OTHER: 4}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_plugins(settings: dict) -> list:
    """从 plugins/*.py 加载第三方 Provider（模块内需定义 PROVIDER 类）。"""
    from providers.base import Provider

    found = []
    directory = plugin_dir()   # 每次现算：首次运行时目录可能刚被创建
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"pbp_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = getattr(module, "PROVIDER", None)
            if isinstance(cls, type) and issubclass(cls, Provider):
                found.append(cls(settings))
                _log(f"[plugins] 已加载 {path.name} -> {cls.__name__}")
        except Exception as exc:
            _log(f"[plugins] {path.name} 加载失败: {type(exc).__name__}: {exc}")
    return found


def build_providers(settings: dict) -> list:
    from providers.ble_gatt import BleGattProvider
    from providers.bt_classic import BtClassicProvider
    from providers.hid_vendor import HidVendorProvider
    from providers.mchose_hub import MchoseHubProvider
    from providers.xinput_pad import XInputProvider

    builtins = [BleGattProvider, BtClassicProvider, MchoseHubProvider, XInputProvider,
                HidVendorProvider]
    providers = []
    for cls in builtins:
        if not settings.get("providers", {}).get(cls.name, True):
            continue
        try:
            providers.append(cls(settings))
        except Exception as exc:
            _log(f"[providers] {cls.__name__} 初始化失败: {exc}")
    providers.extend(load_plugins(settings))
    providers.sort(key=lambda p: p.order)
    return providers


class Manager:
    def __init__(self, settings: dict, on_update=None, on_alert=None, on_device_change=None):
        self.settings = settings
        self.on_update = on_update
        self.on_alert = on_alert
        self.on_device_change = on_device_change

        self.cache = BtBatteryCache(ttl=float(settings.get("bt_cache_ttl", 60)))
        self.providers = build_providers(settings)
        for provider in self.providers:
            if getattr(provider, "wants_cache", False):
                provider.attach(self.cache)

        self._lock = threading.RLock()
        self._readings: dict[str, Reading] = {}
        self._uids: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()

        self._armed: dict[str, bool] = {}
        self._last_alert: dict[str, float] = {}

    # ---- 生命周期 ------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="battery-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                pass

    def request_refresh(self) -> None:
        self._wake.set()

    # ---- 主循环 --------------------------------------------------------
    def _run(self) -> None:
        interval = float(self.settings.get("refresh_interval", 15))
        while not self._stop.is_set():
            try:
                self.refresh_once()
            except Exception as exc:
                _log(f"[loop] 刷新异常: {type(exc).__name__}: {exc}")
            self._wake.wait(timeout=interval)
            self._wake.clear()

    def refresh_once(self) -> list[Reading]:
        discovered = {}
        for provider in self.providers:
            try:
                for device in provider.probe():
                    discovered.setdefault(device.uid, device)
            except Exception as exc:
                _log(f"[probe] {provider.name}: {type(exc).__name__}: {exc}")

        readings: dict[str, Reading] = {}
        for uid, device in discovered.items():
            provider = next((p for p in self.providers if p.name == device.provider), None)
            if provider is None:
                continue
            try:
                readings[uid] = provider.read(device)
            except Exception as exc:
                readings[uid] = Reading(device, None, Status.ERROR,
                                        detail=f"{type(exc).__name__}: {exc}")

        # 过滤掉「既没有数值、又不在线」的条目：
        # 这类通常是早已不用的配对残留或休眠中的手机，列出来只是噪音。
        readings = {
            uid: r for uid, r in readings.items()
            if not (r.status == Status.DISCONNECTED and r.percent is None)
        }
        readings = self._dedupe_by_name(readings)

        # 默认不显示「已断开」的设备。
        # 它们的数值来自 Windows 缓存（最后一次连接时的快照），不是实时值；
        # 混在在线设备里会让人误以为设备还连着、电量还准（B87 那次误报就是这个原因）。
        if not self.settings.get("show_disconnected", False):
            readings = {
                uid: r for uid, r in readings.items()
                if r.status != Status.DISCONNECTED
            }

        if self.settings.get("hide_unknown"):
            readings = {
                uid: r for uid, r in readings.items()
                if r.percent is not None or r.status == Status.CONNECTED
            }

        added = set(readings) - self._uids
        removed = self._uids - set(readings)
        with self._lock:
            self._readings = readings
            self._uids = set(readings)

        if added or removed:
            self._notify_device_change(added, removed, readings)

        self._check_alerts(readings)

        if self.on_update:
            try:
                self.on_update(self.snapshot())
            except Exception as exc:
                _log(f"[ui] on_update 异常: {exc}")
        return list(readings.values())

    # ---- 对外数据 ------------------------------------------------------
    @staticmethod
    def _dedupe_by_name(readings: dict[str, Reading]) -> dict[str, Reading]:
        """同一台设备可能同时出现在 2.4G 和蓝牙两条链路上（例如鼠标两种模式
        都配对过）。按名称归并，优先保留「已连接 > 有数值 > 2.4G」的那条。

        注：同名的两台不同设备会被合并，这是可接受的折中——面板上出现两行
        同名设备对用户更困惑。
        """
        def rank(r: Reading) -> tuple[int, int, int]:
            return (
                1 if r.status in (Status.CONNECTED, Status.CHARGING) else 0,
                1 if r.percent is not None else 0,
                1 if r.device.transport is Transport.WIRELESS_24G else 0,
            )

        best: dict[str, Reading] = {}
        for reading in readings.values():
            key = reading.device.name.strip().lower()
            current = best.get(key)
            if current is None or rank(reading) > rank(current):
                best[key] = reading
        return {r.device.uid: r for r in best.values()}

    def snapshot(self) -> list[Reading]:
        with self._lock:
            items = list(self._readings.values())
        items.sort(key=lambda r: (
            0 if r.status in (Status.CONNECTED, Status.CHARGING) else 1,
            _KIND_ORDER.get(r.device.kind, 9),
            r.device.name.lower(),
        ))
        return items

    def lowest(self) -> Reading | None:
        """电量最低且已连接的设备，用于托盘图标。"""
        pinned = self.settings.get("pin_device") or ""
        items = self.snapshot()
        if pinned:
            for item in items:
                if item.device.uid == pinned:
                    return item
        candidates = [r for r in items if r.percent is not None
                      and r.status in (Status.CONNECTED, Status.CHARGING)]
        return min(candidates, key=lambda r: r.percent) if candidates else None

    # ---- 提醒 ----------------------------------------------------------
    def _check_alerts(self, readings: dict[str, Reading]) -> None:
        threshold = int(self.settings.get("low_battery_threshold", 20))
        hysteresis = int(self.settings.get("low_battery_hysteresis", 5))
        cooldown = float(self.settings.get("alert_cooldown_min", 30)) * 60
        now = time.time()

        for uid, reading in readings.items():
            pct = reading.percent
            if pct is None:
                continue
            self._armed.setdefault(uid, True)

            if reading.status == Status.CHARGING:
                self._armed[uid] = True          # 充电中不提醒，且重新武装
                continue
            if pct >= threshold + hysteresis:
                self._armed[uid] = True
                continue
            if pct > threshold:
                continue

            if not self._armed.get(uid, True):
                continue
            if now - self._last_alert.get(uid, 0) < cooldown:
                continue

            self._armed[uid] = False
            self._last_alert[uid] = now
            if self.on_alert:
                try:
                    self.on_alert(reading, threshold)
                except Exception as exc:
                    _log(f"[alert] 回调异常: {exc}")

    def _notify_device_change(self, added, removed, readings) -> None:
        if not self.settings.get("notify_on_device_change", True):
            return
        if self.on_device_change:
            try:
                self.on_device_change(added, removed, readings)
            except Exception as exc:
                _log(f"[device] 回调异常: {exc}")
