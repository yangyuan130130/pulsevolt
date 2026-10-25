"""pcap 分析：从一堆 USB 报文里找出「请求帧 → 应答帧」的问答对，并定位电量字节。

这一层不碰设备，纯离线分析，所以可以放心大胆地穷举——它是抓包破解向导的
大脑，用户抓完包只要点几下就能看到候选。
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

from tools import usbpcap

MAX_EXCHANGES = 40          # 最多返回这么多组问答，多了前端也看不过来
PAIR_WINDOW = 2.0           # OUT 与 IN 的最大时间间隔（秒）


def _device_names(records: list[dict]) -> dict[int, str]:
    """从控制传输的字符串描述符里捞产品名（0x12 = 03 描述符）。"""
    names: dict[int, str] = {}
    for r in records:
        if r["transfer"] != 2 or not r["payload"]:
            continue
        payload = r["payload"]
        if payload[0] != 0x12:
            continue
        try:
            text = bytes(payload[2:]).decode("utf-16-le", "ignore").strip("\x00").strip()
        except Exception:
            continue
        if text and len(text) >= 2:
            names.setdefault(r["device"], text)
    return names


def _checksum_kind(frame: bytes) -> str:
    if len(frame) < 3:
        return "unknown"
    body, last = frame[:-1], frame[-1]
    if (sum(body) & 0xFF) == last:
        return "sum8"
    x = 0
    for b in body:
        x ^= b
    if x == last:
        return "xor8"
    return "none"


def _candidates(frame: bytes, expect: int | None = None) -> list[dict]:
    out = []
    for i, v in enumerate(frame):
        if expect is not None:
            if v == expect:
                out.append({"offset": i, "value": v})
        elif 1 <= v <= 100:
            out.append({"offset": i, "value": v})
    return out


def summarize(path: str) -> dict:
    """包数、时长、设备清单——先让用户确认「抓对了没有」。"""
    pkts = usbpcap.read_pcap(path)
    if not pkts:
        return {"ok": False, "error": "文件读不出报文（格式不对或抓包为空）"}
    records = [usbpcap.parse_usbpcap(raw) for _, raw in pkts]
    records = [r for r in records if r]
    names = _device_names(records)
    per_dev = Counter(r["device"] for r in records)
    transfers = Counter(r["transfer"] for r in records)
    return {
        "ok": True,
        "file": os.path.basename(path),
        "packets": len(records),
        "duration": round(pkts[-1][0] - pkts[0][0], 1) if len(pkts) > 1 else 0,
        "devices": [
            {"dev": dev, "name": names.get(dev, ""), "packets": cnt}
            for dev, cnt in per_dev.most_common()
        ],
        "transfer_labels": {
            "interrupt": transfers.get(1, 0),
            "control": transfers.get(2, 0),
            "bulk": transfers.get(3, 0),
        },
    }


def find_exchanges(path: str, dev: int | None = None, expect: int | None = None) -> dict:
    """把 OUT→IN 配成问答对，并给每条标注校验方式与候选字节。"""
    pkts = usbpcap.read_pcap(path)
    if not pkts:
        return {"ok": False, "error": "文件读不出报文"}
    t0 = pkts[0][0]
    records = [(ts, usbpcap.parse_usbpcap(raw)) for ts, raw in pkts]
    records = [(ts, r) for ts, r in records if r]
    names = _device_names([r for _, r in records])

    pending: dict[tuple[int, int], tuple[float, bytes]] = {}
    exchanges: list[dict] = []
    for ts, r in records:
        if r["transfer"] != 1 or not r["payload"]:
            continue
        if dev is not None and r["device"] != dev:
            continue
        payload = bytes(r["payload"])
        key = (r["device"], r["ep"])
        if r["dir"] == "OUT":
            # 请求帧通常不长，但也不能太短（1~2 字节多半是别的用途）
            if len(payload) >= 3:
                pending[key] = (ts, payload)
            continue
        prev = pending.get(key)
        if prev is None:
            continue
        if ts - prev[0] > PAIR_WINDOW:
            pending.pop(key, None)
            continue
        out_frame = prev[1]
        in_frame = payload
        if len(out_frame) != len(in_frame):
            # 长度对不上多半不是一问一答；仍然记下，但标出来
            pass
        exchanges.append({
            "dev": r["device"],
            "ep": r["ep"],
            "t": round(ts - t0, 2),
            "out": out_frame.hex(" "),
            "in": in_frame.hex(" "),
            "len": len(in_frame),
            "checksum": _checksum_kind(out_frame),
            "echo": len(out_frame) > 1 and len(in_frame) > 1 and out_frame[1] == in_frame[1],
            "same_len": len(out_frame) == len(in_frame),
            # candidates 始终给全（所有 1~100 的字节），hits 只标等于 expect 的。
            # 早先版本给了 expect 就只返回命中项，用户填差 1% 时整页空白，
            # 看起来像工具坏了 —— 保留候选列表就再也不会误判。
            "candidates": _candidates(in_frame),
            "hits": [c for c in _candidates(in_frame)
                     if expect is not None and c["value"] == expect],
            "name": names.get(r["device"], ""),
        })
        pending.pop(key, None)

    # 同一通道出现多次问答时，按通道聚合一下，方便用户判断「哪条命令是周期性的」
    per_channel = Counter((e["dev"], e["ep"], e["len"]) for e in exchanges)

    exchanges.sort(key=lambda e: (e["t"]))
    return {
        "ok": True,
        "file": os.path.basename(path),
        "names": {str(k): v for k, v in names.items()},
        "exchanges": exchanges[:MAX_EXCHANGES],
        "total": len(exchanges),
        "channels": [
            {"dev": d, "ep": e, "len": n, "count": c}
            for (d, e, n), c in per_channel.most_common()
        ],
        "expect": expect,
    }


def list_captures() -> list[dict]:
    from core.paths import capture_dir

    out = []
    for path in sorted(capture_dir().glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            st = path.stat()
            out.append({"name": path.name, "size": st.st_size,
                        "mtime": st.st_mtime})
        except OSError:
            continue
    return out[:30]
