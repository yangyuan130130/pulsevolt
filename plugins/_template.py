r"""第三方 Provider 模板 —— 文件名以 _ 开头，不会被自动加载。

复制成 ``plugins/my_vendor.py`` 后把 PROVIDER 指向你的类即可生效，
无需改主程序。

适用场景：2.4G 私有协议。厂商各不相同，没有系统级标准，只能一厂一策。
下面给两种最常见写法的骨架。

------------------------------------------------------------------
写法 A：厂商软件已经读到了，只是存在某个文件/注册表里
      （最省事，也最稳；MCHOSE HUB 就是这条路）
------------------------------------------------------------------

    from core.models import Device, Kind, Reading, Status, Transport
    from providers.base import Provider

    class MyVendorProvider(Provider):
        name = "my-vendor"
        order = 40                    # 越小越优先

        def probe(self):
            # 只做发现：读文件/注册表，不要在这里做长耗时 I/O
            return [Device(
                uid="myvendor:0",
                name="某某鼠标",
                kind=Kind.MOUSE,
                transport=Transport.WIRELESS_24G,
                provider=self.name,
                native_id="0",
            )]

        def read(self, device):
            pct = ...  # 从文件里取出来的 0-100
            return Reading(device, pct, Status.CONNECTED, source="某某软件配置")

    PROVIDER = MyVendorProvider

------------------------------------------------------------------
写法 B：自己跟接收器说私有协议（罗技 HID++ 之类）
------------------------------------------------------------------

用 ctypes 打开 hid.dll，按 UsagePage 找到厂商自定义接口，然后收发
feature report。注意三点：

1. **只读，不写**。查电量的报文通常很短，但同一接口也可能承载改配置、
   进 DFU 的命令；发错字节可能让设备进入升级模式。
2. **必须限速**。多数固件对同一命令有冷却（几十秒），连续发会直接不应答。
3. **帧结构先验证再下结论**。典型坑：应答的第一字节是「命令回显」，
   最后一个字节是异或校验；只看到某个字节数值像百分比就当电量，
   很容易得到恒定的假值。

    import ctypes
    from ctypes import wintypes

    hid = ctypes.WinDLL("hid.dll")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # HidD_GetAttributes / HidD_GetPreparsedData / HidP_GetCaps 拿 UsagePage，
    # 命中厂商页（如 0xFF00 / 0xFF60）后再 HidD_GetFeature / WriteFile 收发。

    class MyHidProvider(Provider):
        name = "my-hid"
        order = 40
        MIN_INTERVAL = 30.0           # 秒，遵守设备冷却

        def probe(self):
            ...                       # 枚举 \\?\hid#vid_xxxx&pid_yyyy#...

        def read(self, device):
            ...                       # 发送查询帧 → 解析应答 → 返回 Reading

    PROVIDER = MyHidProvider

调试建议：先用 USBPcap 抓一遍官方驱动的报文（见项目 docs 里的
usbcap-tools），照抄它的命令和解析方式，比自己猜协议快得多。
"""
