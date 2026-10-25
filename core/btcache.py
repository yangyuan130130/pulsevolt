"""蓝牙电量缓存。

Windows 在每次蓝牙连接时会把 GATT 电量写进设备节点的
DEVPKEY_Bluetooth_Battery（{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2），
离线设备也能读到「最后一次」的值。这里一次性全量拉取，作为：

* 已连接设备的兜底值（GATT 读失败时）
* 未连接设备的「上次电量」展示
* 经典蓝牙耳机等拿不到 GATT 的设备的唯一来源
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

CREATE_NO_WINDOW = 0x08000000
KEY_BATTERY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"

_PS_SCRIPT = r"""
$ErrorActionPreference='SilentlyContinue'
$out=@()
Get-PnpDevice -Class Bluetooth -PresentOnly | Where-Object {
  $_.InstanceId -like 'BTHLE\DEV_*' -or $_.InstanceId -like 'BTHENUM\DEV_*'
} | ForEach-Object {
  $b  = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName '""" + KEY_BATTERY + r"""').Data
  $lc = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Bluetooth_LastConnectedTime').Data
  $mac = ($_.InstanceId -split '\\')[1] -replace '^DEV_',''
  $out += [pscustomobject]@{
    mac     = $mac.ToUpper()
    name    = $_.FriendlyName
    battery = $b
    last    = if ($lc) { $lc.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }
    le      = [bool]($_.InstanceId -like 'BTHLE*')
  }
}
@($out) | ConvertTo-Json -Compress -Depth 3
"""


class BtBatteryCache:
    """线程安全、带节流的蓝牙电量缓存。"""

    def __init__(self, ttl: float = 60.0, timeout: float = 25.0):
        self.ttl = ttl
        self.timeout = timeout
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._loaded_at = 0.0
        self.last_error = ""

    # ---- 对外接口 ------------------------------------------------------
    def get(self, mac: str) -> dict | None:
        """按 MAC（大小写不敏感）取一条缓存记录。"""
        self.refresh_if_stale()
        with self._lock:
            return self._data.get(mac.replace(":", "").upper())

    def items(self) -> dict[str, dict]:
        self.refresh_if_stale()
        with self._lock:
            return dict(self._data)

    def refresh_if_stale(self) -> None:
        if time.time() - self._loaded_at < self.ttl:
            return
        self.refresh()

    def refresh(self) -> None:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                capture_output=True, text=True, timeout=self.timeout,
                creationflags=CREATE_NO_WINDOW,
            )
            raw = (proc.stdout or "").strip()
            data: dict[str, dict] = {}
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    parsed = [parsed]
                for row in parsed:
                    mac = (row.get("mac") or "").upper()
                    if mac:
                        data[mac] = row
            with self._lock:
                self._data = data
                self._loaded_at = time.time()
                self.last_error = ""
        except Exception as exc:  # PowerShell 不可用/超时都不应影响主流程
            with self._lock:
                self._loaded_at = time.time()
                self.last_error = f"{type(exc).__name__}: {exc}"
