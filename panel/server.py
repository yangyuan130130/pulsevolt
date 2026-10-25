"""本地 Web 控制台后端。

为什么用 HTTP 而不是 tkinter
----------------------------
2.0 的界面信息密度很高（设备卡片、协议卡片、分步向导、实时进度），
用原生控件堆出来又慢又难看。改成「本地 HTTP 服务 + 浏览器窗口」后，
界面用 HTML/CSS 写，改样式不用碰 Python，而且同一套页面在源码运行和
打包 exe 里表现完全一致。

只监听 127.0.0.1，并校验 Host 头（防 DNS rebinding），不做任何对外暴露。
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import analyzer, protocols as protocol_lib, settings as settings_mod, system
from core.capture import CaptureSession
from core.manager import Manager
from core.models import KIND_LABEL, STATUS_LABEL, TRANSPORT_LABEL, Status
from core.paths import capture_dir, is_frozen, resource_dir
from core.smarttest import SmartTest

APP_NAME = "电之脉动"
APP_VERSION = "2.0.0"


def static_dir() -> Path:
    if is_frozen():
        return resource_dir() / "panel" / "static"
    return Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# 全局上下文
# ---------------------------------------------------------------------------

class AppContext:
    def __init__(self) -> None:
        self.settings = settings_mod.load()
        protocol_lib.sync_settings()
        self.settings = settings_mod.load()
        self.manager = Manager(self.settings)
        self.manager.refresh_once()
        self.manager.start()
        self.smart = SmartTest(self.manager)
        self.capture = CaptureSession()
        self._lock = threading.RLock()
        self.started_at = time.time()

    def rebuild_manager(self) -> None:
        """协议启停后必须重建：Provider 在构造时就装配好了 hid_devices。"""
        with self._lock:
            try:
                self.manager.stop()
            except Exception:
                pass
            self.settings = settings_mod.load()
            self.manager = Manager(self.settings)
            self.smart.manager = self.manager
            self.manager.refresh_once()
            self.manager.start()

    def devices(self) -> list[dict]:
        out = []
        for r in self.manager.snapshot():
            out.append({
                "uid": r.device.uid,
                "name": r.device.name,
                "kind": r.device.kind.value,
                "kind_label": KIND_LABEL.get(r.device.kind, "外设"),
                "percent": r.percent,
                "status": r.status.value,
                "status_label": STATUS_LABEL.get(r.status, "未知"),
                "connected": r.status in (Status.CONNECTED, Status.CHARGING),
                "charging": r.status == Status.CHARGING,
                "transport": r.device.transport.value,
                "transport_label": TRANSPORT_LABEL.get(r.device.transport, "未知"),
                "source": r.source or "",
                "detail": r.detail or "",
            })
        return out


CTX: AppContext | None = None


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------

def _protocol_view(proto: dict, active_names: set[str]) -> dict:
    view = dict(proto)
    view["active"] = proto["name"] in active_names
    view["channel_text"] = (
        f"{proto['channel'].get('usage_page', '?')} / {proto['channel'].get('usage') or '0x?'}"
        f" · {proto['channel'].get('report_len', '?')}B"
    )
    view["match_text"] = f"{proto['match'].get('vid', '?')}:{proto['match'].get('pid', '?')}"
    view["checksum_label"] = {
        "sum8": "累加和", "xor8": "异或", "none": "无校验", "unknown": "未知",
    }.get(proto.get("checksum", "unknown"), proto.get("checksum"))
    return view


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"PulseVolt/{APP_VERSION}"
    protocol_version = "HTTP/1.0"

    # ---- 基础 ------------------------------------------------------
    def log_message(self, fmt, *args):   # 静默，别刷控制台
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host.startswith("127.0.0.1") or host.startswith("localhost")

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _file(self, path: Path, cache: bool = False) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not Found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- 路由 ------------------------------------------------------
    def do_GET(self) -> None:   # noqa: N802
        if not self._host_ok():
            self.send_error(403, "Forbidden")
            return
        ctx = CTX
        assert ctx is not None
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            self._file(static_dir() / "index.html")
            return
        if route.startswith("/static/"):
            name = route[len("/static/"):]
            target = (static_dir() / name).resolve()
            if static_dir().resolve() not in target.parents:
                self.send_error(403, "Forbidden")
                return
            self._file(target, cache=True)
            return
        if route == "/favicon.ico":
            ico = static_dir().parent.parent / "icon.ico"
            if ico.exists():
                self._file(ico, cache=True)
            else:
                self.send_response(204)
                self.end_headers()
            return

        if route == "/api/state":
            self._json(self._state(ctx))
            return
        if route == "/api/devices":
            self._json({"devices": ctx.devices()})
            return
        if route == "/api/protocols":
            names = {d["name"] for d in ctx.devices()}
            self._json({"protocols": [_protocol_view(p, names)
                                      for p in protocol_lib.all_protocols()]})
            return
        if route == "/api/protocols/export":
            pid = (query.get("id") or [""])[0]
            payload = protocol_lib.export_payload(pid)
            if payload is None:
                self._json({"ok": False, "error": "协议不存在"}, 404)
                return
            self._json({"ok": True, "protocol": payload})
            return
        if route == "/api/smarttest":
            self._json(ctx.smart.snapshot())
            return
        if route == "/api/capture/status":
            self._json(ctx.capture.status())
            return
        if route == "/api/captures":
            self._json({"captures": analyzer.list_captures()})
            return
        if route == "/api/analyze":
            self._json(self._analyze(ctx, query))
            return
        if route == "/api/env":
            self._json(self._env())
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:   # noqa: N802
        if not self._host_ok():
            self.send_error(403, "Forbidden")
            return
        ctx = CTX
        assert ctx is not None
        route = urllib.parse.urlparse(self.path).path
        data = self._body()

        try:
            if route == "/api/protocols/save":
                self._json(self._save_protocol(ctx, data))
                return
            if route == "/api/protocols/delete":
                pid = str(data.get("id", ""))
                ok = protocol_lib.remove(pid)
                protocol_lib.sync_settings()
                ctx.rebuild_manager()
                self._json({"ok": ok})
                return
            if route == "/api/protocols/toggle":
                pid = str(data.get("id", ""))
                enabled = bool(data.get("enabled", True))
                proto = protocol_lib.set_enabled(pid, enabled)
                protocol_lib.sync_settings()
                ctx.rebuild_manager()
                self._json({"ok": proto is not None, "protocol": proto})
                return
            if route == "/api/protocols/import":
                payload = data.get("protocol") or data
                rename = str(data.get("rename", ""))
                proto = protocol_lib.import_payload(payload, rename)
                protocol_lib.sync_settings()
                ctx.rebuild_manager()
                self._json({"ok": True, "protocol": proto})
                return
            if route == "/api/smarttest/start":
                started = ctx.smart.run_async()
                self._json({"ok": started, "error": "" if started else "上一轮还在跑"})
                return
            if route == "/api/capture/start":
                hubs = data.get("hubs") or [1, 2]
                seconds = int(data.get("seconds", 60))
                result = ctx.capture.start([int(h) for h in hubs], seconds)
                self._json(result, 200 if result.get("ok") else 400)
                return
            if route == "/api/capture/stop":
                self._json(ctx.capture.stop())
                return
            if route == "/api/analyze":
                self._json(self._analyze(ctx, {"file": [data.get("file", "")],
                                               "expect": [str(data.get("expect", ""))],
                                               "dev": [str(data.get("dev", ""))]}))
                return
            if route == "/api/refresh":
                ctx.manager.request_refresh()
                self._json({"ok": True})
                return
            if route == "/api/settings":
                self._json(self._save_settings(ctx, data))
                return
            if route == "/api/relaunch-admin":
                ok = system.relaunch_as_admin()
                self._json({"ok": ok, "already": system.is_admin()})
                return
            if route == "/api/autostart":
                ok = system.set_autostart(bool(data.get("enable", False)))
                self._json({"ok": ok, "enabled": system.autostart_state()})
                return
            if route == "/api/open-folder":
                target = str(capture_dir())
                system.open_path(target)
                self._json({"ok": True, "path": target})
                return
            if route == "/api/quit":
                self._json({"ok": True})
                threading.Timer(0.4, _shutdown).start()
                return
        except Exception as exc:
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        self.send_error(404, "Not Found")

    # ---- 具体实现 --------------------------------------------------
    def _env(self) -> dict:
        return {
            "app": {"name": APP_NAME, "version": APP_VERSION},
            "admin": system.is_admin(),
            "usbpcap": system.usbpcap_installed(),
            "usbpcap_path": str(system.usbpcap_cmd() or ""),
            "hubs": system.probe_hubs(),
            "autostart": system.autostart_state(),
            "frozen": is_frozen(),
            "capture_dir": str(capture_dir()),
            "uptime": round(time.time() - (CTX.started_at if CTX else time.time()), 0),
        }

    def _state(self, ctx: AppContext) -> dict:
        devices = ctx.devices()
        names = {d["name"] for d in devices}
        return {
            "env": self._env(),
            "settings": {
                "refresh_interval": ctx.settings.get("refresh_interval"),
                "low_battery_threshold": ctx.settings.get("low_battery_threshold"),
                "show_disconnected": ctx.settings.get("show_disconnected"),
                "hide_unknown": ctx.settings.get("hide_unknown"),
                "notify_on_device_change": ctx.settings.get("notify_on_device_change"),
            },
            "devices": devices,
            "protocols": [_protocol_view(p, names) for p in protocol_lib.all_protocols()],
            "smarttest": ctx.smart.snapshot(),
            "capture": ctx.capture.status(),
            "captures": analyzer.list_captures(),
        }

    def _save_protocol(self, ctx: AppContext, data: dict) -> dict:
        proto = data.get("protocol") or data
        if not proto.get("name"):
            return {"ok": False, "error": "协议名不能为空"}
        saved = protocol_lib.save(proto)
        protocol_lib.sync_settings()
        ctx.rebuild_manager()
        return {"ok": True, "protocol": saved}

    def _save_settings(self, ctx: AppContext, data: dict) -> dict:
        cfg = settings_mod.load()
        for key in ("refresh_interval", "low_battery_threshold",
                    "show_disconnected", "hide_unknown", "notify_on_device_change"):
            if key in data:
                cfg[key] = data[key]
        settings_mod.save(cfg)
        ctx.rebuild_manager()
        return {"ok": True}

    def _analyze(self, ctx: AppContext, query: dict) -> dict:
        name = (query.get("file") or [""])[0]
        expect_raw = (query.get("expect") or [""])[0]
        dev_raw = (query.get("dev") or [""])[0]
        expect = int(expect_raw) if expect_raw.isdigit() else None
        dev = int(dev_raw) if dev_raw.isdigit() else None

        if name:
            target = capture_dir() / name
            if not target.exists():
                return {"ok": False, "error": f"找不到文件 {name}"}
        else:
            latest = ctx.capture.latest_file()
            if latest is None:
                return {"ok": False, "error": "还没有可分析的抓包文件"}
            target = latest

        summary = analyzer.summarize(str(target))
        if not summary.get("ok"):
            return summary
        detail = analyzer.find_exchanges(str(target), dev=dev, expect=expect)
        return {"ok": True, "summary": summary, "detail": detail,
                "file": target.name}


def _shutdown() -> None:
    try:
        if CTX:
            CTX.capture.stop()
            CTX.manager.stop()
    finally:
        os._exit(0)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def serve(port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    global CTX
    CTX = AppContext()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    actual = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, name="panel-http", daemon=True).start()
    return httpd, actual
