"""ActionGuard 只读护栏单元测试（无需设备与模型）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.agent.safety import ActionGuard  # noqa: E402


class ActionGuardTest(unittest.TestCase):
    def setUp(self):
        self.guard = ActionGuard()

    def test_allows_neutral_tap(self):
        verdict = self.guard.check({
            "kind": "do", "name": "Tap", "element": [500, 300],
            "intent": "open_chat", "target_text": "AI交流群",
        })
        self.assertTrue(verdict.allowed)

    def test_denies_tap_by_target_text(self):
        for target in ["发送", "立即支付", "删除", "确认", "点赞", "关注"]:
            verdict = self.guard.check({
                "kind": "do", "name": "Tap", "element": [500, 900],
                "intent": "click_button", "target_text": target,
            })
            self.assertFalse(verdict.allowed, target)
            self.assertEqual(verdict.code, "deny_write_action")
            self.assertIn(target, verdict.detail)

    def test_denies_tap_by_intent(self):
        for intent in ["send_message", "like_post", "pay_now", "delete_chat", "follow_user"]:
            verdict = self.guard.check({
                "kind": "do", "name": "Tap", "element": [500, 900],
                "intent": intent, "target_text": "某按钮",
            })
            self.assertFalse(verdict.allowed, intent)
            self.assertEqual(verdict.code, "deny_write_action")

    def test_denies_out_of_range_coordinates(self):
        verdict = self.guard.check({
            "kind": "do", "name": "Tap", "element": [5000, 300],
            "intent": "open", "target_text": "列表项",
        })
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "deny_invalid_coordinates")

    def test_denies_missing_coordinates(self):
        verdict = self.guard.check({"kind": "do", "name": "Tap"})
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "deny_invalid_coordinates")

        verdict = self.guard.check({"kind": "do", "name": "Swipe", "start": [100, 500]})
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "deny_invalid_coordinates")

    def test_denies_unknown_action(self):
        verdict = self.guard.check({"kind": "do", "name": "LongPress", "element": [1, 1]})
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "deny_invalid_action")

    def test_denies_empty_type(self):
        verdict = self.guard.check({"kind": "do", "name": "Type", "text": "  "})
        self.assertFalse(verdict.allowed)

    def test_allows_non_tap_actions(self):
        for action in [
            {"kind": "do", "name": "Back"},
            {"kind": "do", "name": "Home"},
            {"kind": "do", "name": "Wait", "duration": "2 seconds"},
            {"kind": "do", "name": "Launch", "app": "com.tencent.mm"},
            {"kind": "do", "name": "Type", "text": "AI通识", "intent": "search"},
            {"kind": "do", "name": "Swipe", "start": [500, 700], "end": [500, 300],
             "intent": "scroll"},
            {"kind": "finish", "message": "已进入群聊"},
        ]:
            verdict = self.guard.check(action)
            self.assertTrue(verdict.allowed, action)

    def test_swipe_restricted_to_vertical_scroll(self):
        def swipe(start, end):
            return {"kind": "do", "name": "Swipe", "start": start, "end": end,
                    "intent": "scroll", "target_text": "列表"}

        # 近垂直滚动放行（含轻微倾斜与回滚）
        for start, end in [
            ([500, 700], [500, 300]),   # 标准上滑
            ([480, 300], [520, 700]),   # 下滑 + 轻微倾斜
        ]:
            self.assertTrue(self.guard.check(swipe(start, end)).allowed, (start, end))

        # 横滑删除 / 斜滑 / 位移过短 / 边缘手势区 → 拒绝
        for start, end in [
            ([100, 500], [900, 500]),   # 纯横向（左滑删除手势）
            ([500, 700], [700, 300]),   # 对角线 dx=200 > 0.35*dy=140
            ([500, 500], [500, 560]),   # 纵向位移过短
            ([50, 700], [50, 300]),     # 起点在左边缘手势区
        ]:
            verdict = self.guard.check(swipe(start, end))
            self.assertFalse(verdict.allowed, (start, end))
            self.assertEqual(verdict.code, "deny_non_scroll_swipe")

    def test_ui_texts_independent_layer(self):
        # 模型自报干净，但控件树显示目标是"发送" → 独立核验层拒绝
        action = {
            "kind": "do", "name": "Tap", "element": [940, 930],
            "intent": "open_detail", "target_text": "按钮",
        }
        verdict = self.guard.check(action, ui_texts=["发送"])
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.code, "deny_write_action")
        self.assertIn("独立核验", verdict.detail)

        # 控件树文本干净 → 放行
        verdict = self.guard.check(action, ui_texts=["AI交流群", ""])
        self.assertTrue(verdict.allowed)

        # dump 失败（None）/ 无文本（[]）→ 退化为自报判定，本例自报干净放行
        self.assertTrue(self.guard.check(action, ui_texts=None).allowed)
        self.assertTrue(self.guard.check(action, ui_texts=[]).allowed)

    def test_ui_texts_english_keywords(self):
        verdict = self.guard.check({
            "kind": "do", "name": "Tap", "element": [500, 900],
            "intent": "open", "target_text": "x",
        }, ui_texts=["Follow"])
        self.assertFalse(verdict.allowed)


if __name__ == "__main__":
    unittest.main()
