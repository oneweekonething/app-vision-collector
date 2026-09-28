"""uiautomator 层级导出：独立核验 Tap 目标的可见文本。

ActionGuard 的第一层语义判定依据模型自报的 intent/target_text——模型
既是 Planner 又给自己写安全元数据，安全域没有隔离。本模块提供与模型
**无关**的第二事实来源：导出当前界面的 accessibility 控件树，取点击
位置所在节点及其可点击祖先的 text / content-desc，交护栏做关键词核验。

uiautomator dump 在部分场景会失败（FLAG_SECURE 页面、部分自绘 UI、
个别 ROM）——失败时返回 None，护栏自动退化为仅自报语义判定并记录
降级日志，不阻塞导航。
"""

from __future__ import annotations

import html
import re
import subprocess

from collector.adb.connection import run_adb

_NODE_RE = re.compile(r"<node\b[^>]*>")
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_DUMP_TIMEOUT = 10


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else ""


def _nodes(xml: str) -> list[dict]:
    nodes = []
    for tag in _NODE_RE.finditer(xml):
        bounds = _BOUNDS_RE.search(tag.group(0))
        if not bounds:
            continue
        x1, y1, x2, y2 = (int(v) for v in bounds.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        nodes.append({
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "clickable": _attr(tag.group(0), "clickable") == "true",
            "texts": [t for t in (
                _attr(tag.group(0), "text"),
                _attr(tag.group(0), "content-desc"),
            ) if t],
        })
    return nodes


def texts_at(xml: str, x: int, y: int) -> list[str]:
    """取坐标处用户可见的控件文本：内层节点 + 全部可点击祖先。

    点击的实际响应者是最近的 clickable 祖先，所以祖先文本（真正的
    按钮标签）比内层节点更关键；两者都返回给护栏判定。
    """
    containing = [n for n in _nodes(xml) if n["x1"] <= x < n["x2"] and n["y1"] <= y < n["y2"]]
    if not containing:
        return []

    texts: list[str] = []
    innermost = min(containing, key=lambda n: (n["x2"] - n["x1"]) * (n["y2"] - n["y1"]))
    texts.extend(innermost["texts"])
    for node in containing:
        if node["clickable"]:
            texts.extend(node["texts"])

    seen: set[str] = set()
    unique = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def dump_ui_xml(device_id: str | None = None) -> str | None:
    """导出当前界面控件树 XML；失败（不支持/被禁止/超时）返回 None。"""
    try:
        result = run_adb(
            ["exec-out", "uiautomator", "dump", "/dev/tty"],
            device_id=device_id,
            timeout=_DUMP_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    for stream in (result.stdout or "", result.stderr or ""):
        if "<node" in stream:
            return stream
    return None


def ui_texts_at_point(x: int, y: int, device_id: str | None = None) -> list[str] | None:
    """点击坐标处的控件文本；dump 失败返回 None（调用方据此降级）。"""
    xml = dump_ui_xml(device_id)
    if xml is None:
        return None
    return texts_at(xml, x, y)
