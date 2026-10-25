"""智能测试：把「这台机器上装了哪些外设、哪些已经能读电量、哪些还得破解」一次摸清。

设计原则（很重要，别为了显得聪明而越界）
----------------------------------------
* **只发协议库里已经确认过的帧**。绝不遍历命令空间——同一厂商接口可能同时
  承载改 DPI、改灯效、进 DFU 的命令，乱试的代价是设备变砖。
* 所以「智能」体现在**识别与验证**，而不是「暴力猜命令」：
  1. 枚举所有厂商接口，读它们的产品字符串，认出是什么设备；
  2. 用协议库按 VID/PID 匹配，命中的就发帧验证（值是否 0~100、两次是否一致）；
  3. 有蓝牙通道的设备，拿 GATT 真实电量做交叉核对，判断协议是否仍然有效；
  4. 没被覆盖的设备，整理出接口清单，直接给出「去抓包破解」的下一步。
* 所有阶段都写进 ``steps``，前端能一条条亮起来——用户看得见它在干什么，
  而不是转圈等结果。
"""
from __future__ import annotations

import ctypes
import threading
import time

from core import hidscan
from core.models import Status
from core.protocols import all_protocols
from providers import hid_vendor as hv

PASSIVE_SECONDS = 3.0      # 单个接口的被动嗅探时长
PASSIVE_TIMEOUT_MS = 250   # 每次读的超时（没有数据就快速返回，别卡住）

_STEP_DEFS = [
    ("inventory", "清点接口", "枚举本机所有厂商自定义 HID 接口，读取产品名"),
    ("match", "匹配协议库", "按 VID/PID 与协议库比对，找出已知设备"),
    ("verify", "验证协议", "对命中的协议发一帧，检查应答是否合理"),
    ("crosscheck", "蓝牙交叉核对", "用蓝牙通道的真实电量校验 2.4G 读数"),
    ("passive", "被动嗅探", "只读不写，收集设备主动上报的报文"),
    ("conclude", "汇总结论", "整理可用协议与待破解设备"),
]


def _product_name(path: str) -> str:
    with hv.HID_LOCK:
        fh = hv.kernel32.CreateFileW(path, 0, hv.FILE_SHARE_ALL, None,
                                     hv.OPEN_EXISTING, 0, None)
        if not fh or fh == hv.INVALID_HANDLE:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(128)
            if hv.hid.HidD_GetProductString(fh, buf, ctypes.sizeof(buf)):
                return buf.value.strip()
            return ""
        except Exception:
            return ""
        finally:
            try:
                hv.kernel32.CloseHandle(fh)
            except Exception:
                pass


def _passive_read(info: dict, seconds: float = PASSIVE_SECONDS) -> list[bytes]:
    """只读不写，把设备在这段时间里主动推的 IN 报文全收下来。"""
    length = int(info.get("in_len") or 0)
    if length <= 0:
        return []
    frames: list[bytes] = []
    with hv.HID_LOCK:
        handle = hv._open_rw(info["path"])
        if handle is None:
            return []
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                chunk = hv._io(handle, bytes(length), write=False, timeout_ms=PASSIVE_TIMEOUT_MS)
                if chunk:
                    frames.append(bytes(chunk))
        finally:
            try:
                hv.kernel32.CloseHandle(handle)
            except Exception:
                pass
    return frames


def _byte_profile(frames: list[bytes]) -> list[dict]:
    """逐字节统计：值域、是否稳定、是否落在 1~100。"""
    if not frames:
        return []
    n = min(len(f) for f in frames)
    out = []
    for i in range(n):
        vals = [f[i] for f in frames]
        uniq = sorted(set(vals))
        out.append({
            "offset": i,
            "min": min(vals),
            "max": max(vals),
            "uniq": len(uniq),
            "stable": len(uniq) == 1,
            "value": vals[0],
            "looks_like_pct": len(uniq) == 1 and 1 <= vals[0] <= 100,
        })
    return out


