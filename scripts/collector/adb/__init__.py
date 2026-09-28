"""ADB 设备层：连接、截屏、输入。"""

from collector.adb.connection import (
    AdbCommandError,
    ensure_device,
    get_adb_executable,
    get_current_app,
    get_screen_size,
    launch_app,
    list_devices,
)
from collector.adb.input import (
    back,
    home,
    input_text_safe,
    swipe,
    swipe_to_next_screen,
    tap,
    type_text,
)
from collector.adb.screenshot import Screenshot, ScreenshotError, capture
from collector.adb.uitree import dump_ui_xml, ui_texts_at_point

__all__ = [
    "AdbCommandError",
    "Screenshot",
    "ScreenshotError",
    "capture",
    "ensure_device",
    "get_adb_executable",
    "get_current_app",
    "get_screen_size",
    "launch_app",
    "list_devices",
    "tap",
    "swipe",
    "swipe_to_next_screen",
    "back",
    "home",
    "type_text",
    "input_text_safe",
    "dump_ui_xml",
    "ui_texts_at_point",
]
