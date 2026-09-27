"""StepAgent 纯函数部分的单元测试（不需要设备与模型）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.agent.step_agent import _assistant_record, _parse_action  # noqa: E402


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


class AssistantRecordTest(unittest.TestCase):
    def test_extracts_think_block(self):
        record = _assistant_record('<think>先看列表</think>do(action="Tap", element=[1,2])')
        self.assertEqual(record["role"], "assistant")
        self.assertIn("<think>先看列表</think>", record["content"])
        self.assertIn('do(action="Tap", element=[1,2])', record["content"])

    def test_no_think_block_is_tolerated(self):
        record = _assistant_record("finish(message=\"done\")")
        self.assertIn("<think></think>", record["content"])
        self.assertIn('finish(message="done")', record["content"])


if __name__ == "__main__":
    unittest.main()
