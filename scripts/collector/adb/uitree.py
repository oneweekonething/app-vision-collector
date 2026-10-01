"""uiautomator 层级导出：独立核验 Tap 目标的可见文本。

ActionGuard 的第一层语义判定依据模型自报的 intent/target_text——模型
既是 Planner 又给自己写安全元数据，安全域没有隔离。本模块提供与模型
**无关**的第二事实来源：导出当前界面的 accessibility 控件树，取点击
位置所在节点及其可点击祖先（按钮级祖先含子树全部文本，覆盖"icon +
兄弟文字"式按钮）的 text / content-desc，交护栏做关键词核验。

uiautomator dump 在部分场景会失败（FLAG_SECURE 页面、部分自绘 UI、
个别 ROM）——失败时返回 None，护栏自动退化为仅自报语义判定并记录
降级日志，不阻塞导航。dump 输出畸形无法建 XML 树时回退为正则几何
包含解析。
"""

from __future__ import annotations

import html
import re
import subprocess
import xml.etree.ElementTree as ET

from collector.adb.connection import run_adb

_NODE_RE = re.compile(r"<node\b[^>]*>")
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_DUMP_TIMEOUT = 10

# 按钮级可点击节点的面积上限（相对全屏）：小于此值才收集子树全部文本，
# 防止整页可点击的大容器把全屏文本都算进点击目标
_CLICKABLE_SUBTREE_AREA_RATIO = 0.25


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


def _parse_tree(xml: str) -> ET.Element | None:
    """解析控件树为 XML 树；容忍前置垃圾行与尾部状态行。

    `uiautomator dump /dev/tty` 会在 XML 之后附加一行
    "UI hierchary dumped to: /dev/tty"（Android DumpCommand 的固定行为），
    ET.fromstring 遇到根元素后的垃圾会直接 ParseError——而树解析一旦失败
    走正则回退，护栏就丢失兄弟节点的按钮文字（如"发送"），由拒绝变放行。
    因此先截取 </hierarchy> 之前的部分再解析；畸形输出才返回 None 走回退。
    """
    start = xml.find("<?xml")
    if start == -1:
        start = xml.find("<hierarchy")
    if start == -1:
        return None
    end = xml.find("</hierarchy>")
    fragment = xml[start : end + len("</hierarchy>")] if end != -1 else xml[start:]
    try:
        return ET.fromstring(fragment)
    except ET.ParseError:
        return None


def _bounds_of(el: ET.Element) -> tuple[int, int, int, int] | None:
    match = _BOUNDS_RE.search(el.get("bounds", ""))
    if not match:
        return None
    x1, y1, x2, y2 = (int(v) for v in match.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _contains(el: ET.Element, x: int, y: int) -> bool:
    bounds = _bounds_of(el)
    return bounds is not None and bounds[0] <= x < bounds[2] and bounds[1] <= y < bounds[3]


def _own_texts(el: ET.Element) -> list[str]:
    return [t for t in (el.get("text", ""), el.get("content-desc", "")) if t]


def _area(bounds: tuple[int, int, int, int]) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _texts_at_tree(root: ET.Element, x: int, y: int) -> list[str]:
    """树语义判定：含坐标链 = 祖先链（文档序，外层在前）。

    按钮级可点击祖先（面积 ≤ 全屏 25%）收集其子树全部文本——点击落在
    icon 上、按钮标签是兄弟 text 节点时仍能拿到标签；整页大容器只算
    自身文本，避免把全屏文字都算进点击目标。innermost 取包含节点中
    面积最小者：嵌套树等价于最深层，扁平/畸形输出也依然正确。
    """
    containing = [el for el in root.iter("node") if _contains(el, x, y)]
    if not containing:
        return []

    outermost = _bounds_of(containing[0])
    screen_area = _area(outermost) if outermost else 1

    texts: list[str] = []
    innermost = min(containing, key=lambda el: _area(_bounds_of(el) or (0, 0, 0, 0)))
    texts.extend(_own_texts(innermost))
    for el in containing:
        if el.get("clickable") != "true":
            continue
        bounds = _bounds_of(el)
        if bounds and _area(bounds) <= screen_area * _CLICKABLE_SUBTREE_AREA_RATIO:
            for sub in el.iter("node"):
                texts.extend(_own_texts(sub))
        else:
            texts.extend(_own_texts(el))

    seen: set[str] = set()
    unique = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def _texts_at_flat(xml: str, x: int, y: int) -> list[str]:
    """正则几何包含回退：dump 输出畸形、无法建树时使用。

    没有层级信息，用几何包含近似树语义：按钮级可点击节点（面积 ≤ 全屏
    25%）收集 bounds 完全落入其内的全部节点文本——"icon + 兄弟文字"式
    按钮即使建不了树也能拿到标签；整页大容器仍只算自身文本。
    """
    nodes = _nodes(xml)
    containing = [n for n in nodes if n["x1"] <= x < n["x2"] and n["y1"] <= y < n["y2"]]
    if not containing:
        return []

    def area(n: dict) -> int:
        return (n["x2"] - n["x1"]) * (n["y2"] - n["y1"])

    # 屏幕基准取文档序第一个包含节点（与树路径的 outermost 一致）
    screen_area = area(containing[0])

    texts: list[str] = []
    innermost = min(containing, key=area)
    texts.extend(innermost["texts"])
    for node in containing:
        if not node["clickable"]:
            continue
        if area(node) <= screen_area * _CLICKABLE_SUBTREE_AREA_RATIO:
            texts.extend(
                text for sub in nodes
                if sub["x1"] >= node["x1"] and sub["y1"] >= node["y1"]
                and sub["x2"] <= node["x2"] and sub["y2"] <= node["y2"]
                for text in sub["texts"]
            )
        else:
            texts.extend(node["texts"])

    seen: set[str] = set()
    unique = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def texts_at(xml: str, x: int, y: int) -> list[str]:
    """取坐标处用户可见的控件文本：内层节点 + 按钮级可点击祖先（含子树文本）。"""
    root = _parse_tree(xml)
    if root is not None:
        return _texts_at_tree(root, x, y)
    return _texts_at_flat(xml, x, y)


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
