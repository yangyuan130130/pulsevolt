"""用「两次抓包对比法」定位电量字节。

原理：电量变化慢，其他报文（按键、移动、心跳）变化快。在**不同电量**下各抓一次，
同一设备同一端点里「只在两次之间稳定不同、且数值都在 0~100」的那个字节，
基本就是电量。

用法
----
    python tools/pcap_diff.py a.pcap b.pcap
    python tools/pcap_diff.py a.pcap b.pcap --expect 43 41      # 两次实测电量，用于打分

抓包方法见 README「USBPcap 排查流程」。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usbpcap  # noqa: E402


def load(path: str):
    packets = usbpcap.read_pcap(path)
    frames = defaultdict(list)      # (dev, dir, ep, len) -> [payload]
    names = {}
    for _, raw in packets:
        rec = usbpcap.parse_usbpcap(raw)
        if not rec:
            continue
        payload = rec["payload"]
        if rec["transfer"] == 1 and payload:
            frames[(rec["device"], rec["dir"], rec["ep"], len(payload))].append(bytes(payload))
        elif rec["transfer"] == 2 and payload and payload[0] == 0x12:
            try:
                name = payload[2:].decode("utf-16-le", "ignore").strip("\x00")
                if name:
                    names.setdefault(rec["device"], name)
            except Exception:
                pass
    return frames, names


def modal(frames: list[bytes]) -> bytes:
    """逐字节取众数，得到「稳定帧」。"""
    length = len(frames[0])
    out = bytearray(length)
    for i in range(length):
        out[i] = Counter(f[i] for f in frames).most_common(1)[0][0]
    return bytes(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="对比两次抓包定位电量字节")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--expect", nargs=2, type=int, metavar=("PCT_A", "PCT_B"),
                    help="两次抓包时的实际电量（已知就填，用来给候选打分）")
    ap.add_argument("--min-frames", type=int, default=3, help="最少帧数才参与比较")
    args = ap.parse_args()

    fa, na = load(args.a)
    fb, nb = load(args.b)
    common = set(fa) & set(fb)

    print(f"A: {args.a}  {sum(len(v) for v in fa.values())} 帧")
    print(f"B: {args.b}  {sum(len(v) for v in fb.values())} 帧")
    print(f"共有端点组 {len(common)} 个\n")

    candidates = []
    for key in sorted(common):
        dev, direction, ep, length = key
        if direction != "IN" or len(fa[key]) < args.min_frames or len(fb[key]) < args.min_frames:
            continue
        ma, mb = modal(fa[key]), modal(fb[key])
        diffs = [(i, ma[i], mb[i]) for i in range(length) if ma[i] != mb[i]]
        if not diffs:
            continue
        name = na.get(dev) or nb.get(dev) or "?"
        print(f"dev={dev} ep={ep} len={length} [{name}]  差异 {len(diffs)} 处")
        print(f"   A: {ma.hex(' ')}")
        print(f"   B: {mb.hex(' ')}")
        for i, va, vb in diffs:
            hint = ""
            if 0 <= va <= 100 and 0 <= vb <= 100:
                hint = "  <= 两者都在 0~100，疑似电量"
                candidates.append((dev, name, ep, length, i, va, vb))
        print()

    if not candidates:
        print("没有找到「两次都落在 0~100 且不同」的字节。")
        print("可能原因：两次电量相同 / 电量只在应答帧（OUT 请求后的一次性 IN）里 / 设备根本没上报。")
        return 1

    print("候选字节：")
    for dev, name, ep, length, i, va, vb in candidates:
        score = ""
        if args.expect:
            ea, eb = args.expect
            if (va, vb) == (ea, eb):
                score = "  ★ 与实际电量完全吻合"
            elif abs((va - vb) - (ea - eb)) <= 1:
                score = "  ★ 变化量与实际吻合（可能有偏移）"
        print(f"  dev={dev} [{name}] ep={ep} offset={i}: {va} -> {vb}{score}")
    print("\n把命中的 offset 写进 settings.json 的 hid_devices[].response_offset，")
    print("请求帧填当时驱动/工具发出的那一帧（用 pcap 里的 OUT 报文即可）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
