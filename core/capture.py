"""抓包会话管理：起停 USBPcapCMD 子进程，供抓包向导使用。

USBPcapCMD 一次只能抓一个集线器，所以同时抓多个就要多个进程——1.0 的批处理
脚本就是起两个。这里用同样的思路，但把进程句柄留在内存里，能干净地停。
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

from core import system
from core.paths import capture_dir

CREATE_NO_WINDOW = 0x08000000


class CaptureSession:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._procs: list[subprocess.Popen] = []
        self._files: list[Path] = []
        self._started_at = 0.0
        self._stop_at = 0.0
        self._timer: threading.Timer | None = None
        self.active = False
        self.error = ""
        self.note = ""

    # ---- 状态 ------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            files = []
            for p in self._files:
                try:
                    files.append({"name": p.name, "size": p.stat().st_size})
                except OSError:
                    files.append({"name": p.name, "size": 0})
            return {
                "active": self.active,
                "elapsed": round(time.time() - self._started_at, 1) if self.active else 0,
                "remaining": max(0, round(self._stop_at - time.time(), 1)) if self._stop_at else 0,
                "files": files,
                "total_bytes": sum(f["size"] for f in files),
                "error": self.error,
                "note": self.note,
            }

    # ---- 起停 ------------------------------------------------------
    def start(self, hubs: list[int], seconds: int = 60) -> dict:
        with self._lock:
            if self.active:
                return {"ok": False, "error": "已有抓包在进行中"}
            self.error, self.note = "", ""

            if not system.usbpcap_installed():
                return {"ok": False, "error": "没找到 USBPcapCMD.exe，请先安装 USBPcap",
                        "code": "no_usbpcap"}
            if not system.is_admin():
                return {"ok": False, "error": "抓 USB 需要管理员权限",
                        "code": "need_admin"}

            cmd_path = system.usbpcap_cmd()
            assert cmd_path is not None
            stamp = time.strftime("%Y%m%d_%H%M%S")
            folder = capture_dir()
            self._procs, self._files = [], []

            for hub in hubs:
                target = folder / f"cap_{stamp}_h{hub}.pcap"
                try:
                    proc = subprocess.Popen(
                        [str(cmd_path), "-d", rf"\\.\USBPcap{hub}", "-A", "-o", str(target)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=CREATE_NO_WINDOW,
                    )
                except Exception as exc:
                    return {"ok": False, "error": f"启动 USBPcap{hub} 失败: {exc}"}
                self._procs.append(proc)
                self._files.append(target)

            time.sleep(1.2)
            alive = [p for p in self._procs if p.poll() is None]
            if not alive:
                self._procs.clear()
                self.error = "USBPcapCMD 立即退出（集线器编号不对，或权限被拒）"
                return {"ok": False, "error": self.error, "code": "start_failed"}

            self.active = True
            self._started_at = time.time()
            self._stop_at = self._started_at + max(5, seconds)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(max(5, seconds), self.stop)
            self._timer.daemon = True
            self._timer.start()
            return {"ok": True, "files": [p.name for p in self._files],
                    "seconds": seconds, "hubs": len(alive)}

    def stop(self) -> dict:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            was_active = self.active
            for proc in self._procs:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass
            deadline = time.time() + 2.0
            for proc in self._procs:
                while proc.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            self._procs.clear()
            self.active = False
            self._stop_at = 0

            files = []
            for p in self._files:
                try:
                    files.append({"name": p.name, "size": p.stat().st_size})
                except OSError:
                    files.append({"name": p.name, "size": 0})
            # 空的 pcap（只有文件头）没分析价值，直接挑出较大的那些
            useful = [f for f in files if f["size"] > 4096]
            if was_active:
                self.note = (f"已停止，产出 {len(files)} 个文件"
                             + ("（其中 " + "、".join(f["name"] for f in useful) + " 有数据）"
                                if useful else "，但都几乎没有数据"))
            return {"ok": True, "files": files, "useful": useful, "note": self.note}

    def latest_file(self) -> Path | None:
        candidates = []
        for p in self._files:
            try:
                if p.stat().st_size > 4096:
                    candidates.append((p.stat().st_size, p))
            except OSError:
                continue
        if not candidates:
            for p in capture_dir().glob("*.pcap"):
                try:
                    if p.stat().st_size > 4096:
                        candidates.append((p.stat().st_mtime, p))
                except OSError:
                    continue
        return max(candidates)[1] if candidates else None
