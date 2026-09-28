"""条目去重：跨屏指纹判重。

旧版指纹 = 类型+发送者+标题+正文，全局判重——聊天里同一个人发两次
"收到"，第二条会被误杀；而翻页保留 60% 重叠，同一条消息最多跨 3 屏
可见，只有相邻屏的重复才是真正的"同一屏内容重采"。

策略按条目类型区分：

- 聊天类（message/comment/system）：只对最近 N 屏（滑动窗口）判重。
  指纹额外并入 time_hint（有则必并）：同一消息跨屏时间戳一致，仍会
  判重；同一个人不同时刻发的相同文字则因时间不同而保留。窗口外的
  相同内容视为真实重复，保留不误杀。
- 非聊天类（note/product/search_result 等）：全局判重（卡片有标题，
  远处重现基本就是重复曝光）。

同屏内的完全相同指纹总是判重（提取器重复输出）。

窗口取 5 而非理论最小值 3：60% 重叠下普通内容最多跨 3 屏，但超长
消息 / 长图文 / 大卡片可占一屏以上、连续出现 4~5 屏——窗口 3 会把
它们的尾部重采误判为"真实重复"再次入库，5 屏留出余量，代价仅是
真实重复晚几屏才被承认。
"""

from __future__ import annotations

import hashlib
from typing import Any

CHAT_LIKE_TYPES = {"message", "comment", "system"}


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def _normalize_time(text: Any) -> str:
    return "".join(str(text or "").split()).lower()


def fingerprint(item: dict[str, Any]) -> str:
    """条目指纹：内容键；聊天类带 time_hint 时并入时间。"""
    key = "|".join([
        str(item.get("type", "")),
        _normalize(item.get("sender")),
        _normalize(item.get("title")),
        _normalize(item.get("text")),
    ])
    if item.get("type") in CHAT_LIKE_TYPES and item.get("time_hint"):
        key += "|t=" + _normalize_time(item["time_hint"])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class Deduplicator:
    """跨屏去重器：聊天类滑窗判重，非聊天类全局判重。"""

    def __init__(self, window: int = 5):
        # 普通内容最多跨 3 屏；长内容（长消息/大卡片）可达 4~5 屏，取 5 留余量
        self.window = max(1, window)
        self._global: set[str] = set()          # 全部已收录指纹（非聊天类判重用）
        self._screen_sets: dict[int, set[str]] = {}  # 各屏收录的指纹
        self._screen_order: list[int] = []      # 屏幕收录顺序（滑动窗口）

    def admit(self, screen: int, item: dict[str, Any]) -> bool:
        """判定条目是否收录（True=新条目，False=重复）。"""
        fp = fingerprint(item)

        current = self._screen_sets.setdefault(screen, set())
        if screen not in self._screen_order:
            self._screen_order.append(screen)
        if fp in current:
            return False  # 同屏完全相同 → 提取重复

        chat_like = item.get("type") in CHAT_LIKE_TYPES
        if chat_like:
            recent = self._screen_order[-(self.window + 1):-1]  # 不含当前屏
            if any(fp in self._screen_sets.get(s, set()) for s in recent):
                return False  # 重叠窗口内重采
        elif fp in self._global:
            return False

        current.add(fp)
        self._global.add(fp)
        return True
