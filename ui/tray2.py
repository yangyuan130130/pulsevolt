"""电之脉动 2.0 —— 托盘侧的精简实现。

1.0 的托盘菜单是「设备明细」，2.0 的主界面搬到网页了，托盘只留最常用的三项：
打开面板 / 立即刷新 / 退出。图标仍复用 1.0 的电池绘制，保证两版观感一致。
"""
from __future__ import annotations

import threading
import webbrowser

import pystray
from pystray import Menu, MenuItem

from core.models import Status
from ui.render import draw_icon

KIND_LABEL = {"keyboard": "键盘", "mouse": "鼠标", "gamepad": "手柄",
              "headset": "耳机", "other": "外设"}


class TrayApp:
    def __init__(self, url: str, manager, on_quit):
        self.url = url
        self.manager = manager
        self.on_quit = on_quit
        self.icon: pystray.Icon | None = None

    # ---- 菜单 ------------------------------------------------------
    def _lowest(self):
        try:
            return self.manager.lowest()
        except Exception:
            return None

    def _title(self) -> str:
        low = self._lowest()
        if low and low.percent is not None:
            return f"电之脉动 · {low.device.name} {low.percent}%"
        return "电之脉动"

    def _menu(self):
        low = self._lowest()
        head = (f"{low.device.name}  {low.percent}%" if low and low.percent is not None
                else "暂无读数")
        return Menu(
            MenuItem(head, None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("打开控制台", self._open_panel, default=True),
            MenuItem("立即刷新", self._refresh),
            Menu.SEPARATOR,
            MenuItem("退出", self._quit),
        )

    # ---- 动作 ------------------------------------------------------
    def _open_panel(self, *_):
        webbrowser.open(self.url)

    def _refresh(self, *_):
        try:
            self.manager.request_refresh()
        except Exception:
            pass

    def _quit(self, *_):
        try:
            self.icon.stop()
        finally:
            self.on_quit()

    # ---- 刷新 ------------------------------------------------------
    def refresh(self, *_):
        if self.icon is None:
            return
        low = self._lowest()
        if low and low.percent is not None:
            charging = low.status == Status.CHARGING
            offline = low.status == Status.DISCONNECTED
            self.icon.icon = draw_icon(low.percent,
                                      1 if charging else 0,
                                      offline)
        else:
            self.icon.icon = draw_icon(None)
        self.icon.title = self._title()
        try:
            self.icon.menu = self._menu()
        except Exception:
            pass

    def start(self):
        self.icon = pystray.Icon("pulsevolt", draw_icon(None), self._title(), self._menu())
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        self.manager.on_update = lambda *_: self.refresh()


def run_tray(url: str, manager, on_quit) -> TrayApp:
    app = TrayApp(url, manager, on_quit)
    app.start()
    return app
