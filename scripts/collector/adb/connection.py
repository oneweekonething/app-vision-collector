"""ADB 可执行文件解析、设备管理与常用 shell 封装。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

# 常见 App 的包名映射：launch 时按名称或包名启动
KNOWN_APPS = {
    "wechat": "com.tencent.mm",
    "微信": "com.tencent.mm",
    "xiaohongshu": "com.xingin.xhs",
    "rednote": "com.xingin.xhs",
    "小红书": "com.xingin.xhs",
    "douyin": "com.ss.android.ugc.aweme",
    "抖音": "com.ss.android.ugc.aweme",
    "taobao": "com.taobao.taobao",
    "淘宝": "com.taobao.taobao",
    "weibo": "com.sina.weibo",
    "微博": "com.sina.weibo",
}


def get_adb_executable() -> str:
    """定位 adb 可执行文件：环境变量 > PATH > 常见安装路径。"""
    env_path = os.environ.get("AVC_ADB")
    if env_path and Path(env_path).exists():
        return env_path

    which = shutil.which("adb")
    if which:
        return which

    candidates = [
        Path.home() / "Library/Android/sdk/platform-tools/adb",
        Path("/usr/local/bin/adb"),
        Path("/opt/homebrew/bin/adb"),
        Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools/adb",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return "adb"


def _adb_prefix(device_id: str | None) -> list[str]:
    adb = get_adb_executable()
    return [adb, "-s", device_id] if device_id else [adb]


def run_adb(
    args: list[str],
    device_id: str | None = None,
    timeout: int = 15,
    binary: bool = False,
) -> subprocess.CompletedProcess:
    """执行一条 adb 命令并返回 CompletedProcess。"""
    return subprocess.run(
        _adb_prefix(device_id) + args,
        capture_output=True,
        timeout=timeout,
        **({"errors": "replace"} if not binary else {}),
    )


def list_devices() -> list[dict[str, str]]:
    """列出当前 adb 设备（仅返回 device 状态的）。"""
    adb = get_adb_executable()
    result = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10)
    devices = []
    for line in (result.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append({"device_id": parts[0], "status": parts[1]})
    return devices


def ensure_device(device_id: str | None = None) -> str:
    """确认有可用设备；未指定 device_id 时取第一台在线设备。"""
    devices = list_devices()
    if device_id:
        if not any(d["device_id"] == device_id for d in devices):
            raise RuntimeError(
                f"设备 {device_id} 不在线。当前在线设备: "
                f"{[d['device_id'] for d in devices] or '无'}"
            )
        return device_id
    if not devices:
        raise RuntimeError(
            "未发现在线的 adb 设备。请连接手机、开启 USB 调试并确认 `adb devices` 可见。"
        )
    return devices[0]["device_id"]


def shell(args: list[str], device_id: str | None = None, timeout: int = 15) -> str:
    """执行 adb shell 命令，返回 stdout 文本。"""
    result = run_adb(["shell", *args], device_id=device_id, timeout=timeout)
    return (result.stdout or "").strip()


def get_screen_size(device_id: str | None = None) -> tuple[int, int]:
    """获取屏幕物理分辨率 (width, height)。"""
    output = shell(["wm", "size"], device_id=device_id)
    matches = re.findall(r"(\d+)x(\d+)", output)
    if matches:
        width, height = (int(v) for v in matches[-1])
        return width, height
    return 1080, 2400


def get_current_app(device_id: str | None = None) -> str:
    """获取当前前台应用，返回 'package/activity' 或 'unknown'。"""
    output = shell(["dumpsys", "window"], device_id=device_id, timeout=20)
    match = re.search(r"mCurrentFocus=Window\{[^}]*\bu0\s+([\w.]+/[\w.$]+)", output)
    if match:
        return match.group(1)
    match = re.search(r"mFocusedApp=.*\bu0\s+([\w.]+/[\w.$]+)", output)
    return match.group(1) if match else "unknown"


def launch_app(app: str, device_id: str | None = None) -> bool:
    """按名称（中英文）或包名启动 App。"""
    package = KNOWN_APPS.get(app) or KNOWN_APPS.get(app.lower(), app)
    result = run_adb(
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        device_id=device_id,
        timeout=20,
    )
    return result.returncode == 0 and b"Events injected: 1" in (result.stdout or b"")
