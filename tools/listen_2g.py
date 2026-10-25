"""10 秒自测：键盘到底有没有在 2.4G 链路上发数据？

用法：
    python tools/listen_2g.py            # 默认 10 秒
    python tools/listen_2g.py 20         # 20 秒
    python tools/listen_2g.py --vid 05AC --pid 024F 15

运行期间请**持续敲键盘**：
* 有报文输出  -> 键盘确实在 2.4G 上，可以继续抓包找电量字节
* 一条都没有  -> 键盘不在 2.4G（还在蓝牙/关机/接收器未对码），先解决连接再谈电量

不需要管理员权限，不会向设备写任何东西（只读 IN 端点）。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("tools", 1)[0].rstrip("\\/"))

from providers import hid_vendor as hv  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:]]
    vid, pid = 0x05AC, 0x024F
    seconds = 10.0
    i = 0
    while i < len(args):
        if args[i] == "--vid":
            vid = int(args[i + 1], 16); i += 2
        elif args[i] == "--pid":
            pid = int(args[i + 1], 16); i += 2
        else:
            seconds = float(args[i]); i += 1

    targets = [x for x in hv.enumerate_hid() if x["vid"] == vid and x["pid"] == pid and x["in_len"] > 0]
    if not targets:
        print(f"没找到 {vid:04X}:{pid:04X} 的输入接口（键盘没插？）")
        return 1

    print(f"{vid:04X}:{pid:04X} 共 {len(targets)} 个输入接口，监听 {seconds:.0f} 秒")
    print("==> 现在开始持续敲键盘\n")

    handles = []
    for info in targets:
        h = hv._open_rw(info["path"])
        if h:
            handles.append((info, h))
    if not handles:
        print("接口都被占用了（关闭厂商软件后重试）")
        return 1

    deadline = time.time() + seconds
    seen = 0
    try:
        while time.time() < deadline:
            for info, h in handles:
                data = hv._io(h, bytes(info["in_len"]), write=False, timeout_ms=300)
                if data and any(data):
                    seen += 1
                    ep = f"UP=0x{info['usage_page']:04X}/U=0x{info['usage']:04X}"
                    print(f"  [{ep}] len={len(data)}  {data.hex(' ')}")
    finally:
        for _, h in handles:
            hv.kernel32.CloseHandle(h)

    print(f"\n共收到 {seen} 条非空报文")
    if seen:
        print("结论：键盘在 2.4G 上正常发数据 —— 可以抓包找电量字节了")
        return 0
    print("结论：这条链路上没有任何输入报文。键盘大概率不在 2.4G：")
    print("  · 还在蓝牙模式（去 Windows 蓝牙列表看键盘是否仍显示已连接）")
    print("  · 切到了另一个蓝牙通道（BT2/BT3），不是 2.4G 档")
    print("  · 接收器与键盘没对上码（需要按键盘的 2.4G 对码组合键）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
