"""Provider 抽象：每个数据源实现 probe() + read()。

约定
----
* ``probe()`` 只做「发现设备」，必须便宜（读注册表/文件/枚举句柄），
  绝不能触发蓝牙连接或长耗时 I/O，否则会拖慢整轮刷新。
* ``read()`` 针对单台设备取一次读数，允许慢（内部自带超时），
  失败要返回带 Status.ERROR / Status.UNKNOWN 的 Reading，不要抛异常。
* Provider 之间互不依赖；同一台物理设备若被两个 Provider 认领，
  由 Manager 按 ``order`` 去重（数字小的优先）。
"""
from __future__ import annotations

from core.models import Device, Reading


class Provider:
    name: str = "base"
    order: int = 100
    wants_cache: bool = False

    def __init__(self, settings: dict | None = None):
        self.settings = settings or {}
        self.cache = None  # 需要缓存时由 Manager 注入

    def attach(self, cache) -> None:
        self.cache = cache

    def probe(self) -> list[Device]:
        return []

    def read(self, device: Device) -> Reading:
        raise NotImplementedError

    def close(self) -> None:
        pass
