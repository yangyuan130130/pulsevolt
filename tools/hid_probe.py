"""HID 接口枚举 / 私有协议试发工具（2.4G 接入的第一步）。

用法
----
    python tools/hid_probe.py                          # 列出全部 HID 接口
    python tools/hid_probe.py --filter 05AC:024F       # 只看某 VID:PID
    python tools/hid_probe.py --filter 05AC:024F --vendor
                                                       # 只看厂商自定义页（UsagePage >= 0xFF00）
    python tools/hid_probe.py --filter 05AC:024F --page FF60 --send "06 14" --len 33

默认**只枚举不发包**。--send 会真的写一帧到设备，请确认你清楚这一帧的含义
（同一接口可能承载改配置、进 DFU 的命令）。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hid_vendor as hv  # noqa: E402


def parse_filter(text: str) -> tuple[int, int]:
    vid, pid = text.replace(":", " ").split()
    return int(vid, 16), int(pid, 16)


def main() -> int:
    ap = argparse.ArgumentParser(description="HID 接口枚举与试发")
    ap.add_argument("--filter", help="VID:PID，如 05AC:024F")
    ap.add_argument("--vendor", action="store_true", help="只显示厂商自定义 UsagePage(>=0xFF00)")
    ap.add_argument("--page", help="按 UsagePage 过滤，如 FF60")
    ap.add_argument("--send", help="要发送的十六进制帧，如 \"06 14\"")
    ap.add_argument("--len", type=int, default=33, help="报文长度（默认 33）")
    ap.add_argument("--via", choices=("output", "feature"), default="output")
    ap.add_argument("--timeout", type=int, default=3000, help="读超时 ms")
    args = ap.parse_args()

    infos = hv.enumerate_hid()
    if args.filter:
        vid, pid = parse_filter(args.filter)
        infos = [i for i in infos if i["vid"] == vid and i["pid"] == pid]
    if args.vendor:
        infos = [i for i in infos if i["usage_page"] >= 0xFF00]
    if args.page:
        want = int(args.page, 16)
        infos = [i for i in infos if i["usage_page"] == want]

    print(f"{'VID:PID':<12}{'UsagePage':<11}{'Usage':<8}{'in':>4}{'out':>5}{'feat':>6}  path")
    print("-" * 118)
    for i in infos:
        print(f"{i['vid']:04X}:{i['pid']:04X}   "
              f"0x{i['usage_page']:04X}     0x{i['usage']:04X}  "
              f"{i['in_len']:>4}{i['out_len']:>5}{i['feat_len']:>6}  {i['path'][-46:]}")
    print(f"\n共 {len(infos)} 个接口")

    if not args.send:
        print("\n（只枚举。要试发请加 --send \"06 14\" --len 33）")
        return 0

    frame = hv._parse_frame(args.send, args.len)
    print(f"\n发送帧({args.len}B, via={args.via}): {frame.hex(' ')}")
    for info in infos:
        handle = hv._open_rw(info["path"])
        if handle is None:
            print(f"  {info['path'][-30:]} -> 打开失败（可能被厂商软件占用）")
            continue
        try:
            if args.via == "feature":
                inbuf = ctypes.create_string_buffer(bytes(frame), args.len)
                outbuf = ctypes.create_string_buffer(args.len)
                ok1 = hv.hid.HidD_SetFeature(handle, inbuf, args.len)
                ok2 = hv.hid.HidD_GetFeature(handle, outbuf, args.len)
                resp = outbuf.raw[:args.len] if (ok1 and ok2) else None
            else:
                hv._io(handle, frame, write=True)
                resp = hv._io(handle, bytes(args.len), write=False, timeout_ms=args.timeout)
        finally:
            hv.kernel32.CloseHandle(handle)
        if resp:
            print(f"  {info['path'][-30:]} <- {resp.hex(' ')}")
        else:
            print(f"  {info['path'][-30:]} <- (无应答)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
