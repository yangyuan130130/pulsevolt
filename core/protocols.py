"""协议库：把「每台设备的私有电量协议」变成可加载、可分享的一等公民。

为什么单独抽一层
----------------
1.0 里协议是散在 ``settings.json`` 的 ``hid_devices`` 数组里，人看着累、
没法分享、也没法标来源。2.0 把它变成 ``protocols/*.json``：一条协议一个文件，
带厂商/型号/来源/校验方式，能单独导出给别人。

「启用」的语义
--------------
协议库里勾选启用的那些，会被 ``sync_settings()`` 全量写回
``settings.json`` 的 ``hid_devices`` —— 也就是说**协议库是唯一事实来源**，
settings 里的那一份只是给 Provider 读的投影。这样不会再出现
「协议库改了但实际没生效」的两份真相问题。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from core.paths import app_dir, protocol_dir

# ---------------------------------------------------------------------------
# 内置协议：从抓包实录里解出来的两套，随程序分发，删了会自动恢复
# ---------------------------------------------------------------------------

BUILTIN: list[dict] = [
    {
        "id": "langtu-t88",
        "name": "狼途 T88",
        "vendor": "狼途 LANGTU",
        "model": "T88",
        "kind": "keyboard",
        "transport": "24g",
        "match": {"vid": "0x05AC", "pid": "0x024F"},
        "channel": {"usage_page": "0xFF60", "usage": "0x61", "report_len": 33, "via": "output"},
        "request": "06 20 01 " + "00 " * 29 + "21",
        "response_offset": 4,
        "checksum": "sum8",
        "min_interval": 20,
        "enabled": True,
        "source": "builtin",
        "notes": "33 字节报告，报告 ID 0x06。校验 = 前 32 字节累加和取低 8 位。",
    },
    {
        "id": "mchose-g7",
        "name": "MCHOSE G7",
        "vendor": "迈从 MCHOSE",
        "model": "G7",
        "kind": "mouse",
        "transport": "24g",
        "match": {"vid": "0xA8A5", "pid": "0x2255"},
        "channel": {"usage_page": "0xFF01", "usage": "0x0010", "report_len": 65, "via": "output"},
        "request": "00 55 30 a5 0b 2e 01 01 01 " + "00 " * 56,
        "response_offset": 9,
        "checksum": "none",
        "min_interval": 20,
        "enabled": True,
        "source": "builtin",
        "notes": "65 字节双向报告，请求头 0x55 / 应答头 0xAA。首字节是报告 ID 占位，设备会忽略。",
    },
]

_SAFE_ID = re.compile(r"[^a-z0-9\-]+")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def make_id(name: str, existing: set[str] | None = None) -> str:
    """由名字生成 id：中文名转不出拼音，就用短哈希兜底，保证唯一。"""
    base = _SAFE_ID.sub("-", name.strip().lower()).strip("-")
    if not base or len(base) < 3:
        base = "proto-" + uuid.uuid4().hex[:8]
    rid = base
    taken = existing or set()
    i = 2
    while rid in taken:
        rid = f"{base}-{i}"
        i += 1
    return rid


def _normalize(proto: dict) -> dict:
    """补齐缺省字段，保证下游不用到处写 .get()。"""
    proto = dict(proto)
    proto.setdefault("vendor", "")
    proto.setdefault("model", "")
    proto.setdefault("kind", "other")
    proto.setdefault("transport", "24g")
    proto.setdefault("match", {})
    proto.setdefault("channel", {})
    proto.setdefault("checksum", "unknown")
    proto.setdefault("min_interval", 20)
    proto.setdefault("enabled", True)
    proto.setdefault("source", "user")
    proto.setdefault("notes", "")
    proto.setdefault("created", _now())
    proto.setdefault("updated", "")
    proto["builtin"] = proto.get("source") == "builtin"
    return proto


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def ensure_builtin() -> None:
    """把内置协议补回协议库（用户删掉了也会恢复，但不会覆盖用户的同名条目）。"""
    directory = protocol_dir()
    for proto in BUILTIN:
        path = directory / f"{proto['id']}.json"
        if not path.exists():
            path.write_text(json.dumps(_normalize(proto), ensure_ascii=False, indent=2), "utf-8")


def all_protocols() -> list[dict]:
    ensure_builtin()
    items: list[dict] = []
    for path in sorted(protocol_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("name"):
            continue
        data.setdefault("id", path.stem)
        items.append(_normalize(data))
    # 内置在前，其余按名称
    items.sort(key=lambda p: (not p.get("builtin"), p.get("name", "")))
    return items


def get(pid: str) -> dict | None:
    for item in all_protocols():
        if item["id"] == pid:
            return item
    return None


def save(proto: dict) -> dict:
    """新增或更新一条协议。同名不同 id 的视为新协议（允许两个同名不同型号）。"""
    ensure_builtin()
    proto = _normalize(proto)
    existing = {p["id"] for p in all_protocols() if p["id"] != proto.get("id")}
    if not proto.get("id"):
        proto["id"] = make_id(proto.get("name", ""), existing)
    path = protocol_dir() / f"{proto['id']}.json"
    if path.exists():
        try:
            old = json.loads(path.read_text("utf-8"))
            proto["created"] = old.get("created") or proto.get("created")
        except Exception:
            pass
    proto["updated"] = _now()
    path.write_text(json.dumps(proto, ensure_ascii=False, indent=2), "utf-8")
    return proto


def remove(pid: str) -> bool:
    path = protocol_dir() / f"{pid}.json"
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def set_enabled(pid: str, enabled: bool) -> dict | None:
    proto = get(pid)
    if proto is None:
        return None
    proto["enabled"] = bool(enabled)
    return save(proto)


# ---------------------------------------------------------------------------
# 与 settings.json 的同步（协议库 → Provider 能读的投影）
# ---------------------------------------------------------------------------

def to_hid_spec(proto: dict) -> dict:
    """协议 → settings.hid_devices 的一行。"""
    match = proto.get("match") or {}
    channel = proto.get("channel") or {}
    return {
        "name": proto["name"],
        "kind": proto.get("kind", "other"),
        "vid": match.get("vid", "0x0"),
        "pid": match.get("pid", "0x0"),
        "usage_page": channel.get("usage_page", "0x0"),
        "usage": channel.get("usage", "0x0"),
        "path_hint": match.get("path_hint", ""),
        "report_len": int(channel.get("report_len", 33)),
        "request": proto.get("request", ""),
        "via": channel.get("via", "output"),
        "response_offset": int(proto.get("response_offset", 1)),
        "min_interval": float(proto.get("min_interval", 20)),
        "enabled": True,
    }


def sync_settings() -> int:
    """把「已启用」的协议写进 settings.json，返回生效条数。

    调用方负责在之后重建 Manager（Provider 在构造时装配 hid_devices）。
    """
    from core import settings as settings_mod

    cfg = settings_mod.load()
    specs = [to_hid_spec(p) for p in all_protocols() if p.get("enabled", True)]
    cfg["hid_devices"] = specs
    cfg.setdefault("providers", {})["hid-vendor"] = True
    settings_mod.save(cfg)
    return len(specs)


def export_payload(pid: str) -> dict | None:
    proto = get(pid)
    if proto is None:
        return None
    payload = {k: v for k, v in proto.items() if k not in ("created", "updated")}
    payload["exported_at"] = _now()
    payload["app"] = "电之脉动 2.0"
    return payload


def import_payload(data: dict, rename: str = "") -> dict:
    """导入外部协议：强制换 id，避免覆盖本地同 id 的条目。"""
    if not isinstance(data, dict) or not data.get("name"):
        raise ValueError("不是有效的协议文件（缺少 name 字段）")
    proto = dict(data)
    if rename.strip():
        proto["name"] = rename.strip()
    proto["id"] = ""                       # 让它重新生成，绝不覆盖已有
    proto["source"] = "imported"
    proto["enabled"] = bool(proto.get("enabled", True))
    proto.pop("exported_at", None)
    proto.pop("app", None)
    return save(proto)
