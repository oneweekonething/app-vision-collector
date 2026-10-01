"""调用者工作流：无设备、无模型 API，验证命令之间的真实证据与状态恢复。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import DEFAULT, patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import caller_collect as caller
from collector.adb.screenshot import Screenshot
from inspect_session import verify_session

PNG = b"simulated-screenshot" * 100
ITEM = {"type": "text", "sender": "张三", "text": "今晚八点开会", "time_hint": None}


class CallerCollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="caller skill ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.action_file = self.root / "action.json"
        self.result_file = self.root / "result.json"
        mock_adb = patch.multiple(caller.adb, **dict.fromkeys([
            "list_devices", "ensure_device", "capture", "get_current_app", "get_screen_size",
            "ui_texts_at_point", "tap", "swipe", "input_text_safe", "back", "home", "launch_app",
        ], DEFAULT))
        self.device = mock_adb.start()
        self.addCleanup(mock_adb.stop)
        self.device["ensure_device"].return_value = "SIMULATED"
        self.device["list_devices"].return_value = ["SIMULATED"]
        self.device["get_current_app"].return_value = "com.tencent.mm"
        self.device["get_screen_size"].return_value = (540, 1000)
        self.device["ui_texts_at_point"].return_value = ["测试群"]
        self.device["launch_app"].return_value = True
        self.device["capture"].return_value = Screenshot(
            PNG, 540, 1000, "2026-10-01T10:00:00+08:00", hashlib.sha256(PNG).hexdigest(),
        )
        self.session = Path(self.command("start", "--app", "wechat", "--target", "测试群",
                                         "--task", "打开微信，采集测试群消息",
                                         "--data-dir", str(self.root / "output"))[1]["session"])

    def command(self, *args, code=0):
        output = io.StringIO()
        with redirect_stdout(output):
            actual = caller.main(list(args))
        result = json.loads(output.getvalue())
        self.assertEqual(actual, code, result)
        return actual, result

    def session_command(self, name, *args, code=0):
        return self.command(name, str(self.session), *args, code=code)[1]

    def action(self, value, code=0):
        self.action_file.write_text(json.dumps(value))
        return self.session_command("action", "--action-file", str(self.action_file), code=code)

    def record(self, items=None, code=0):
        self.result_file.write_text(json.dumps({"items": [ITEM] if items is None else items}))
        return self.session_command("record", "--result-file", str(self.result_file), code=code)

    def events(self):
        return [json.loads(line) for line in (self.session / "manifest.jsonl").read_text().splitlines()]

    def assert_valid(self):
        ok, report = verify_session(self.session)
        self.assertTrue(ok, "\n".join(report))

    def test_complete_flow_resumes_dedup_and_keeps_original_evidence(self):
        observation = self.session_command("observe")
        self.assertEqual(Path(observation["screenshot"]).read_bytes(), PNG)
        self.assertEqual(caller.SessionStore.resume(self.session).screen_count, 0)
        self.action({"name": "Tap", "element": [500, 210], "intent": "open_chat",
                     "target_text": "测试群"})
        self.device["tap"].assert_called_once()
        capture = self.session_command("capture")
        self.assertEqual(Path(capture["screenshot"]).read_bytes(), PNG)
        self.assertIn("items", capture["prompt"])
        self.assertFalse((self.session / "extracted/screen-0001.json").exists())
        self.assertEqual(self.record()["new"], 1)
        self.action({"name": "Swipe", "start": [500, 300], "end": [500, 700],
                     "intent": "browse_history"})
        self.device["swipe"].assert_called_once()
        self.session_command("observe")
        self.session_command("capture")
        self.assertEqual(self.record([ITEM, {"type": "text", "text": "第二条"}])["duplicates"], 1)
        result = self.session_command("finish")
        index = json.loads(Path(result["index"]).read_text())
        self.assertEqual(index["execution_mode"], "caller")
        self.assertEqual(index["totals"]["screens"], 2)
        self.assertEqual(index["totals"]["items_unique"], 2)
        self.assertEqual([x["item_id"] for x in index["items"]], ["itm_000001", "itm_000002"])
        self.assertEqual(index["items"][1]["evidence"]["screen_index"], 2)
        self.assertEqual(index["items"][0]["evidence"]["model"], "caller-vision")
        self.assert_valid()

    def test_check_requires_no_key_but_does_not_claim_to_validate_vision(self):
        with patch.dict(os.environ, {}, clear=True):
            result = self.command("check")[1]
        self.assertFalse(result["api_key_required"])
        self.assertIn("调用者", result["caller_requirement"])

    def test_pending_screen_blocks_navigation_and_next_capture(self):
        self.session_command("capture")
        previous = (self.session / "manifest.jsonl").read_bytes()
        for name in ("observe", "capture"):
            self.session_command(name, code=1)
        self.action({"name": "Back"}, code=1)
        self.assertEqual((self.session / "manifest.jsonl").read_bytes(), previous)
        self.device["back"].assert_not_called()

    def test_record_cannot_overwrite_and_finished_session_cannot_resume(self):
        self.session_command("capture")
        self.record()
        original = (self.session / "extracted/screen-0001.json").read_bytes()
        self.record([{"text": "改写"}], code=1)
        self.assertEqual((self.session / "extracted/screen-0001.json").read_bytes(), original)
        self.session_command("finish")
        for name in ("observe", "capture", "finish"):
            self.session_command(name, code=1)
        self.assert_valid()

    def test_invalid_json_can_be_corrected_before_record_without_losing_image(self):
        self.session_command("capture")
        for raw in ('{"items":', '{"items":[1]}', '{"items":[{"text":3}]}'):
            self.result_file.write_text(raw)
            self.session_command("record", "--result-file", str(self.result_file), code=1)
        self.record([{"text": None, "title": None}, {"title": "仅标题"}])
        self.session_command("finish")
        index = json.loads((self.session / "index.json").read_text())
        self.assertEqual(index["totals"]["items_unique"], 1)
        self.assert_valid()

    def test_extraction_failure_is_preserved_and_later_commands_resume(self):
        self.session_command("capture")
        self.session_command("record", "--error", "模糊")
        self.session_command("capture")
        self.record()
        self.session_command("finish", "--reason", "interrupted")
        index = json.loads((self.session / "index.json").read_text())
        self.assertEqual(index["totals"]["extract_failures"], 1)
        self.assert_valid()

    def test_pending_failure_can_be_finalized_without_device(self):
        self.session_command("capture")
        self.device["get_current_app"].side_effect = RuntimeError("offline")
        self.session_command("finish", "--reason", "extraction_failed", "--error", "无法识别")
        self.assert_valid()

    def test_navigation_failure_keeps_observations_without_collected_screens(self):
        self.session_command("observe")
        self.session_command("finish", "--reason", "navigation_failed", "--error", "没有测试群")
        self.assertEqual(len(list((self.session / "navigation").glob("*.png"))), 1)
        self.assertEqual(len(list((self.session / "screenshots").glob("*.png"))), 0)
        self.assert_valid()

    def test_first_capture_failure_can_finish_as_navigation_failed(self):
        self.device["capture"].side_effect = caller.adb.ScreenshotError("设备断开")
        self.session_command("capture", code=1)
        self.session_command("finish", "--reason", "navigation_failed", "--error", "设备断开")
        self.assertEqual(len([e for e in self.events() if e["event"] == "navigation"]), 1)
        self.assert_valid()

    def test_screenshot_save_failure_does_not_record_navigation_success(self):
        with patch.object(caller.SessionStore, "save_screenshot", side_effect=OSError("磁盘满")):
            self.session_command("capture", code=1)
        self.assertFalse(any(e["event"] == "navigation" for e in self.events()))
        self.session_command("finish", "--reason", "navigation_failed", "--error", "磁盘满")
        self.assert_valid()

    def test_first_capture_can_be_retried_after_device_failure(self):
        self.device["capture"].side_effect = [caller.adb.ScreenshotError("暂时离线"),
                                             self.device["capture"].return_value]
        self.session_command("capture", code=1)
        self.assertFalse(any(e["event"] == "navigation" for e in self.events()))
        self.session_command("capture")
        self.record()
        self.session_command("finish")
        self.assertEqual(len([e for e in self.events() if e["event"] == "navigation"]), 1)
        self.assert_valid()

    def test_invalid_optional_fields_can_be_repaired_without_writing_evidence(self):
        self.session_command("capture")
        before = (self.session / "manifest.jsonl").read_bytes()
        invalid_items = [
            {"bbox": "left"}, {"bbox": [0, 1, 2]}, {"bbox": [0, False, 2, 3]},
            {"bbox": [0, 1, 1000, 3]}, {"bbox": [20, 10, 10, 20]},
            {"bbox": [0, 1, 2, float("nan")]}, {"bbox": [0, 1, 2, float("inf")]},
            {"confidence": "certain"}, {"confidence": []}, {"confidence": None},
            {"extra": []}, {"extra": "价格"}, {"extra": None},
        ]
        for invalid in invalid_items:
            with self.subTest(invalid=invalid):
                self.record([{**ITEM, **invalid}], code=1)
                self.assertEqual((self.session / "manifest.jsonl").read_bytes(), before)
                self.assertFalse((self.session / "extracted/screen-0001.json").exists())
        for summary in (None, [], {}):
            self.result_file.write_text(json.dumps({"screen_summary": summary, "items": [ITEM]}))
            self.session_command("record", "--result-file", str(self.result_file), code=1)
            self.assertEqual((self.session / "manifest.jsonl").read_bytes(), before)
        self.record()
        self.session_command("finish")
        self.assert_valid()

    def test_valid_optional_fields_are_preserved(self):
        self.session_command("capture")
        item = {**ITEM, "bbox": [0, 1.5, 999, 800], "confidence": "low",
                "extra": {"time_raw": "周四", "count": 3}}
        self.result_file.write_text(json.dumps({"screen_summary": "可见消息", "items": [
            item, {"text": "另一条", "bbox": None, "extra": {}, "confidence": "medium"},
        ]}))
        self.session_command("record", "--result-file", str(self.result_file))
        self.session_command("finish")
        index = json.loads((self.session / "index.json").read_text())
        stored = index["items"][0]
        self.assertEqual(stored["evidence"]["bbox"], item["bbox"])
        self.assertEqual(stored["confidence"], "low")
        self.assertEqual(stored["extra"], item["extra"])
        self.assertIsNone(index["items"][1]["evidence"]["bbox"])
        self.assert_valid()

    def test_action_limit_does_not_log_an_execution_error_or_attempt(self):
        self.session = Path(self.command("start", "--app", "generic", "--task", "当前屏",
                                         "--data-dir", str(self.root), "--max-actions", "1")
                            [1]["session"])
        self.action({"name": "Back"})
        before = self.events()
        self.action({"name": "Back"}, code=1)
        after = self.events()
        self.assertEqual(len([e for e in after if e["event"] == "action_attempt"]), 1)
        self.assertFalse(any(e["event"] == "action_error" for e in after))
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[-1]["event"], "action_rejected")
        self.assertEqual(after[-1]["code"], "max_actions")
        self.device["back"].assert_called_once()

    def test_app_aliases_select_the_same_extract_schema(self):
        for aliases, expected in [
            (("微信", "wechat", "WeChat"), "text|image|voice|video|link|sticker|system"),
            (("小红书", "xiaohongshu", "XIAOHONGSHU", "rednote"), "note|comment"),
            (("红果", "红果免费短剧", "hongguo"),
             "message|note|comment|product|profile|search_result|system|other"),
        ]:
            prompts = [caller.build_extract_prompt(app, "当前屏") for app in aliases]
            hints = [prompt.split("## 界面说明\n")[1].split("## 提取规则")[0] for prompt in prompts]
            self.assertTrue(all(hint == hints[0] for hint in hints))
            for prompt in prompts:
                self.assertIn(f'"type": "{expected}"', prompt)

    def test_invalid_finish_does_not_write_partial_failure_event(self):
        self.session_command("capture")
        previous = (self.session / "manifest.jsonl").read_bytes()
        self.session_command("finish", "--reason", "navigation_failed", "--error", "错误", code=1)
        self.assertEqual((self.session / "manifest.jsonl").read_bytes(), previous)
        self.assertFalse((self.session / "extracted/screen-0001.json").exists())

    def test_tap_to_real_send_button_is_denied(self):
        self.device["ui_texts_at_point"].return_value = ["发送"]
        verdict = self.action({"name": "Tap", "element": [500, 210],
                               "intent": "open_chat", "target_text": "按钮"}, code=2)
        self.assertEqual(verdict["code"], "deny_write_action")
        self.device["tap"].assert_not_called()

    def test_tap_without_control_tree_is_denied(self):
        self.device["ui_texts_at_point"].return_value = None
        verdict = self.action({"name": "Tap", "element": [500, 210]}, code=2)
        self.assertEqual(verdict["code"], "deny_ui_unavailable")
        self.device["tap"].assert_not_called()

    def test_invalid_coordinates_and_horizontal_swipe_never_reach_device(self):
        for value in ({"name": "Tap", "element": [True, 10]},
                      {"name": "Swipe", "start": [200, 500], "end": [800, 500]}):
            self.action(value, code=2)
        self.device["tap"].assert_not_called()
        self.device["swipe"].assert_not_called()

    def test_limits_count_failed_attempts_and_screens(self):
        self.session = Path(self.command("start", "--app", "generic", "--task", "当前屏",
                                         "--data-dir", str(self.root), "--max-actions", "1",
                                         "--max-screens", "1")[1]["session"])
        self.device["back"].side_effect = RuntimeError("设备离线")
        self.action({"name": "Back"}, code=1)
        self.action({"name": "Back"}, code=1)
        self.assertEqual(self.device["back"].call_count, 1)
        self.assertTrue(any(e["event"] == "action_error" for e in self.events()))
        self.session_command("capture")
        self.record()
        self.session_command("capture", code=1)
        self.session_command("finish", "--reason", "max_screens")
        self.assert_valid()

    def test_corrupt_screenshot_blocks_resume(self):
        self.session_command("capture")
        (self.session / "screenshots/screen-0001.png").write_bytes(b"changed")
        self.record(code=1)

    def test_corrupt_extraction_blocks_resume(self):
        self.session_command("capture")
        self.record()
        path = self.session / "extracted/screen-0001.json"
        payload = json.loads(path.read_text())
        payload["items"][0]["text"] = "改写"
        path.write_text(json.dumps(payload))
        self.session_command("capture", code=1)

    def test_orphan_evidence_after_crash_is_not_overwritten(self):
        for directory, filename in (("screenshots", "screen-0001.png"),
                                    ("extracted", "screen-0001.json")):
            with self.subTest(directory=directory):
                orphan = self.session / directory / filename
                orphan.write_bytes(b"unregistered-evidence")
                self.session_command("capture", code=1)
                self.assertEqual(orphan.read_bytes(), b"unregistered-evidence")
                orphan.unlink()

    def test_import_and_cli_work_when_sdk_import_is_forbidden(self):
        # 新进程没有已缓存的 SDK；全流程测试也在 CI 的基础依赖环境执行。
        script = (
            "import builtins, runpy, sys\n"
            "original = builtins.__import__\n"
            "def deny_sdk(name, *args, **kwargs):\n"
            "    if name == 'openai' or name.startswith('openai.'):\n"
            "        raise AssertionError('default workflow imported model SDK')\n"
            "    return original(name, *args, **kwargs)\n"
            "builtins.__import__ = deny_sdk\n"
            f"sys.argv = [{str(SCRIPTS / 'caller_collect.py')!r}, '--help']\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        env = {key: value for key, value in os.environ.items()
               if not key.endswith("API_KEY")}
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("observe", result.stdout)

    def test_capture_prompt_calendar_and_types_match_wechat(self):
        with patch("collector.agent.prompts.datetime") as calendar:
            calendar.today.return_value = datetime(2026, 10, 1, 12)
            result = self.session_command("capture")
        prompt = result["prompt"]
        self.assertIn("周四=2026年10月01日", prompt)
        self.assertIn("周五=2026年09月25日", prompt)
        self.assertIn('"type": "text|image|voice|video|link|sticker|system"', prompt)
        self.assertIn("不能唯一确定", prompt)


if __name__ == "__main__":
    unittest.main()
