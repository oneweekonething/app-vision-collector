"""触摸、滑动与文本输入。

文本输入依赖设备上安装的 ADBKeyBoard 虚拟键盘（com.android.adbkeyboard，
https://github.com/senzhk/ADBKeyBoard）：它监听系统广播并把文本提交到
当前聚焦的输入框。本项目通过 ADB_INPUT_B64 广播 base64 编码内容，
实现对中文等非 ASCII 文本的输入——用于搜索框等场景。

输入法生命周期由 input_text_safe() 以事务方式管理：输入前切换到
ADB Keyboard，无论输入成败都在 finally 中恢复原输入法。
"""

from __future__ import annotations

import base64
import random
import time

from collector.adb.connection import (
    AdbCommandError,
    get_screen_size,
    run_adb,
    shell,
)

ADB_IME_PACKAGE = "com.android.adbkeyboard"
ADB_IME = f"{ADB_IME_PACKAGE}/.AdbIME"


def tap(x: int, y: int, device_id: str | None = None) -> None:
    run_adb(["shell", "input", "tap", str(x), str(y)], device_id=device_id, timeout=10, check=True)


def back(device_id: str | None = None) -> None:
    run_adb(["shell", "input", "keyevent", "4"], device_id=device_id, timeout=10, check=True)


def home(device_id: str | None = None) -> None:
    run_adb(["shell", "input", "keyevent", "3"], device_id=device_id, timeout=10, check=True)


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
        check=True,
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
    """向当前聚焦的输入框输入文本（需设备已切换到 ADB Keyboard）。"""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    run_adb(
        [
            "shell", "am", "broadcast",
            "-a", "ADB_INPUT_B64",
            "--es", "msg", encoded,
        ],
        device_id=device_id,
        timeout=10,
        check=True,
    )


def ensure_adb_keyboard(device_id: str | None = None) -> str | None:
    """切换到 ADB Keyboard，返回原输入法 id 以便恢复。

    设备已启用但未激活 ADB Keyboard 时执行 `ime set`；未启用时抛
    AdbCommandError（提示安装），调用方据此给出可诊断的失败原因。
    """
    enabled = shell(["ime", "list", "-s"], device_id=device_id, check=False)
    if ADB_IME_PACKAGE not in enabled:
        raise AdbCommandError(
            ["ime", "set", ADB_IME], 1,
            f"设备未启用 ADB Keyboard（{ADB_IME_PACKAGE}），无法输入文本；"
            "请按 README 安装并启用后重试",
            device_id=device_id,
        )
    current = shell(
        ["settings", "get", "secure", "default_input_method"], device_id=device_id, check=False
    )
    if ADB_IME_PACKAGE in current:
        return current  # 已是 ADB Keyboard，恢复为自身即无操作
    run_adb(["shell", "ime", "set", ADB_IME], device_id=device_id, timeout=10, check=True)
    return current or None


def restore_keyboard(ime: str | None, device_id: str | None = None) -> None:
    if ime:
        run_adb(["shell", "ime", "set", ime], device_id=device_id, timeout=10, check=True)


def input_text_safe(text: str, device_id: str | None = None) -> bool:
    """事务式文本输入：切换输入法 → 输入 → 无论成败恢复原输入法。

    返回 True 表示文本已提交。失败语义区分两级：
    - 切换/输入本身失败：抛 AdbCommandError，上层按动作失败处理；
    - 输入已成功、仅 IME 恢复失败：重试一次后降级为警告并返回 True——
      此时按动作失败重试会导致同一文本被输入两遍。
    """
    previous_ime = ensure_adb_keyboard(device_id)
    try:
        type_text(text, device_id)
    finally:
        try:
            restore_keyboard(previous_ime, device_id)
        except AdbCommandError as exc:
            try:
                time.sleep(0.6)
                restore_keyboard(previous_ime, device_id)
            except AdbCommandError:
                print(f"[输入] 警告：文本已输入，但输入法恢复失败（{exc}），"
                      "请手动把默认输入法切回常用项")
    return True
