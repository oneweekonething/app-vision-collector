"""collect.py 纯函数单元测试（去重模式解析）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collect import resolve_dedup_mode  # noqa: E402


class ResolveDedupModeTest(unittest.TestCase):
    def test_auto_by_app(self):
        self.assertEqual(resolve_dedup_mode("wechat"), "chat")
        self.assertEqual(resolve_dedup_mode("微信"), "chat")
        self.assertEqual(resolve_dedup_mode("红果免费短剧"), "global")
        self.assertEqual(resolve_dedup_mode("xiaohongshu"), "global")
        self.assertEqual(resolve_dedup_mode("generic"), "global")

    def test_explicit_choice_wins(self):
        self.assertEqual(resolve_dedup_mode("红果免费短剧", "chat"), "chat")
        self.assertEqual(resolve_dedup_mode("wechat", "global"), "global")


if __name__ == "__main__":
    unittest.main()
