"""托盘 UI：图标 + 悬浮提示 + 右键菜单 + 气泡通知。

菜单排版说明：pystray 的菜单是系统原生控件，无法改字体和颜色，所以靠
**等宽对齐 + 状态符号**来做出「表格感」：左侧类型/名称补齐，中间百分比右对齐，
右侧连接方式与状态。中文按 2 列宽度计算，否则列会错位。
"""
from __future__ import annotations

import sys
import time

import pystray
from pystray import Menu, MenuItem

from core.models import KIND_LABEL, STATUS_LABEL, TRANSPORT_LABEL, Reading, Status
from core.settings import save as save_settings
from ui.render import draw_icon, draw_placeholder

NAME_COL = 22   # 名称列显示宽度（中文算 2）


def _disp_len(text: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_len(text))


def _dot(reading: Reading) -> str:
    if reading.status == Status.CHARGING:
        return "⚡"
    if reading.status == Status.CONNECTED:
        return "●"
    if reading.status == Status.DISCONNECTED:
        return "○"
    return "?"


class TrayApp:
    def __init__(self, manager, settings: dict):
        self.manager = manager
        self.settings = settings
        self.snapshot: list[Reading] = []
        self._icon: pystray.Icon | None = None
        self._last_refresh = 0.0

    # ---- 菜单 ----------------------------------------------------------
    def _device_rows(self):
        def detail_action(reading: Reading):
            def action(_icon, _item):
                self._show_detail(reading)
            return action

        rows = []
        for reading in self.snapshot:
            pct = "   —" if reading.percent is None else f"{reading.percent:>3}%"
            left = f"{_dot(reading)} {KIND_LABEL.get(reading.device.kind, '外设')}  {reading.device.name}"
            right = TRANSPORT_LABEL.get(reading.device.transport, "")
            if reading.status == Status.DISCONNECTED:
                right += " · " + STATUS_LABEL[Status.DISCONNECTED]
            elif reading.status == Status.CHARGING:
                right += " · 充电中"
            rows.append(MenuItem(f"{_pad(left, NAME_COL)}{pct}   {right}",
                                 detail_action(reading), enabled=True))
        if not rows:
            rows.append(MenuItem("（未发现可读外设）", None, enabled=False))
        return rows

    def _threshold_items(self):
        def action_for(value: int):
            def action(_icon, _item):
                self._set_threshold(value)
            return action

        def checked_for(value: int):
            def checked(_item):
                return int(self.settings["low_battery_threshold"]) == value
            return checked

        return [MenuItem(f"{v}%", action_for(v), checked=checked_for(v), radio=True)
                for v in (10, 15, 20, 30, 40)]

    def _pin_items(self):
        def action_for(uid: str):
            def action(_icon, _item):
                self._set_pin(uid)
            return action

        def checked_for(uid: str):
            def checked(_item):
                return (self.settings.get("pin_device") or "") == uid
            return checked

        items = [MenuItem("自动（电量最低）", action_for(""), checked=checked_for(""), radio=True),
                 Menu.SEPARATOR]
        for reading in self.snapshot:
            items.append(MenuItem(reading.device.name, action_for(reading.device.uid),
                                  checked=checked_for(reading.device.uid), radio=True))
        return items

    def _build_menu(self) -> Menu:
        online = sum(1 for r in self.snapshot if r.status in (Status.CONNECTED, Status.CHARGING))
        lowest = self.manager.lowest()

        if self.snapshot:
            summary = f"{online} 台在线 · 共 {len(self.snapshot)} 台"
            if lowest and lowest.percent is not None:
                summary += f" · 最低 {lowest.percent}%"
        else:
            summary = "未发现设备"

        stamp = time.strftime("%H:%M:%S", time.localtime(self._last_refresh)) if self._last_refresh else "—"

        return Menu(
            MenuItem("外设电量", None, enabled=False),
            MenuItem(summary, None, enabled=False),
            Menu.SEPARATOR,
            *self._device_rows(),
            Menu.SEPARATOR,
            MenuItem("立即刷新", lambda _i, _it: self.manager.request_refresh()),
            MenuItem(f"上次刷新 {stamp}", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("低电量阈值", Menu(*self._threshold_items())),
            MenuItem("托盘图标", Menu(*self._pin_items())),
            MenuItem("隐藏读不到电量的设备",
                     lambda _i, _it: self._toggle("hide_unknown"),
                     checked=lambda _it: bool(self.settings.get("hide_unknown"))),
            MenuItem("显示已断开的设备（上次电量）",
                     lambda _i, _it: self._toggle("show_disconnected"),
                     checked=lambda _it: bool(self.settings.get("show_disconnected"))),
            MenuItem("设备上下线提醒",
                     lambda _i, _it: self._toggle("notify_on_device_change"),
                     checked=lambda _it: bool(self.settings.get("notify_on_device_change", True))),
            Menu.SEPARATOR,
            MenuItem("退出", lambda icon, _it: self._quit(icon)),
        )

    # ---- 运行 ----------------------------------------------------------
    def start(self) -> None:
        icon = pystray.Icon("peripheral-battery-tray", draw_placeholder("…"),
                            "外设电量 · 正在扫描…", self._build_menu())
        self._icon = icon
        self.manager.start()
        icon.run()

    def stop(self) -> None:
        self.manager.stop()
        if self._icon:
            self._icon.stop()

    # ---- 回调 ----------------------------------------------------------
    def refresh_ui(self, snapshot: list[Reading]) -> None:
        self.snapshot = snapshot
        self._last_refresh = time.time()
        icon = self._icon
        if icon is None:
            return

        primary = self.manager.lowest()
        percent = primary.percent if primary else None
        charging = bool(primary and primary.status == Status.CHARGING)
        offline = sum(1 for r in snapshot if r.status == Status.DISCONNECTED)

        icon.icon = draw_icon(percent, offline, charging)
        icon.title = self._tooltip(snapshot, primary)
        try:
            icon.update_menu()
        except Exception:
            pass

    def notify_alert(self, reading: Reading, threshold: int) -> None:
        if not self._icon:
            return
        self._icon.notify(
            f"{reading.device.name} 电量 {reading.percent}%，已低于 {threshold}%",
            "外设电量偏低",
        )

    def notify_device_change(self, added, removed, readings) -> None:
        if not self._icon:
            return
        if added:
            names = "、".join(readings[uid].device.name for uid in added if uid in readings)
            if names:
                self._icon.notify(f"已接入：{names}", "外设变化")
        elif removed:
            self._icon.notify(f"{len(removed)} 台设备已断开", "外设变化")

    # ---- 辅助 ----------------------------------------------------------
    @staticmethod
    def _tooltip(snapshot: list[Reading], primary: Reading | None) -> str:
        if not snapshot:
            return "外设电量 · 未发现设备"
        parts, used = [], 0
        for reading in snapshot:
            if reading.status == Status.DISCONNECTED and reading.percent is not None:
                pct = f"{reading.percent}%(离线)"
            else:
                pct = "—" if reading.percent is None else f"{reading.percent}%"
            piece = f"{reading.device.name} {pct}"
            if used + len(piece) > 96:
                parts.append("…")
                break
            parts.append(piece)
            used += _disp_len(piece) + 3
        head = "电量偏低 · " if primary and primary.percent is not None and primary.percent <= 20 else ""
        return head + "  |  ".join(parts)

    def _show_detail(self, reading: Reading) -> None:
        if not self._icon:
            return
        pct = "未知" if reading.percent is None else f"{reading.percent}%"
        body = (f"{pct} · {STATUS_LABEL.get(reading.status, '')}\n"
                f"来源：{reading.source or '—'}\n{reading.detail or ''}").strip()
        self._icon.notify(body, reading.device.name)

    def _set_threshold(self, value: int) -> None:
        self.settings["low_battery_threshold"] = value
        save_settings(self.settings)
        self.manager.request_refresh()

    def _set_pin(self, uid: str) -> None:
        self.settings["pin_device"] = uid
        save_settings(self.settings)
        self.refresh_ui(self.manager.snapshot())

    def _toggle(self, key: str) -> None:
        self.settings[key] = not bool(self.settings.get(key))
        save_settings(self.settings)
        self.manager.request_refresh()

    def _quit(self, icon) -> None:
        self.manager.stop()
        icon.stop()


def run(manager, settings: dict, test_notify: bool = False) -> int:
    app = TrayApp(manager, settings)
    manager.on_update = app.refresh_ui
    manager.on_alert = app.notify_alert
    manager.on_device_change = app.notify_device_change
    if test_notify:
        import threading

        def _fire():
            if app._icon:
                app._icon.notify("这是一条测试通知：低电量提醒走的就是这个通道", "外设电量 · 自检")

        threading.Timer(4.0, _fire).start()
    try:
        app.start()
    except KeyboardInterrupt:
        app.stop()
    except Exception as exc:
        print(f"[tray] 启动失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0
