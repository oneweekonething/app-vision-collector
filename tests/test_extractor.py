"""ExtractAgent 解析与修复逻辑的单元测试：用假 client，不联网、不需要设备。

运行: python3 -m unittest discover tests -v
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.adb.screenshot import Screenshot  # noqa: E402
from collector.agent.extractor import ExtractionError, ExtractAgent, _parse_json  # noqa: E402
from collector.config import CollectorConfig  # noqa: E402

FAKE_PNG = b"\x89PNG-extractor-test-" + b"z" * 512

GOOD_JSON = '{"screen_summary": "两条消息", "items": [{"type": "message", "text": "你好", "sender": "张三"}, {"type": "message", "text": "  ", "sender": "李四"}]}'
BAD_JSON = '{"screen_summary": "xx", "items": [{"type": "message", "text": "你好"}]'  # 缺最外层右括号


def make_screenshot() -> Screenshot:
    return Screenshot(
        png_bytes=FAKE_PNG,
        width=540,
        height=1000,
        captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        sha256=hashlib.sha256(FAKE_PNG).hexdigest(),
    )


class FakeCompletions:
    """依次返回预设响应，并记录每次调用的消息参数。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.responses.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def make_agent(responses: list[str], extract_retries: int = 2) -> tuple[ExtractAgent, FakeCompletions]:
    agent = ExtractAgent(
        CollectorConfig(api_key="test", extract_retries=extract_retries),
        app="generic", task="单测",
    )
    fake = FakeCompletions(responses)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return agent, fake


def is_image_call(call: dict) -> bool:
    return isinstance(call["messages"][0]["content"], list)


class ParseJsonTest(unittest.TestCase):
    def test_plain_json(self):
        data = _parse_json(GOOD_JSON)
        self.assertEqual(len(data["items"]), 2)

    def test_title_only_items_survive(self):
        # 卡片型条目（红果/小红书）可能只有 title 没有 text——必须保留；
        # 只有 text 和 title 双空的才是提取噪声
        raw = ('{"items": ['
               '{"type": "note", "title": "都市重生之最强剑仙", "text": ""},'
               '{"type": "message", "title": null, "text": "你好"},'
               '{"type": "noise", "title": "  ", "text": ""}]}')
        agent, _ = make_agent([f'```json\n{raw}\n```'])
        out = agent.extract(make_screenshot())
        self.assertEqual(len(out["items"]), 2)
        self.assertEqual(out["items"][0]["title"], "都市重生之最强剑仙")
        self.assertEqual(out["items"][1]["text"], "你好")

    def test_fenced_json_with_junk(self):
        raw = f"好的，结果如下：\n```json\n{GOOD_JSON}\n```\n以上。"
        self.assertEqual(len(_parse_json(raw)["items"]), 2)

    def test_missing_items_raises(self):
        with self.assertRaises(ValueError):
            _parse_json('{"screen_summary": "no items here"}')

    def test_no_braces_raises(self):
        with self.assertRaises(ValueError):
            _parse_json("这不是 JSON")


class ExtractTest(unittest.TestCase):
    def test_success_filters_empty_text(self):
        agent, fake = make_agent([GOOD_JSON])
        data = agent.extract(make_screenshot())
        self.assertEqual(data["message_count"], 1)          # 空白 text 条目被过滤
        self.assertEqual(data["raw_response"], GOOD_JSON)
        self.assertNotIn("repair_response", data)
        self.assertEqual(len(fake.calls), 1)

    def test_repair_without_second_image_call(self):
        agent, fake = make_agent([BAD_JSON, GOOD_JSON])
        data = agent.extract(make_screenshot())
        self.assertEqual(data["message_count"], 1)
        # 证据以带图调用的原始输出为准，修复输出单独留存
        self.assertEqual(data["raw_response"], BAD_JSON)
        self.assertEqual(data["repair_response"], GOOD_JSON)
        # 第 1 次带图 + 第 2 次纯文本修复（不再发图）
        self.assertEqual(len(fake.calls), 2)
        self.assertTrue(is_image_call(fake.calls[0]))
        self.assertFalse(is_image_call(fake.calls[1]))

    def test_repair_failure_falls_back_to_image_retry(self):
        agent, fake = make_agent([BAD_JSON, "修复失败，抱歉", GOOD_JSON])
        data = agent.extract(make_screenshot())
        self.assertEqual(data["message_count"], 1)
        self.assertNotIn("repair_response", data)
        # 带图、修复、再带图
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(is_image_call(fake.calls[2]))

    def test_retries_exhausted_raises(self):
        agent, fake = make_agent([BAD_JSON, "no json", BAD_JSON, "no json"], extract_retries=2)
        with self.assertRaises(ExtractionError) as ctx:
            agent.extract(make_screenshot())
        # 每次尝试 = 1 次带图 + 1 次修复
        self.assertEqual(len(fake.calls), 4)
        # 异常携带最后一次带图调用的原始输出，供失败留证
        self.assertEqual(ctx.exception.last_raw, BAD_JSON)


if __name__ == "__main__":
    unittest.main()
