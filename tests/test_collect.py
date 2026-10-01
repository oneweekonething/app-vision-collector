"""collect.py 纯函数单元测试（去重模式解析 + 导航任务组装）。"""

from __future__ import annotations

import sys
import io
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect
from collect import (
    _summary,
    build_arg_parser,
    build_navigation_task,
    resolve_dedup_mode,
    resolve_scroll_direction,
    run_collection,
)
from collector.adb.screenshot import Screenshot
from collector.config import CollectorConfig


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


class ResolveScrollDirectionTest(unittest.TestCase):
    def test_chat_apps_scroll_toward_history(self):
        # 聊天视图锚定在最新消息端：翻历史必须手指下滑（反向）
        self.assertEqual(resolve_scroll_direction("wechat"), "down")
        self.assertEqual(resolve_scroll_direction("微信"), "down")

    def test_feed_apps_scroll_up(self):
        for app in ("xiaohongshu", "红果免费短剧", "generic"):
            self.assertEqual(resolve_scroll_direction(app), "up", app)

    def test_independent_of_dedup_override(self):
        # 方向只看 App 形态：--dedup 被显式覆盖时微信仍向历史方向翻
        self.assertEqual(resolve_scroll_direction("wechat"), "down")


class CollectionRegressionTest(unittest.TestCase):
    def test_explicit_direction_reaches_device_with_either_dedup_mode(self):
        for dedup_mode in ("chat", "global"):
            with self.subTest(dedup=dedup_mode), tempfile.TemporaryDirectory() as data_dir:
                args = build_arg_parser().parse_args([
                    "--app", "wechat", "--no-navigate", "--max-screens", "2",
                    "--task", "从历史消息处手指上滑采集更新消息",
                    "--scroll-direction", "up", "--dedup", dedup_mode,
                    "--data-dir", data_dir,
                ])
                screenshot = Screenshot(b"test screenshot", 100, 200, "test time", "pending")
                with mock.patch("collect.adb.ensure_device", return_value="TEST"), \
                        mock.patch("collect.adb.capture", return_value=screenshot), \
                        mock.patch("collect.adb.swipe_to_next_screen") as swipe, \
                        mock.patch("collect.average_hash", return_value=None), \
                        mock.patch("collect.time.sleep"), \
                        mock.patch("collect.ExtractAgent") as extractor, \
                        mock.patch("builtins.print"):
                    extractor.return_value.extract.side_effect = [
                        {"items": [{"type": "text", "text": "第一屏"}]},
                        {"items": [{"type": "text", "text": "第二屏"}]},
                    ]
                    self.assertEqual(run_collection(args, CollectorConfig(api_key="test")), 0)
                swipe.assert_called_once_with("TEST", direction="up")

    def test_printed_verifier_command_preserves_paths_with_spaces_and_quotes(self):
        store = SimpleNamespace(
            session_dir=Path("/tmp/session's data"), screen_count=1,
            extracted_total=0, items=[], extract_failures_total=0,
        )
        script_path = Path("/tmp/skill's scripts/collect.py")
        output = io.StringIO()
        with mock.patch.object(collect, "__file__", str(script_path)), \
                mock.patch("sys.stdout", output):
            _summary(store, "completed", None, store.session_dir / "index.json")
        command = next(line.removeprefix("[校验] ") for line in output.getvalue().splitlines()
                       if line.startswith("[校验] "))
        self.assertEqual(shlex.split(command), [
            "python3", str(script_path.resolve().parent / "inspect_session.py"),
            str(store.session_dir),
        ])


if __name__ == "__main__":
    unittest.main()
