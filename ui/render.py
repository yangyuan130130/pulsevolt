"""托盘图标绘制。

Windows 托盘实际只有 16~24 px，直接在这个尺寸画会糊成一团，所以统一
**4 倍超采样**（256×256 画完再 LANCZOS 缩到 64×64），缩到 16px 时边缘仍是干净的。

视觉语言：
* 形状：电池外壳 + 正极触点，内部填充随电量变化
* 颜色：>50 绿 / 20~50 黄 / <=20 红 / 未知灰（与 Windows 自身电量语义一致）
* 附加标记：充电闪电、极低电量感叹号、有设备离线的灰点
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

GREEN = (46, 160, 67, 255)
GREEN_DARK = (30, 110, 45, 255)
AMBER = (212, 160, 34, 255)
AMBER_DARK = (150, 110, 20, 255)
RED = (218, 54, 51, 255)
RED_DARK = (150, 32, 30, 255)
GRAY = (128, 134, 140, 255)
GRAY_DARK = (85, 90, 95, 255)

SUPERSAMPLE = 4
CANVAS = 64
S = CANVAS * SUPERSAMPLE


def _palette(percent: int | None) -> tuple[tuple, tuple]:
    if percent is None:
        return GRAY, GRAY_DARK
    if percent <= 20:
        return RED, RED_DARK
    if percent <= 50:
        return AMBER, AMBER_DARK
    return GREEN, GREEN_DARK


def _bolt(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    """画一个闪电（充电中）。坐标按超采样比例给定。"""
    h = size // 2
    pts = [
        (cx + h // 3, cy - h), (cx - h // 2, cy + h // 8),
        (cx - h // 12, cy + h // 8), (cx - h // 3, cy + h),
        (cx + h // 2, cy - h // 6), (cx + h // 12, cy - h // 6),
    ]
    draw.polygon(pts, fill=color)


def draw_icon(percent: int | None, offline: int = 0, charging: bool = False) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color, dark = _palette(percent)

    # 外壳：留出右侧正极位置
    left, top, right, bottom = 6 * SUPERSAMPLE, 17 * SUPERSAMPLE, 48 * SUPERSAMPLE, 47 * SUPERSAMPLE
    radius = 7 * SUPERSAMPLE
    d.rounded_rectangle((left, top, right, bottom), radius=radius,
                        outline=color, width=4 * SUPERSAMPLE)
    # 正极触点
    d.rounded_rectangle((right + 2 * SUPERSAMPLE, 27 * SUPERSAMPLE, right + 7 * SUPERSAMPLE, 37 * SUPERSAMPLE),
                        radius=2 * SUPERSAMPLE, fill=color)

    inner_l, inner_t = left + 5 * SUPERSAMPLE, top + 5 * SUPERSAMPLE
    inner_r, inner_b = right - 5 * SUPERSAMPLE, bottom - 5 * SUPERSAMPLE
    inner_w = inner_r - inner_l

    if percent is None:
        # 未知：画一个空心横杠，比问号在小尺寸下更干净
        cy = (inner_t + inner_b) // 2
        d.rounded_rectangle((inner_l + inner_w * 0.2, cy - 2 * SUPERSAMPLE,
                             inner_r - inner_w * 0.2, cy + 2 * SUPERSAMPLE),
                            radius=2 * SUPERSAMPLE, fill=dark)
    else:
        pct = max(0, min(100, percent))
        w = int(inner_w * pct / 100)
        if w > 0:
            d.rounded_rectangle((inner_l, inner_t, inner_l + w, inner_b),
                                radius=3 * SUPERSAMPLE, fill=color)
            # 顶部高光，避免大色块显得死板
            if w > 6 * SUPERSAMPLE:
                d.rounded_rectangle((inner_l + 2 * SUPERSAMPLE, inner_t + 2 * SUPERSAMPLE,
                                     inner_l + w - 2 * SUPERSAMPLE, inner_t + 6 * SUPERSAMPLE),
                                    radius=2 * SUPERSAMPLE, fill=(255, 255, 255, 46))
        if pct <= 10:
            # 极低电量：中间放感叹号，16px 下也能看出异常
            cx, cy = (inner_l + inner_r) // 2, (inner_t + inner_b) // 2
            d.rounded_rectangle((cx - 2 * SUPERSAMPLE, cy - 8 * SUPERSAMPLE,
                                 cx + 2 * SUPERSAMPLE, cy + 2 * SUPERSAMPLE),
                                radius=2 * SUPERSAMPLE, fill=(255, 255, 255, 230))
            d.ellipse((cx - 2 * SUPERSAMPLE, cy + 5 * SUPERSAMPLE,
                       cx + 2 * SUPERSAMPLE, cy + 9 * SUPERSAMPLE), fill=(255, 255, 255, 230))

    if charging:
        _bolt(d, (inner_l + inner_r) // 2, (inner_t + inner_b) // 2, 22 * SUPERSAMPLE, (255, 255, 255, 235))

    if offline > 0:
        # 右下角离线标记：小圆点 + 深色描边，保证压在电池上也分得开
        d.ellipse((44 * SUPERSAMPLE, 40 * SUPERSAMPLE, 58 * SUPERSAMPLE, 54 * SUPERSAMPLE),
                  fill=(96, 100, 104, 255), outline=(38, 40, 43, 255), width=2 * SUPERSAMPLE)

    return img.resize((CANVAS, CANVAS), Image.LANCZOS)


def draw_placeholder(text: str = "?") -> Image.Image:
    """未扫描完成时的占位图标。"""
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("segoeui.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    d.text((CANVAS / 2, CANVAS / 2), text, fill=GRAY, anchor="mm", font=font)
    return img
