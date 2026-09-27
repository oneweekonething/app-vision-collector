"""截屏：exec-out screencap 直接获取二进制 PNG。

证据优先原则：只要拿到了字节就视为成功（即使尺寸解析失败也原样保留），
因为截屏是溯源的证据本体。
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO


class ScreenshotError(RuntimeError):
    """截屏失败（设备离线、敏感页面禁止截屏等）。"""


@dataclass
class Screenshot:
    """一屏原始证据。"""

    png_bytes: bytes
    width: int
    height: int
    captured_at: str  # ISO8601，含时区
    sha256: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _image_size(png_bytes: bytes) -> tuple[int, int]:
    """解析 PNG 尺寸；失败时返回 (0, 0)，不影响证据留存。"""
    try:
        from PIL import Image

        with Image.open(BytesIO(png_bytes)) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def capture(device_id: str | None = None, timeout: int = 15) -> Screenshot:
    """从设备截取一屏。"""
    from collector.adb.connection import run_adb

    try:
        result = run_adb(
            ["exec-out", "screencap", "-p"],
            device_id=device_id,
            timeout=timeout,
            binary=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScreenshotError(f"截屏超时（{timeout}s）: {exc}") from exc

    data = result.stdout or b""
    if result.returncode != 0 or len(data) < 1000:
        stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise ScreenshotError(
            f"截屏失败 (rc={result.returncode}, {len(data)} bytes)。"
            f"可能是敏感页面禁止截屏或设备离线。{stderr[:200]}"
        )

    width, height = _image_size(data)
    return Screenshot(
        png_bytes=data,
        width=width,
        height=height,
        captured_at=_now_iso(),
        sha256=hashlib.sha256(data).hexdigest(),
    )
