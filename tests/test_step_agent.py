"""StepAgent 单元测试：解析、护栏集成、失败反馈与导航结果（mock 设备与模型）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector import adb  # noqa: E402
from collector.adb.connection import AdbCommandError  # noqa: E402
from collector.agent.step_agent import (  # noqa: E402
    NavigationResult,
    StepAgent,
    _assistant_record,
    _parse_action,
    _parse_verdict,
)
from collector.config import CollectorConfig  # noqa: E402


# ------------------------------------------------------------------ parsing

class ParseActionTest(unittest.TestCase):
    def test_do_tap(self):
        action = _parse_action('<think>点击</think><answer>do(action="Tap", element=[500, 300])</answer>')
        self.assertEqual(action["kind"], "do")
        self.assertEqual(action["name"], "Tap")
        self.assertEqual(action["element"], [500, 300])

    def test_do_type_and_launch(self):
        action = _parse_action('do(action="Type", text="AI通识交流")')
        self.assertEqual(action["name"], "Type")
        self.assertEqual(action["text"], "AI通识交流")

        action = _parse_action('do(action="Launch", app="com.tencent.mm")')
        self.assertEqual(action["name"], "Launch")
        self.assertEqual(action["app"], "com.tencent.mm")

    def test_finish(self):
        action = _parse_action('finish(message="已进入群聊")')
        self.assertEqual(action["kind"], "finish")
        self.assertEqual(action["message"], "已进入群聊")

    def test_unparseable_falls_back_to_wait(self):
        action = _parse_action("我觉得应该再等等看")
        self.assertEqual(action["kind"], "do")
        self.assertEqual(action["name"], "Wait")

    def test_escaped_quotes_in_text(self):
        action = _parse_action('do(action="Type", text="他说 \\"hello\\"" )')
        self.assertEqual(action["text"], '他说 "hello"')

    def test_parens_inside_quoted_text(self):
        action = _parse_action('do(action="Type", text="回复(1)参加活动")')
        self.assertEqual(action["text"], "回复(1)参加活动")

    def test_escaped_quote_in_finish_message(self):
        action = _parse_action('finish(message="进入\\"AI\\"群")')
        self.assertEqual(action["message"], '进入"AI"群')

    def test_intent_and_target_text_parsed(self):
        action = _parse_action(
            'do(action="Tap", element=[320, 500], intent="open_chat", target_text="AI交流群")')
        self.assertEqual(action["intent"], "open_chat")
        self.assertEqual(action["target_text"], "AI交流群")

    def test_json_action_object(self):
        action = _parse_action(
            '```json\n{"action": "tap", "point": [500, 300], '
            '"intent": "open_chat", "target_text": "AI交流群"}\n```')
        self.assertEqual(action["kind"], "do")
        self.assertEqual(action["name"], "Tap")
        self.assertEqual(action["element"], [500, 300])
        self.assertEqual(action["target_text"], "AI交流群")

    def test_json_finish(self):
        action = _parse_action('{"action": "finish", "message": "已到达搜索结果页"}')
        self.assertEqual(action["kind"], "finish")
        self.assertEqual(action["message"], "已到达搜索结果页")

    def test_lower_case_action_normalized(self):
        action = _parse_action('do(action="tap", element=[1, 2])')
        self.assertEqual(action["name"], "Tap")


class VerdictParseTest(unittest.TestCase):
    def test_yes_no_first_line(self):
        self.assertEqual(_parse_verdict("YES\n看到群聊消息列表")[0], True)
        self.assertEqual(_parse_verdict("NO\n还在聊天列表页")[0], False)

    def test_chinese_fallback(self):
        self.assertEqual(_parse_verdict("是\n已进入目标群")[0], True)
        self.assertEqual(_parse_verdict("否\n页面不对")[0], False)

    def test_unparseable(self):
        self.assertIsNone(_parse_verdict("看起来还行") [0])

    def test_think_block_stripped(self):
        satisfied, _ = _parse_verdict("<think>分析</think>NO\n不对")
        self.assertFalse(satisfied)


class AssistantRecordTest(unittest.TestCase):
    def test_extracts_think_block(self):
        record = _assistant_record('<think>先看列表</think>do(action="Tap", element=[1,2])')
        self.assertEqual(record["role"], "assistant")
        self.assertIn("<think>先看列表</think>", record["content"])
        self.assertIn('do(action="Tap", element=[1,2])', record["content"])

    def test_no_think_block_is_tolerated(self):
        record = _assistant_record("finish(message=\"done\")")
        self.assertIn("<think></think>", record["content"])
        self.assertIn("finish(message=\"done\")", record["content"])


# -------------------------------------------------------------- agent loop

class FakeResponse:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]


class FakeClient:
    """按顺序吐出预置回复的假 OpenAI 客户端。"""

    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.requests: list[dict] = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return FakeResponse(outer._contents.pop(0))

        class Chat:
            completions = Completions()

        self.chat = Chat()


def fake_screenshot() -> adb.Screenshot:
    return adb.Screenshot(
        png_bytes=b"\x89PNG" + b"x" * 2048, width=1080, height=2400,
        captured_at="2026-09-28T10:00:00+08:00", sha256="0" * 64,
    )


def device_mocks():
    """导航循环依赖的 adb 函数 mock 集合。"""
    return {
        "capture": mock.patch("collector.adb.capture", return_value=fake_screenshot()),
        "current_app": mock.patch(
            "collector.adb.get_current_app",
            return_value="com.tencent.mm/.ui.LauncherUI"),
        "screen_size": mock.patch(
            "collector.adb.get_screen_size", return_value=(1080, 2400)),
        "tap": mock.patch("collector.adb.tap"),
        "swipe": mock.patch("collector.adb.swipe"),
        "back": mock.patch("collector.adb.back"),
        "home": mock.patch("collector.adb.home"),
        "type_safe": mock.patch("collector.adb.input_text_safe"),
        "launch": mock.patch("collector.adb.launch_app", return_value=True),
        "ui_texts": mock.patch("collector.adb.ui_texts_at_point", return_value=[]),
    }


def run_agent(responses: list[str], nav_steps: int = 10, nav_verify: bool = True):
    config = CollectorConfig(api_key="test", nav_steps=nav_steps, nav_verify=nav_verify)
    agent = StepAgent(config, device_id="TEST", client=FakeClient(responses))
    patches = device_mocks()
    mocks = {name: p.start() for name, p in patches.items()}
    try:
        result = agent.run("进入「AI交流群」的聊天页面")
    finally:
        for p in patches.values():
            p.stop()
    return result, agent, mocks


class NavigationFlowTest(unittest.TestCase):
    def test_successful_navigation_with_verify(self):
        result, agent, mocks = run_agent([
            'do(action="Tap", element=[500, 300], intent="open_chat", target_text="AI交流群")',
            'finish(message="已进入群聊")',
            "YES\n当前是「AI交流群」的消息页面",
        ])
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "finished")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.current_app, "com.tencent.mm/.ui.LauncherUI")
        mocks["tap"].assert_called_once_with(540, 720, "TEST")

    def test_verify_rejected_then_recovers(self):
        result, agent, mocks = run_agent([
            'finish(message="已进入群聊")',
            "NO\n还在聊天列表页",
            'do(action="Tap", element=[500, 400], intent="open_chat", target_text="AI交流群")',
            'finish(message="这次真的进入了")',
            "YES\n看到了目标群消息",
        ])
        self.assertTrue(result.success)
        self.assertEqual(result.steps, 3)  # finish(核验未过) → tap → finish(核验通过)
        feedbacks = [m for m in agent.context
                     if m["role"] == "user" and "VERIFY" in str(m["content"])]
        self.assertEqual(len(feedbacks), 1)
        mocks["tap"].assert_called_once()

    def test_verify_disabled_trusts_finish(self):
        result, _, _ = run_agent(['finish(message="已进入")'], nav_verify=False)
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "finished")

    def test_max_steps_fails(self):
        result, _, mocks = run_agent([
            'do(action="Swipe", start=[500, 700], end=[500, 300], intent="scroll")'
        ] * 3, nav_steps=3)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "max_steps")
        self.assertEqual(result.steps, 3)
        self.assertEqual(mocks["swipe"].call_count, 3)

    def test_navigation_result_shape(self):
        result, _, _ = run_agent(['finish(message="ok")'], nav_verify=False)
        self.assertIsInstance(result, NavigationResult)
        for field in ("success", "reason", "message", "steps", "current_app"):
            self.assertTrue(hasattr(result, field))


class GuardIntegrationTest(unittest.TestCase):
    def test_dangerous_tap_blocked_and_reported(self):
        dangerous = 'do(action="Tap", element=[500, 960], intent="send_message", target_text="发送")'
        result, agent, mocks = run_agent([dangerous] * 3)
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "safety_aborted")
        mocks["tap"].assert_not_called()
        guard_feedback = [m for m in agent.context
                          if m["role"] == "user" and "GUARD" in str(m["content"])]
        self.assertEqual(len(guard_feedback), 3)

    def test_allowed_after_denial_resets_streak(self):
        result, _, mocks = run_agent([
            'do(action="Tap", element=[500, 960], intent="send_message", target_text="发送")',
            'do(action="Tap", element=[500, 300], intent="open_chat", target_text="AI交流群")',
            'do(action="Tap", element=[500, 960], intent="send_message", target_text="发送")',
            'do(action="Tap", element=[500, 310], intent="open_chat", target_text="AI交流群")',
            "finish(message=\"已进入\")",
            "YES\n目标页面",
        ])
        # 两次拒绝未连续 → 不触发 safety_aborted，最终成功
        self.assertTrue(result.success)
        self.assertEqual(mocks["tap"].call_count, 2)

    def test_invalid_coordinates_blocked(self):
        result, _, mocks = run_agent([
            'do(action="Tap", element=[5000, 300], intent="open", target_text="列表")'
        ] * 3)
        self.assertEqual(result.reason, "safety_aborted")
        mocks["tap"].assert_not_called()

    def test_tap_without_element_is_guarded_not_crash(self):
        # 缺 element 的 Tap 必须走护栏 [GUARD] 反馈，而不是 KeyError 冒泡
        for responses in (
            ['do(action="Tap", intent="open_detail")'] * 3,                     # DSL
            ['{"action": "tap", "intent": "open_detail"}'] * 3,                 # JSON
            ['do(action="Tap", target_text="某条目")'] * 3,                      # 有自报无坐标
        ):
            with self.subTest(responses=responses[0]):
                result, agent, mocks = run_agent(responses)
                self.assertFalse(result.success)
                self.assertEqual(result.reason, "safety_aborted")
                mocks["tap"].assert_not_called()
                guard_feedback = [m for m in agent.context
                                  if m["role"] == "user" and "GUARD" in str(m["content"])]
                self.assertEqual(len(guard_feedback), 3)

    def test_horizontal_swipe_blocked(self):
        # 左滑删除/切换是写手势：近垂直滚动之外一律拒绝
        result, _, mocks = run_agent([
            'do(action="Swipe", start=[100, 500], end=[900, 500], intent="scroll")'
        ] * 3)
        self.assertEqual(result.reason, "safety_aborted")
        mocks["swipe"].assert_not_called()

    def test_vertical_scroll_still_allowed(self):
        result, _, mocks = run_agent([
            'do(action="Swipe", start=[500, 700], end=[500, 300], intent="scroll")',
            "finish(message=\"ok\")",
            "YES\nok",
        ])
        self.assertTrue(result.success)
        mocks["swipe"].assert_called_once()

    def test_ui_tree_catches_mislabeled_send_button(self):
        # 模型谎报：真实是"发送"按钮，自报却是 open_detail/按钮。
        # 控件树独立核验必须拦住——这是安全域隔离的关键场景。
        lying = ('do(action="Tap", element=[940, 930], intent="open_detail", '
                 'target_text="按钮")')
        config = CollectorConfig(api_key="test", nav_steps=10)
        client = FakeClient([lying] * 3)
        agent = StepAgent(config, device_id="TEST", client=client)
        with mock.patch("collector.adb.capture", return_value=fake_screenshot()), \
                mock.patch("collector.adb.get_current_app", return_value="unknown"), \
                mock.patch("collector.adb.get_screen_size", return_value=(1080, 2400)), \
                mock.patch("collector.adb.tap") as tap_mock, \
                mock.patch("collector.adb.ui_texts_at_point",
                           return_value=["发送"]) as ui_mock:
            result = agent.run("进入群聊")
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "safety_aborted")
        tap_mock.assert_not_called()
        self.assertEqual(ui_mock.call_count, 3)
        guard_feedback = [m for m in agent.context
                          if m["role"] == "user" and "GUARD" in str(m["content"])]
        self.assertIn("独立核验", str(guard_feedback[0]["content"]))

    def test_ui_tree_degrades_to_self_report_on_dump_failure(self):
        # dump 失败返回 None → 退化为仅自报语义判定，干净自报放行
        tap = ('do(action="Tap", element=[500, 300], intent="open_chat", '
               'target_text="AI交流群")')
        config = CollectorConfig(api_key="test", nav_steps=10)
        client = FakeClient([tap, "finish(message=\"ok\")", "YES\nok"])
        agent = StepAgent(config, device_id="TEST", client=client)
        with mock.patch("collector.adb.capture", return_value=fake_screenshot()), \
                mock.patch("collector.adb.get_current_app", return_value="unknown"), \
                mock.patch("collector.adb.get_screen_size", return_value=(1080, 2400)), \
                mock.patch("collector.adb.tap") as tap_mock, \
                mock.patch("collector.adb.ui_texts_at_point", return_value=None) as ui_mock:
            result = agent.run("进入群聊")
        self.assertTrue(result.success)
        tap_mock.assert_called_once()
        ui_mock.assert_called_once()


class FailureFeedbackTest(unittest.TestCase):
    def test_adb_failure_feeds_observation_then_recovers(self):
        config = CollectorConfig(api_key="test", nav_steps=10)
        client = FakeClient([
            'do(action="Tap", element=[500, 300], intent="open_chat", target_text="AI交流群")',
            'do(action="Tap", element=[500, 310], intent="open_chat", target_text="AI交流群")',
            'finish(message="已进入群聊")',
            "YES\n目标页面",
        ])
        agent = StepAgent(config, device_id="TEST", client=client)
        with mock.patch("collector.adb.capture", return_value=fake_screenshot()), \
                mock.patch("collector.adb.get_current_app",
                           return_value="com.tencent.mm/.ui.LauncherUI"), \
                mock.patch("collector.adb.get_screen_size", return_value=(1080, 2400)), \
                mock.patch("collector.adb.tap",
                           side_effect=[AdbCommandError(["input", "tap"], 1, "device offline"), None]):
            result = agent.run("进入群聊")
        self.assertTrue(result.success)
        self.assertEqual(result.reason, "finished")
        failures = [m for m in agent.context
                    if m["role"] == "user" and "ACTION_FAILED" in str(m["content"])]
        self.assertEqual(len(failures), 1)
        self.assertIn("device offline", str(failures[0]["content"]))

    def test_consecutive_device_errors_abort(self):
        config = CollectorConfig(api_key="test", nav_steps=10)
        client = FakeClient([
            'do(action="Tap", element=[500, 300], intent="open_chat", target_text="AI交流群")'
        ] * 3)
        agent = StepAgent(config, device_id="TEST", client=client)
        with mock.patch("collector.adb.capture", return_value=fake_screenshot()), \
                mock.patch("collector.adb.get_current_app",
                           return_value="com.tencent.mm/.ui.LauncherUI"), \
                mock.patch("collector.adb.get_screen_size", return_value=(1080, 2400)), \
                mock.patch("collector.adb.tap",
                           side_effect=AdbCommandError(["input", "tap"], 1, "device offline")):
            result = agent.run("进入群聊")
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "device_error")
        failures = [m for m in agent.context
                    if m["role"] == "user" and "ACTION_FAILED" in str(m["content"])]
        self.assertEqual(len(failures), 3)

    def test_type_uses_safe_input(self):
        result, _, mocks = run_agent([
            'do(action="Type", text="AI通识", intent="search")',
            "finish(message=\"ok\")",
            "YES\n页面正确",
        ])
        self.assertTrue(result.success)
        mocks["type_safe"].assert_called_once_with("AI通识", "TEST")
        mocks["tap"].assert_not_called()

    def test_launch_failure_aborts_after_streak(self):
        config = CollectorConfig(api_key="test", nav_steps=10)
        client = FakeClient(['do(action="Launch", app="com.not.exist")'] * 3)
        agent = StepAgent(config, device_id="TEST", client=client)
        with mock.patch("collector.adb.capture", return_value=fake_screenshot()), \
                mock.patch("collector.adb.get_current_app", return_value="unknown"), \
                mock.patch("collector.adb.launch_app", return_value=False):
            result = agent.run("打开微信")
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "action_failed")


if __name__ == "__main__":
    unittest.main()
