"""ActionGuard：导航动作的只读语义护栏。

动作集合本身只含浏览类操作，但 Tap 完全可能落在"发送 / 点赞 / 支付 /
删除"等写操作按钮上。本模块在 ADB 执行之前对每个动作做最后一道检查：

    VLM decision → parse action → ActionGuard → ADB execute
                                          ├─ allow
                                          ├─ deny（写操作/坐标非法）→ 反馈模型重新规划
                                          └─ 连续 deny 达阈值 → 终止导航

判定共三层，逐层收口：
1. 动作集合不含写原语（Tap/Swipe/Type/Back/Home/Launch/Wait）；
2. 模型自报语义：intent / target_text 命中写操作关键词即拒绝——模型
   既是 Planner 又写安全元数据，这一层会被瞒报绕过；
3. 独立核验：uiautomator 控件树给出点击坐标处的真实控件文本
   （见 adb/uitree.py），与模型自报无关，命中关键词即拒绝。

第 3 层是安全域隔离的关键：模型把"发送"按钮谎报成 open_detail 也拦得住。
控件树不可用时（FLAG_SECURE 等场景 dump 失败）自动退化为第 2 层并记录
降级。这仍是纵深防御而非形式化验证。
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

    def check(self, action: dict[str, Any],
              ui_texts: list[str] | None = None) -> GuardVerdict:
        """判定一个动作是否放行。

        ui_texts 为 uiautomator 控件树给出的点击目标文本（与模型自报
        无关的独立事实来源）：任一文本命中写操作关键词即拒绝。None 表示
        控件树不可用（dump 失败），此时退化为仅自报语义判定。
        """
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

        verdict = self._check_semantics(name, action)
        if not verdict.allowed:
            return verdict

        return self._check_ui_texts(name, ui_texts)

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

    def _check_ui_texts(self, name: str, ui_texts: list[str] | None) -> GuardVerdict:
        """独立核验层：控件树上点击位置的实际文本命中写关键词即拒绝。

        这一层不依赖模型自报——即使模型把"发送"按钮申报成
        intent="open_detail"、target_text="按钮"，只要控件树显示该
        坐标落在文本为"发送"的可点击控件上，动作仍会被拦截。
        """
        if not ui_texts:
            return GuardVerdict(True, "allow")
        for text in ui_texts:
            haystack = text.lower()
            for keyword in WRITE_KEYWORDS:
                if keyword in haystack:
                    return GuardVerdict(
                        False, "deny_write_action",
                        f"界面控件树显示点击目标文本「{text}」命中写操作关键词"
                        f"「{keyword}」（独立核验，与模型自报无关），已拒绝执行 {name}。"
                        "请改用浏览类方式完成导航，不要触发任何写操作。",
                    )
        return GuardVerdict(True, "allow")

    def _deny_write(self, name: str, keyword: str, where: str) -> GuardVerdict:
        return GuardVerdict(
            False, "deny_write_action",
            f"{where} 命中写操作关键词「{keyword}」，已拒绝执行 {name}。"
            "本采集器严格只读：请改用浏览类方式完成导航"
            "（搜索、返回、点击其他入口），不要触发任何写操作。",
        )
