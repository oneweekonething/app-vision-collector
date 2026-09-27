"""感知哈希：翻页停止策略的视觉位移判据。

截图保留 60% 重叠翻页时，相邻两屏的感知哈希（average hash）距离较大；
翻页失效或已到页面底部时画面几乎不动，距离接近 0。collect.py 用它
区分"没有新条目但还在正常翻页"和"真的翻不动了"。
"""

from __future__ import annotations

from io import BytesIO


def average_hash(png_bytes: bytes, size: int = 8) -> int | None:
    """8x8 平均哈希（64bit）；图片无法解析时返回 None。"""
    try:
        from PIL import Image

        with Image.open(BytesIO(png_bytes)) as img:
            gray = img.convert("L").resize((size, size))
            pixels = list(gray.getdata())
    except Exception:
        return None

    threshold = sum(pixels) / len(pixels)
    value = 0
    for pixel in pixels:
        value = (value << 1) | (1 if pixel > threshold else 0)
    return value


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()
