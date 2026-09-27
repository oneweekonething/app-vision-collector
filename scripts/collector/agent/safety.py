"""ActionGuard：导航动作的只读语义护栏。

动作集合本身只含浏览类操作，但 Tap 完全可能落在"发送 / 点赞 / 支付 /
删除"等写操作按钮上——只靠动作白名单挡不住这种语义级写操作。本模块
在 ADB 执行之前对每个动作做最后一道检查：

    VLM decision → parse action → ActionGuard → ADB execute
                                          ├─ allow
                                          ├─ deny（写操作/坐标非法）→ 反馈模型重新规划
                                          └─ 连续 deny 达阈值 → 终止导航

判定依据是模型随动作申报的 intent 与 target_text（提示词要求提供），
以及坐标的合法性。这是软护栏而非形式化验证：模型瞒报意图时依赖
"动作集合不含写原语"这一层兜底，两层共同把写操作风险压到最低。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 写操作语义关键词。target_text / intent 命中即拒绝执行。
# 误杀（如群名恰好包含"关注"）由护栏反馈机制兜底：模型会换搜索等入口重试。
WRITE_KEYWORDS = (
    # 消息类
    "发送", "发布", "回复", "评论", "转发", "分享", "点赞", "赞一下", "喜欢",
    "收藏", "关注", "投币", "弹幕",
    # 交易类
    "支付", "付款", "购买", "买入", "下单", "提交", "结算", "转账", "充值", "打赏",
    # 破坏/变更类
    "删除", "清空", "移除", "撤回", "编辑", "修改", "安装", "卸载", "授权", "允许",
    "确认", "确定", "加入", "退出群聊", "解散",
    # 英文常见写法（intent 通常为英文短语）
    "send", "post", "publish", "reply", "comment", "share", "forward",
    "like", "follow", "favorite", "delete", "remove", "pay", "purchase",
    "buy", "submit", "confirm", "transfer", "authorize", "install",
    "uninstall", "join", "edit",
)

# 需要坐标的动作 → 坐标字段名
_COORD_FIELDS = {"Tap": ("element",), "Swipe": ("start", "end")}

VALID_ACTIONS = {"Tap", "Swipe", "Type", "Back", "Home", "Launch", "Wait"}


@dataclass
class GuardVerdict:
    """护栏判定结果。

    code: allow | deny_write_action | deny_invalid_coordinates | deny_invalid_action
    detail: 面向模型的人类可读反馈（deny 时注入对话历史）。
    """

    allowed: bool
    code: str
    detail: str = ""


class ActionGuard:
    """对解析后的动作做只读安全检查。"""

    def check(self, action: dict[str, Any]) -> GuardVerdict:
        if action.get("kind") == "finish":
            return GuardVerdict(True, "allow")

        name = str(action.get("name", ""))
        if name not in VALID_ACTIONS:
            return GuardVerdict(
                False, "deny_invalid_action",
                f"未知动作「{name}」，可用动作: {sorted(VALID_ACTIONS)}",
            )

        verdict = self._check_coordinates(name, action)
        if not verdict.allowed:
            return verdict

        return self._check_semantics(name, action)

    # ------------------------------------------------------------------ parts

    def _check_coordinates(self, name: str, action: dict[str, Any]) -> GuardVerdict:
        for field in _COORD_FIELDS.get(name, ()):
            point = action.get(field)
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return GuardVerdict(
                    False, "deny_invalid_coordinates",
                    f"{name} 缺少合法的 {field}=[x,y] 归一化坐标，请重新给出。",
                )
            for value in point:
                if not isinstance(value, (int, float)) or isinstance(value, bool) \
                        or not 0 <= value <= 999:
                    return GuardVerdict(
                        False, "deny_invalid_coordinates",
                        f"{name} 的 {field}={list(point)} 超出 0-999 归一化范围，"
                        "请重新给出合法坐标。",
                    )
        return GuardVerdict(True, "allow")

    def _check_semantics(self, name: str, action: dict[str, Any]) -> GuardVerdict:
        intent = str(action.get("intent") or "").lower()
        target_text = str(action.get("target_text") or "")

        for keyword in WRITE_KEYWORDS:
            if keyword in intent:
                return self._deny_write(
                    name, keyword, f"意图 intent「{action.get('intent')}」")
            if keyword in target_text:
                return self._deny_write(
                    name, keyword, f"目标元素「{target_text}」")

        if name == "Type" and not str(action.get("text") or "").strip():
            return GuardVerdict(
                False, "deny_invalid_action",
                "Type 动作的 text 为空；如需清除输入框请用 Back 关闭键盘。",
            )
        return GuardVerdict(True, "allow")

    def _deny_write(self, name: str, keyword: str, where: str) -> GuardVerdict:
        return GuardVerdict(
            False, "deny_write_action",
            f"{where} 命中写操作关键词「{keyword}」，已拒绝执行 {name}。"
            "本采集器严格只读：请改用浏览类方式完成导航"
            "（搜索、返回、点击其他入口），不要触发任何写操作。",
        )
