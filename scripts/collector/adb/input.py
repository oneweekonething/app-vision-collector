"""触摸、滑动与文本输入。

文本输入依赖设备上安装的 ADB Keyboard（com.android.adbkeyboard），
通过广播 base64 编码内容实现对中文等非 ASCII 文本的输入——用于搜索框等场景。
"""

from __future__ import annotations

import base64
import random
import time

from collector.adb.connection import _adb_prefix, get_screen_size, run_adb, shell

ADB_IME = "com.android.adbkeyboard/.AdbIME"


def tap(x: int, y: int, device_id: str | None = None) -> None:
    run_adb(["shell", "input", "tap", str(x), str(y)], device_id=device_id, timeout=10)


def back(device_id: str | None = None) -> None:
    run_adb(["shell", "input", "keyevent", "4"], device_id=device_id, timeout=10)


def home(device_id: str | None = None) -> None:
    run_adb(["shell", "input", "keyevent", "3"], device_id=device_id, timeout=10)


def swipe(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_ms: int = 500,
    device_id: str | None = None,
) -> None:
    run_adb(
        ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        device_id=device_id,
        timeout=15,
    )


def swipe_to_next_screen(device_id: str | None = None) -> None:
    """手指上滑翻到"下一屏"（聊天中即查看更早的历史消息）。

    固定 40% 屏距、保留 60% 重叠，配合随机抖动模拟真人手势：
    重叠保证内容不漏采，抖动降低机械滑动被 App 风控识别的概率。
    """
    width, height = get_screen_size(device_id)
    jitter = max(6, int(height * 0.01))

    x = width // 2 + random.randint(-jitter, jitter)
    start_y = int(height * 0.70) + random.randint(-jitter, jitter)
    end_y = int(height * 0.30) + random.randint(-jitter, jitter)
    duration = random.randint(420, 680)

    swipe(x, start_y, x, end_y, duration_ms=duration, device_id=device_id)


def type_text(text: str, device_id: str | None = None) -> None:
    """向当前聚焦的输入框输入文本（需设备已安装 ADB Keyboard）。"""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    run_adb(
        [
            "shell", "am", "broadcast",
            "-a", "ADB_INPUT_B64",
            "--es", "msg", encoded,
        ],
        device_id=device_id,
        timeout=10,
    )


def ensure_adb_keyboard(device_id: str | None = None) -> str | None:
    """切换到 ADB Keyboard，返回原输入法 id 以便恢复。

    设备未安装 ADB Keyboard 时不做任何切换，返回 None。
    """
    current = shell(
        ["settings", "get", "secure", "default_input_method"], device_id=device_id
    )
    if not current:
        return None
    if "com.android.adbkeyboard" in current:
        return current
    run_adb(["shell", "ime", "set", ADB_IME], device_id=device_id, timeout=10)
    return current


def restore_keyboard(ime: str, device_id: str | None = None) -> None:
    if ime:
        run_adb(["shell", "ime", "set", ime], device_id=device_id, timeout=10)
