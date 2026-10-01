"""条目去重：跨屏指纹判重。

去重模式按采集对象的形态选择（SessionStore.create 的 dedup_mode，collect.py
按 App 自动判定，`--dedup` 可覆盖）：

- **chat（聊天采集，如微信）**：同一个人隔很久再发相同文字是真实重复，
  必须保留。聊天类条目（词表见 CHAT_LIKE_TYPES）只对最近 N 屏（滑动窗口）
  判重——窗口内的重复是 60% 重叠造成的重采，窗口外的相同内容视为真实
  重复保留；指纹并入 time_hint（有则必并），不同时刻的相同文本不误杀。
  非聊天类条目（卡片有标题）仍全局判重。
- **global（信息流采集，如红果免费短剧/小红书）**：同内容远距重现就是
  重复曝光（榜单回看、推荐流重推、翻页回弹），不存在"真实重复"的语义。
  全部条目按内容全局判重，且指纹不并入 time_hint（时间抖动不应放过重复）。

同屏内的完全相同指纹总是判重（提取器重复输出）。

窗口取 5 而非理论最小值 3：60% 重叠下普通内容最多跨 3 屏，但超长
消息 / 长图文 / 大卡片可占一屏以上、连续出现 4~5 屏——窗口 3 会把
它们的尾部重采误判为"真实重复"再次入库，5 屏留出余量，代价仅是
真实重复晚几屏才被承认。
"""

from __future__ import annotations

import hashlib
from typing import Any

# 聊天类条目类型。必须与提取侧的类型词表双方对齐：
# - prompts.py 微信 hint 要求 type=text/image/voice/video/link/sticker/system；
# - 通用输出 schema 里聊天消息写作 message/comment/system。
# 两套写法任一出现都必须走滑窗判重，否则微信消息（type=text）会被当成
# 卡片全局判重，同文本远距重现的真实重复（如两条"收到"）被误删。
CHAT_LIKE_TYPES = {
    "message", "comment", "system",
    "text", "image", "voice", "video", "link", "sticker",
}

MODE_CHAT = "chat"
MODE_GLOBAL = "global"


def _normalize(text: Any) -> str:
    return " ".join(str(text or "").split()).lower()


def _normalize_time(text: Any) -> str:
    return "".join(str(text or "").split()).lower()


def content_fingerprint(item: dict[str, Any]) -> str:
    """内容指纹：类型+发送者+标题+正文，不含时间。"""
    key = "|".join([
        str(item.get("type", "")),
        _normalize(item.get("sender")),
        _normalize(item.get("title")),
        _normalize(item.get("text")),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def fingerprint(item: dict[str, Any]) -> str:
    """chat 模式指纹：内容指纹；聊天类条目带 time_hint 时并入时间。"""
    key = content_fingerprint(item)
    if item.get("type") in CHAT_LIKE_TYPES and item.get("time_hint"):
        key += "|t=" + _normalize_time(item["time_hint"])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


class Deduplicator:
    """跨屏去重器：chat 模式聊天类滑窗判重，global 模式全部全局判重。"""

    def __init__(self, window: int = 5, mode: str = MODE_CHAT):
        if mode not in (MODE_CHAT, MODE_GLOBAL):
            raise ValueError(f"未知去重模式: {mode}（可选 {MODE_CHAT}/{MODE_GLOBAL}）")
        # 普通内容最多跨 3 屏；长内容（长消息/大卡片）可达 4~5 屏，取 5 留余量
        self.window = max(1, window)
        self.mode = mode
        self._global: set[str] = set()          # 全部已收录指纹（非聊天类/global 判重用）
        self._screen_sets: dict[int, set[str]] = {}   # 各屏收录的指纹
        self._screen_order: list[int] = []      # 屏幕收录顺序（滑动窗口）

    def admit(self, screen: int, item: dict[str, Any]) -> bool:
        """判定条目是否收录（True=新条目，False=重复）。"""
        chat_like = self.mode == MODE_CHAT and item.get("type") in CHAT_LIKE_TYPES
        fp = fingerprint(item) if chat_like else content_fingerprint(item)

        current = self._screen_sets.setdefault(screen, set())
        if screen not in self._screen_order:
            self._screen_order.append(screen)
        if fp in current:
            return False  # 同屏完全相同 → 提取重复

        if chat_like:
            recent = self._screen_order[-(self.window + 1):-1]  # 不含当前屏
            if any(fp in self._screen_sets.get(s, set()) for s in recent):
                return False  # 重叠窗口内重采
        elif fp in self._global:
            return False

        current.add(fp)
        self._global.add(fp)
        return True
