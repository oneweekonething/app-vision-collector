"""collect.py 纯函数单元测试（去重模式解析 + 导航任务组装）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collect import build_navigation_task, resolve_dedup_mode  # noqa: E402


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


class BuildNavigationTaskTest(unittest.TestCase):
    def test_explicit_task_wins_over_target(self):
        # 显式 --task 不被 App 模板覆盖
        self.assertEqual(
            build_navigation_task("红果免费短剧", "某短剧", "自定义导航任务"),
            "自定义导航任务")

    def test_hongguo_template_used_with_target(self):
        nav = build_navigation_task("红果免费短剧", "AI交流群", "")
        self.assertIn("com.phoenix.read", nav)
        self.assertIn("AI交流群", nav)

    def test_hongguo_chinese_and_pinyin_keys(self):
        for app in ("红果", "hongguo"):
            self.assertIn("红果免费短剧 App", build_navigation_task(app, "x", ""))

    def test_generic_template_fallback(self):
        self.assertEqual(build_navigation_task("某小众app", "关键词", ""), "关键词")

    def test_no_target_no_task(self):
        self.assertEqual(
            build_navigation_task("红果免费短剧", "", ""),
            "打开 红果免费短剧 并停留在需要采集信息的页面")


if __name__ == "__main__":
    unittest.main()