class SmartTest:
    """一次智能测试的运行态。同一时间只允许一个实例在跑。"""

    def __init__(self, manager=None):
        self.manager = manager
        self._lock = threading.RLock()
        self.running = False
        self.finished = False
        self.started_at = 0.0
        self.steps = [{"key": k, "title": t, "desc": d, "status": "pending", "detail": ""}
                      for k, t, d in _STEP_DEFS]
        self.interfaces: list[dict] = []
        self.verified: list[dict] = []
        self.passive: list[dict] = []
        self.uncovered: list[dict] = []
        self.summary = ""
        self.error = ""

    # ---- 供前端轮询 ------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "finished": self.finished,
                "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
                "steps": [dict(s) for s in self.steps],
                "interfaces": list(self.interfaces),
                "verified": list(self.verified),
                "passive": list(self.passive),
                "uncovered": list(self.uncovered),
                "summary": self.summary,
                "error": self.error,
            }

    def _step(self, key: str, status: str, detail: str = "") -> None:
        with self._lock:
            for s in self.steps:
                if s["key"] == key:
                    s["status"] = status
                    if detail:
                        s["detail"] = detail
                    break

    # ---- 主流程 ----------------------------------------------------
    def run_async(self) -> bool:
        if self.running:
            return False
        self.running = True
        self.finished = False
        self.error = ""
        self.started_at = time.time()
        self.verified, self.passive, self.uncovered = [], [], []
        self.summary = ""
        self.steps = [{"key": k, "title": t, "desc": d, "status": "pending", "detail": ""}
                      for k, t, d in _STEP_DEFS]
        threading.Thread(target=self._run, name="smart-test", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            for s in self.steps:
                if s["status"] == "running":
                    self._step(s["key"], "fail", self.error)
        finally:
            self.running = False
            self.finished = True

    def _do_run(self) -> None:
        protocols = all_protocols()

        # 1) 清点
        self._step("inventory", "running")
        rows = hidscan.vendor_interfaces()
        interfaces = []
        for info in rows:
            interfaces.append({
                "vid": f"{info['vid']:04X}", "pid": f"{info['pid']:04X}",
                "usage_page": f"0x{info['usage_page']:04X}",
                "usage": f"0x{info['usage']:04X}",
                "in_len": info["in_len"], "out_len": info["out_len"],
                "feat_len": info["feat_len"],
                "bidirectional": info["in_len"] > 0 and info["out_len"] > 0,
                "product": _product_name(info["path"]),
                "path_tail": info["path"][-24:],
                "_info": info,
            })
        with self._lock:
            self.interfaces = [{k: v for k, v in i.items() if k != "_info"} for i in interfaces]
        bi = sum(1 for i in interfaces if i["bidirectional"])
        self._step("inventory", "ok", f"共 {len(interfaces)} 个厂商接口，其中双向 {bi} 个")

        # 2) 匹配协议库
        self._step("match", "running")
        matched: list[tuple[dict, dict]] = []      # (协议, 接口)
        for proto in protocols:
            vid = int(str((proto.get("match") or {}).get("vid", "0")), 0)
            pid = int(str((proto.get("match") or {}).get("pid", "0")), 0)
            page = int(str((proto.get("channel") or {}).get("usage_page", "0")), 0)
            for row in interfaces:
                if (int(row["vid"], 16), int(row["pid"], 16)) != (vid, pid):
                    continue
                if page and int(row["usage_page"], 16) != page:
                    continue
                matched.append((proto, row))
        self._step("match", "ok" if matched else "warn",
                   f"{len(protocols)} 条协议里有 {len(matched)} 条与本机接口对上"
                   if matched else "协议库里的设备都不在线（或接收器没插）")

        # 3) 验证
        self._step("verify", "running")
        for proto, row in matched:
            info = row["_info"]
            length = int(proto["channel"].get("report_len") or hidscan.guess_length(info))
            offset = int(proto.get("response_offset", 1))
            samples = hidscan.repeat_probe(info, proto.get("request", ""), length,
                                           via=proto["channel"].get("via", "output"),
                                           times=2, gap=0.4)
            entry = {
                "protocol_id": proto["id"], "name": proto["name"],
                "kind": proto.get("kind", "other"),
                "vid": row["vid"], "pid": row["pid"],
                "usage_page": row["usage_page"], "usage": row["usage"],
                "report_len": length, "offset": offset,
                "ok": False, "value": None, "stable": False,
                "raw": "", "note": "",
            }
            if not samples:
                entry["note"] = "无应答（设备可能不在该通道，例如切到了蓝牙）"
            else:
                raw = samples[0]
                entry["raw"] = raw.hex(" ")
                if length <= offset:
                    entry["note"] = f"应答过短（{len(raw)} 字节）"
                else:
                    value = raw[offset]
                    # 电压法估算电量天然会抖 ±1~2%，严格相等会把正常设备判成不稳定
                    stable = (all(abs(s[offset] - value) <= 2 for s in samples)
                              if len(samples) > 1 else True)
                    entry["value"] = value
                    entry["stable"] = stable
                    if 0 <= value <= 100:
                        entry["ok"] = True
                        entry["note"] = "读数在有效范围" + ("且多次采样一致" if stable else "，但多次采样抖动偏大")
                    else:
                        entry["note"] = f"byte[{offset}] = {value}，不在 0~100，offset 可能配错"
            with self._lock:
                self.verified.append(entry)
        ok_n = sum(1 for v in self.verified if v["ok"])
        self._step("verify", "ok" if ok_n else ("warn" if matched else "skip"),
                   f"{ok_n}/{len(matched)} 条协议读数正常" if matched else "无匹配协议，跳过")

        # 4) 蓝牙交叉核对
        self._step("crosscheck", "running")
        cross = self._cross_check()
        self._step("crosscheck", "ok" if cross else "skip", cross or "没有可对照的蓝牙通道")

        # 5) 被动嗅探：只挑还没被协议覆盖的双向接口
        self._step("passive", "running")
        covered = {(v["vid"], v["pid"], v["usage_page"]) for v in self.verified if v["ok"]}
        targets = [i for i in interfaces
                   if i["bidirectional"] and (i["vid"], i["pid"], i["usage_page"]) not in covered]
        got_frames = 0
        for row in targets:
            frames = _passive_read(row["_info"], PASSIVE_SECONDS)
            got_frames += len(frames)
            profile = _byte_profile(frames)
            cands = [{"offset": p["offset"], "value": p["value"]}
                     for p in profile if p["looks_like_pct"]]
            with self._lock:
                self.passive.append({
                    "vid": row["vid"], "pid": row["pid"],
                    "usage_page": row["usage_page"], "usage": row["usage"],
                    "product": row["product"],
                    "frames": len(frames),
                    "candidates": cands,
                    "profile": profile[:32],
                })
        self._step("passive", "ok" if got_frames else "skip",
                   f"嗅探 {len(targets)} 个接口、{PASSIVE_SECONDS:.0f} 秒，收到 {got_frames} 帧"
                   if targets else "所有双向接口都已被协议覆盖")

        # 6) 未覆盖设备
        seen = {(v["vid"], v["pid"]) for v in self.verified}
        for row in interfaces:
            if (row["vid"], row["pid"]) in seen:
                continue
            product = row["product"] or ""
            # 触摸板 / 虚拟设备 / 指纹这类不可能带电池的接口，标出来但排后面
            likely = not any(k in product.lower() for k in
                             ("hidi2c", "virtual", "touch", "pen", "fingerprint", "gpio"))
            self.uncovered.append({
                "vid": row["vid"], "pid": row["pid"],
                "usage_page": row["usage_page"], "usage": row["usage"],
                "in_len": row["in_len"], "out_len": row["out_len"],
                "product": product, "bidirectional": row["bidirectional"],
                "likely": likely,
            })

        self.uncovered.sort(key=lambda u: (not u["likely"], u["vid"], u["usage_page"]))

        self._step("conclude", "running")
        parts = []
        if ok_n:
            parts.append(f"{ok_n} 台设备已可直接读取")
        if self.uncovered:
            parts.append(f"{len(self.uncovered)} 个接口尚无协议")
        self.summary = "；".join(parts) or "没有发现可用的外设接口"
        self._step("conclude", "ok" if ok_n else "warn", self.summary)

    def _cross_check(self) -> str:
        """拿蓝牙通道的真实电量，校验 2.4G 协议读数是否还准。

        按「设备名的数字/字母型号」匹配（T88 ↔ T88-BT1、G7 ↔ MCHOSE G7），
        差 8 个百分点以内视为一致。这一步能自动发现「换了固件导致协议失效」。
        """
        if self.manager is None:
            return ""
        readings = {r.device.name.lower(): r for r in self.manager.snapshot()
                    if r.percent is not None and r.status in (Status.CONNECTED, Status.CHARGING)}
        if not readings:
            return ""
        notes = []
        for entry in self.verified:
            if not entry["ok"] or entry["value"] is None:
                continue
            tokens = [t for t in _tokens(entry["name"]) if len(t) >= 2]
            for name, reading in readings.items():
                if any(t in name for t in tokens):
                    diff = abs(int(entry["value"]) - int(reading.percent))
                    entry["ble_ref"] = reading.percent
                    if diff <= 8:
                        entry["score_boost"] = True
                        notes.append(f"{entry['name']}：2.4G {entry['value']}% ≈ 蓝牙 {reading.percent}%")
                    else:
                        entry["note"] += f"；与蓝牙 {reading.percent}% 相差 {diff}，协议可能已变"
                        notes.append(f"{entry['name']}：2.4G {entry['value']}% 与蓝牙 {reading.percent}% 不一致")
                    break
        return "；".join(notes)


def _tokens(text: str) -> list[str]:
    """从名字里抽型号片段：'狼途 T88' -> ['t88']，'MCHOSE G7' -> ['g7']。"""
    buf, cur = [], ""
    for ch in text:
        if ch.isalnum():
            cur += ch.lower()
        elif cur:
            buf.append(cur)
            cur = ""
    if cur:
        buf.append(cur)
    return [t for t in buf if any(c.isdigit() for c in t)] or buf
