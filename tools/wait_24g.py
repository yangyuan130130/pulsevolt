"""等待键盘从蓝牙切到 2.4G（BLE 连接断开即视为已切换）。

用法：python tools/wait_24g.py [超时秒数]
退出码：0 = 已切换，1 = 超时未切换
"""
from __future__ import annotations

import asyncio
import sys
import time

ADDR = 0xDC826655EA1D  # T88-BT1


async def _connected() -> bool:
    from winrt.windows.devices.bluetooth import (
        BluetoothConnectionStatus,
        BluetoothLEDevice,
    )

    dev = await BluetoothLEDevice.from_bluetooth_address_async(ADDR)
    if dev is None:
        return False
    try:
        return dev.connection_status == BluetoothConnectionStatus.CONNECTED
    finally:
        try:
            dev.close()
        except Exception:
            pass


def main() -> int:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    start = time.time()
    last = None
    while time.time() - start < timeout:
        try:
            state = asyncio.run(_connected())
        except Exception:
            state = False
        if state != last:
            print(("蓝牙已连接，等待切换..." if state else "蓝牙已断开 -> 判定为 2.4G"))
            last = state
        if not state:
            print("SWITCHED")
            return 0
        time.sleep(2)
    print("TIMEOUT 仍未切换")
    return 1


if __name__ == "__main__":
    sys.exit(main())
