"""解析 USBPcap 生成的 pcap，抽出 HID 中断传输的原始报文，用于定位 T88 的电量命令。

USBPcap 伪头部（headerLen 字段给出实际长度，通常 27 字节）：
  u16 headerLen | u64 irpId | u32 status | u16 function | u8 info
  u16 bus       | u16 device | u8 endpoint | u8 transfer  | u32 dataLength
transfer: 0=iso 1=interrupt 2=control 3=bulk
endpoint: 低 7 位为端点号，最高位 1 表示 IN
"""
import struct
import sys
from collections import Counter, defaultdict


def read_pcap(path):
    with open(path, "rb") as f:
        blob = f.read()
    if len(blob) < 24:
        raise SystemExit("文件太小，不是 pcap: %s" % path)
    magic = blob[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        raise SystemExit("未知 pcap magic: %r" % magic)

    off = 24
    packets = []
    while off + 16 <= len(blob):
        ts_s, ts_us, incl, orig = struct.unpack(endian + "IIII", blob[off:off + 16])
        off += 16
        data = blob[off:off + incl]
        off += incl
        if len(data) < 2:
            continue
        packets.append((ts_s + ts_us / 1e6, data))
    return packets


def parse_usbpcap(data):
    if len(data) < 27:
        return None
    hdr_len = struct.unpack("<H", data[:2])[0]
    if hdr_len < 27 or hdr_len > len(data):
        return None
    irp_id, status, function, info, bus, device, endpoint, transfer, dlen = \
        struct.unpack("<QIHBHHBBI", data[2:27])
    payload = data[hdr_len:hdr_len + dlen]
    return {
        "irp": irp_id, "status": status, "function": function,
        "bus": bus, "device": device, "ep": endpoint & 0x7F,
        "dir": "IN" if endpoint & 0x80 else "OUT",
        "transfer": transfer, "payload": payload,
    }


def main(paths):
    all_pkts = []
    for p in paths:
        try:
            pkts = read_pcap(p)
        except SystemExit as e:
            print("skip", p, e)
            continue
        print("%s: %d packets" % (p, len(pkts)))
        for ts, raw in pkts:
            rec = parse_usbpcap(raw)
            if rec:
                rec["ts"] = ts
                rec["file"] = p
                all_pkts.append(rec)

    print("\n传输类型分布:", Counter(r["transfer"] for r in all_pkts))
    print("端点分布:", Counter((r["bus"], r["device"], r["dir"], r["ep"]) for r in all_pkts).most_common(20))

    # 只保留中断传输
    intr = [r for r in all_pkts if r["transfer"] == 1 and r["payload"]]
    print("\n中断报文数:", len(intr))

    # 按设备分组，输出非空报文的唯一指纹
    groups = defaultdict(Counter)
    for r in intr:
        key = (r["bus"], r["device"], r["dir"], r["ep"])
        groups[key][bytes(r["payload"])] += 1

    for key in sorted(groups, key=lambda k: -sum(groups[k].values())):
        total = sum(groups[key].values())
        print("\n=== bus=%s dev=%s %s ep=%s  (%d 条, %d 种)" %
              (key[0], key[1], key[2], key[3], total, len(groups[key])))
        for payload, cnt in groups[key].most_common(8):
            head = payload[:12].hex(" ")
            flag = ""
            if payload and payload[0] in (0x06, 0x05, 0x07):
                flag = "  <== 疑似命令帧"
            if any(b != 0 for b in payload[2:]):
                flag += "  [含非零数据]"
            print("   x%-4d len=%-3d %s%s" % (cnt, len(payload), head, flag))

    # 专门找 06 xx 命令与对应应答
    print("\n\n########## 命令帧 / 应答帧提取 ##########")
    for r in intr:
        pl = bytes(r["payload"])
        if pl and pl[0] == 0x06:
            tail = pl[2:]
            nonzero = [(i, b) for i, b in enumerate(tail, start=2) if b]
            print("OUT t=%.3f bus=%s dev=%s ep=%s len=%d %s | 非零字节: %s" %
                  (r["ts"], r["bus"], r["device"], r["ep"], len(pl), pl[:8].hex(" "),
                   nonzero[:10] if nonzero else "无"))
    print()
    for r in intr:
        pl = bytes(r["payload"])
        if r["dir"] == "IN" and len(pl) >= 3:
            nonzero = [(i, b) for i, b in enumerate(pl) if b]
            if len(nonzero) > 2:   # 有实际数据的应答
                print("IN  t=%.3f bus=%s dev=%s ep=%s len=%d %s | 非零字节: %s" %
                      (r["ts"], r["bus"], r["device"], r["ep"], len(pl), pl[:8].hex(" "), nonzero[:12]))


if __name__ == "__main__":
    args = sys.argv[1:] or [r"F:\work1\usb1.pcap", r"F:\work1\usb2.pcap"]
    main(args)
