"""新设备接入辅助：枚举厂商 HID 接口 → 试发一帧 → 定位电量字节 → 写配置。

这一段逻辑放在 core 里（而不是 tools/），是因为**打包后的 exe 也要能用**：
用户手上只有一个 PeripheralBatteryTray.exe，没有源码目录、没有 Python 环境，
新买一个 2.4G 鼠标的时候，必须能靠这个 exe 自己把协议试出来。

设计上的两个自我约束：
* 只发调用方给的那一帧，**绝不遍历命令空间**（同一接口可能带改配置/进 DFU 的命令）；
* 结果的「候选字节」只做提示，最终 offset 由人确认 —— 只有人知道厂商软件此刻
  显示的是多少，程序不该替人猜。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from providers import hid_vendor as hv

# 报文长度推断：Windows 的 caps 里 out_len/in_len 是「含报告 ID」的字节数，
# 也就是我们 CreateFile 之后要读写的缓冲区长度。
def guess_length(info: dict, via: str = "output") -> int:
    if via == "feature":
        return int(info.get("feat_len") or info.get("in_len") or info.get("out_len") or 0)
    return int(info.get("out_len") or info.get("in_len") or 0)


def vendor_interfaces(vid: int | None = None, pid: int | None = None,
                      vendor_only: bool = True, page: int | None = None,
                      usage: int | None = None) -> list[dict]:
    """列出 HID 接口。vendor_only 只保留厂商自定义页（UsagePage >= 0xFF00）。"""
    rows = hv.enumerate_hid()
    if vid is not None:
        rows = [r for r in rows if r["vid"] == vid]
    if pid is not None:
        rows = [r for r in rows if r["pid"] == pid]
    if page is not None:
        rows = [r for r in rows if r["usage_page"] == page]
    if usage is not None:
        rows = [r for r in rows if r["usage"] == usage]
    if vendor_only and page is None:
        rows = [r for r in rows if r["usage_page"] >= 0xFF00]
    rows.sort(key=lambda r: (r["usage_page"], r["usage"], r["path"]))
    return rows


def try_frame(info: dict, frame_hex: str, length: int,
              via: str = "output", timeout_ms: int = 2500) -> dict:
    """把一帧原样发给设备，返回应答。

    返回 {"response": bytes|None, "error": str, "frame": bytes}
    """
    frame = hv._parse_frame(frame_hex, length)
    with hv.HID_LOCK:
        handle = hv._open_rw(info["path"])
        if handle is None:
            return {"response": None, "frame": frame,
                    "error": "打开接口失败（可能被厂商软件独占）"}
        try:
            if via == "feature":
                import ctypes
                inbuf = ctypes.create_string_buffer(bytes(frame), length)
                outbuf = ctypes.create_string_buffer(length)
                if not hv.hid.HidD_SetFeature(handle, inbuf, length):
                    return {"response": None, "frame": frame, "error": "SetFeature 失败"}
                if not hv.hid.HidD_GetFeature(handle, outbuf, length):
                    return {"response": None, "frame": frame, "error": "GetFeature 失败"}
                return {"response": outbuf.raw[:length], "frame": frame, "error": ""}
            wrote = hv._io(handle, frame, write=True)
            if wrote is None:
                return {"response": None, "frame": frame, "error": "写入失败"}
            resp = hv._io(handle, bytes(length), write=False, timeout_ms=timeout_ms)
            return {"response": resp, "frame": frame,
                    "error": "" if resp else "无应答（设备可能不在该链路 / 帧不对 / 报告 ID 缺位）"}
        finally:
            hv.kernel32.CloseHandle(handle)


def candidate_bytes(response: bytes, expect: int | None = None,
                    low: int = 1, high: int = 100) -> list[dict]:
    """挑出「像电量」的字节位置。

    expect 给定值时只返回等于该值的字节（最强信号：厂商软件此刻显示多少，
    应答里就有多少）。否则返回所有落在 [low, high] 的字节。
    """
    if not response:
        return []
    out = []
    for offset, value in enumerate(response):
        if expect is not None:
            if value == expect:
                out.append({"offset": offset, "value": value})
            continue
        if low <= value <= high:
            out.append({"offset": offset, "value": value})
    return out


def repeat_probe(info: dict, frame_hex: str, length: int, via: str = "output",
                 times: int = 3, gap: float = 0.6) -> list[bytes]:
    """连发多次，用于判断哪些字节稳定（电量稳定、按键字节乱跳）。"""
    results = []
    for i in range(max(1, times)):
        res = try_frame(info, frame_hex, length, via=via)
        if res["response"]:
            results.append(res["response"])
        if i + 1 < times:
            time.sleep(gap)
    return results


def stable_offsets(responses: list[bytes]) -> list[int]:
    """在多次采样中都相同、且值非零的字节位置。"""
    if not responses:
        return []
    n = min(len(r) for r in responses)
    return [i for i in range(n) if len({r[i] for r in responses}) == 1 and responses[0][i] != 0]


def build_spec(name: str, kind: str, info: dict, frame_hex: str, length: int,
               offset: int, via: str = "output", min_interval: float = 20,
               path_hint: str = "") -> dict:
    return {
        "name": name,
        "kind": kind,
        "vid": f"0x{info['vid']:04X}",
        "pid": f"0x{info['pid']:04X}",
        "usage_page": f"0x{info['usage_page']:04X}",
        "usage": f"0x{info['usage']:04X}",
        "path_hint": path_hint,
        "report_len": length,
        "request": frame_hex.strip(),
        "via": via,
        "response_offset": offset,
        "min_interval": min_interval,
        "enabled": True,
    }


def upsert_spec(cfg_path: Path, spec: dict) -> str:
    """把配置写进 settings.json 的 hid_devices（按 name 去重）。"""
    cfg: dict = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text("utf-8"))
        except Exception:
            cfg = {}
    devices = cfg.get("hid_devices") or []
    for i, old in enumerate(devices):
        if str(old.get("name", "")).strip().lower() == spec["name"].strip().lower():
            devices[i] = spec
            cfg["hid_devices"] = devices
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
            return "replaced"
    devices.append(spec)
    cfg["hid_devices"] = devices
    cfg.setdefault("providers", {}).setdefault("hid-vendor", True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    return "added"


def format_probe(info: dict, res: dict, expect: int | None = None,
                 samples: list[bytes] | None = None) -> list[str]:
    """把一次探测结果整理成人类可读的若干行（给控制台和 diag 文件共用）。"""
    lines = [
        f"接口      {info['vid']:04X}:{info['pid']:04X}  "
        f"UsagePage=0x{info['usage_page']:04X}  Usage=0x{info['usage']:04X}",
        f"长度      in={info['in_len']}  out={info['out_len']}  feat={info['feat_len']}",
        f"发送帧    {res['frame'].hex(' ')}",
    ]
    resp = res.get("response")
    if not resp:
        lines.append(f"应答      {res.get('error') or '无应答'}")
        return lines

    lines.append(f"应答帧    {resp.hex(' ')}")

    cands = candidate_bytes(resp, expect=expect)
    if expect is not None:
        if cands:
            lines.append(f"命中      值等于 {expect} 的字节："
                         + "、".join(f"byte[{c['offset']}]" for c in cands))
        else:
            lines.append(f"未命中    应答里没有值 {expect} 的字节 —— 可能帧不对，或电量不在这条命令里")
    else:
        if cands:
            lines.append("候选字节  （值落在 1~100，按偏移排序）")
            for c in cands[:16]:
                lines.append(f"            byte[{c['offset']:>2}] = {c['value']:>3}"
                             + ("   <= 看起来最像电量" if len(cands) == 1 else ""))
        else:
            lines.append("候选字节  无（全部字节都在 1~100 之外，需换命令或换通道）")

    if samples and len(samples) > 1:
        stable = stable_offsets(samples)
        lines.append(f"多次采样  {len(samples)} 次，稳定非零字节："
                     + ("、".join(f"[{i}]" for i in stable[:20]) if stable else "无"))
        lines.append("          （电量字节应当每次相同；跳动的多半是按键/时序字段）")
    return lines
