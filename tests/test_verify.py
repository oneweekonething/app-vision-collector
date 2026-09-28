"""inspect_session.verify_session 的测试：用临时 session 验证校验逻辑。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.adb.screenshot import Screenshot  # noqa: E402
from collector.store import SessionStore  # noqa: E402
from inspect_session import verify_session  # noqa: E402

FAKE_PNG = b"\x89PNG-verify-bytes-" + b"y" * 1024

ITEM = {"type": "message", "sender": "张三", "text": "hello",
        "time_hint": "2026年09月27日 10:00", "bbox": None,
        "confidence": "high", "title": None, "extra": {}}


def build_session(root: Path) -> Path:
    store = SessionStore.create(
        data_dir=root, app="wechat", target="verify", task="校验测试",
        device_id="TEST", vlm_model="test-vlm", nav_model="test-nav",
    )
    screenshot = Screenshot(
        png_bytes=FAKE_PNG, width=100, height=200,
        captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        sha256="pending",
    )
    record = store.save_screenshot(screenshot)
    store.save_extraction(1, record, {"items": [ITEM]})
    store.finalize("completed")
    return store.session_dir


def build_empty_session(root: Path) -> Path:
    """模拟中断的会话：只创建、立即收尾，无任何截屏。"""
    store = SessionStore.create(
        data_dir=root, app="wechat", target="empty", task="空会话",
        device_id="TEST", vlm_model="test-vlm", nav_model="test-nav",
    )
    store.finalize("error", error="运行中断")
    return store.session_dir


def build_navigation_failed_session(root: Path, with_screens: bool = False,
                                    nav_success: bool = False) -> Path:
    """构造导航失败形态的会话（可选混入截图/成功标记制造反例）。"""
    store = SessionStore.create(
        data_dir=root, app="wechat", target="navfail", task="导航失败会话",
        device_id="TEST", vlm_model="test-vlm", nav_model="test-nav",
    )
    store.record_navigation(nav_success, "max_steps" if not nav_success else "finished",
                            "达到最大导航步数", steps=20)
    if with_screens:
        screenshot = Screenshot(
            png_bytes=FAKE_PNG, width=100, height=200,
            captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            sha256="pending",
        )
        record = store.save_screenshot(screenshot)
        store.save_extraction_failure(1, record, RuntimeError("不应发生"))
    store.finalize("navigation_failed", error="导航未完成（max_steps）")
    return store.session_dir


def build_navigated_session(root: Path) -> Path:
    """正常完成且带导航记录的会话（用于台账/index 交叉一致性测试）。"""
    store = SessionStore.create(
        data_dir=root, app="wechat", target="navok", task="导航成功会话",
        device_id="TEST", vlm_model="test-vlm", nav_model="test-nav",
    )
    store.record_navigation(True, "finished", "已进入群聊；目标页核验通过",
                            steps=3, current_app="com.tencent.mm/.ui.LauncherUI")
    screenshot = Screenshot(
        png_bytes=FAKE_PNG, width=100, height=200,
        captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        sha256="pending",
    )
    record = store.save_screenshot(screenshot)
    store.save_extraction(1, record, {"items": [ITEM]})
    store.finalize("no_new_items")
    return store.session_dir


class VerifySessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session_dir = build_session(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_session_passes(self):
        ok, report = verify_session(self.session_dir)
        self.assertTrue(ok, "\n".join(report))

    def test_tampered_screenshot_fails(self):
        target = next((self.session_dir / "screenshots").glob("*.png"))
        target.write_bytes(b"tampered")
        ok, report = verify_session(self.session_dir)
        self.assertFalse(ok)
        self.assertTrue(any("哈希不一致" in line for line in report))

    def test_missing_screenshot_fails(self):
        target = next((self.session_dir / "screenshots").glob("*.png"))
        target.unlink()
        ok, report = verify_session(self.session_dir)
        self.assertFalse(ok)
        self.assertTrue(any("截图缺失" in line for line in report))

    def test_totals_mismatch_fails(self):
        index_path = self.session_dir / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["totals"]["items_unique"] = 99
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        ok, report = verify_session(self.session_dir)
        self.assertFalse(ok)
        self.assertTrue(any("不符" in line for line in report))

    def test_empty_session_fails(self):
        """0 截屏的会话（运行中断产物）不允许 vacuous 通过。"""
        empty = build_empty_session(Path(self.tmp.name))
        ok, report = verify_session(empty)
        self.assertFalse(ok)
        self.assertTrue(any("没有任何截图" in line for line in report))

    def test_navigation_failed_zero_screen_is_valid(self):
        """导航失败 → 不采集 → 0 截图是预期结果，校验必须通过。"""
        session = build_navigation_failed_session(Path(self.tmp.name))
        ok, report = verify_session(session)
        self.assertTrue(ok, "\n".join(report))
        index = json.loads((session / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["totals"]["screens"], 0)
        self.assertFalse(index["navigation"]["success"])

    def test_navigation_failed_with_screenshots_is_invalid(self):
        """navigation_failed 会话不应产生采集截图（不变量反例）。"""
        session = build_navigation_failed_session(Path(self.tmp.name), with_screens=True)
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("不应产生采集截图" in line for line in report))

    def test_navigation_failed_with_success_flag_is_invalid(self):
        """stop_reason=navigation_failed 但 navigation.success 不是 false。"""
        session = build_navigation_failed_session(Path(self.tmp.name), nav_success=True)
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("success 不是 false" in line for line in report))

    def test_corrupt_index_json_reports_problem_not_crash(self):
        """index.json 损坏 → 正常给出 ✗ 报告，而不是抛异常。"""
        session = build_session(Path(self.tmp.name))
        (session / "index.json").write_text('{"items": [ 余额损坏', encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("不是合法 JSON" in line for line in report))

    def test_non_object_index_reports_problem(self):
        session = build_session(Path(self.tmp.name))
        (session / "index.json").write_text("[]", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("顶层不是 JSON 对象" in line for line in report))

    def test_navigation_fields_cross_checked_with_manifest(self):
        """manifest navigation 事件与 index.navigation 逐字段一致（跨文件不变量）。"""
        session = build_navigated_session(Path(self.tmp.name))
        ok, report = verify_session(session)
        self.assertTrue(ok, "\n".join(report))  # 一致时通过

        index_path = session / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["navigation"]["steps"] = 99  # 只改 index，台账仍是 3
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("navigation.steps" in line and "不一致" in line
                            for line in report))

    def test_navigation_in_index_without_manifest_event_is_invalid(self):
        session = build_navigated_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        lines = [ln for ln in manifest.read_text(encoding="utf-8").splitlines()
                 if '"event": "navigation"' not in ln]
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("缺少 navigation 事件" in line for line in report))

    def test_malformed_manifest_line_reports_not_crash(self):
        """manifest 某行是合法 JSON 但不是对象（如 []）→ 报告问题而非 AttributeError。"""
        session = build_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        lines.insert(1, "[]")
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("不是 JSON 对象" in line for line in report))

    def test_screenshot_event_missing_path_reports_not_crash(self):
        session = build_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        fixed = []
        for ln in manifest.read_text(encoding="utf-8").splitlines():
            ev = json.loads(ln)
            if ev.get("event") == "screenshot":
                del ev["path"]
            fixed.append(json.dumps(ev, ensure_ascii=False))
        manifest.write_text("\n".join(fixed) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("缺少 path" in line for line in report))

    def test_items_not_a_list_reports_not_crash(self):
        session = build_session(Path(self.tmp.name))
        index_path = session / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["items"] = {"broken": True}
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("index.items 不是数组" in line for line in report))

    def test_item_without_evidence_object_reports_not_crash(self):
        session = build_session(Path(self.tmp.name))
        index_path = session / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["items"][0]["evidence"] = "tampered"
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("缺少 evidence 对象" in line for line in report))

    def test_missing_session_finished_is_invalid(self):
        """index 已写但终态事件缺失（crash window）→ 必须判问题。"""
        session = build_navigated_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        lines = [ln for ln in manifest.read_text(encoding="utf-8").splitlines()
                 if '"event": "session_finished"' not in ln]
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("session_finished" in line for line in report))

    def test_finished_not_last_event_is_invalid(self):
        session = build_navigated_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps({"ts": "2026-09-28T14:00:00+08:00",
                                 "event": "items", "screen": 99}, ensure_ascii=False))
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("最后一条台账事件" in line for line in report))

    def test_finished_totals_mismatch_with_index_is_invalid(self):
        session = build_navigated_session(Path(self.tmp.name))
        manifest = session / "manifest.jsonl"
        lines = manifest.read_text(encoding="utf-8").splitlines()
        fixed = []
        for ln in lines:
            ev = json.loads(ln)
            if ev.get("event") == "session_finished":
                ev["screens"] = 999
            fixed.append(json.dumps(ev, ensure_ascii=False))
        manifest.write_text("\n".join(fixed) + "\n", encoding="utf-8")
        ok, report = verify_session(session)
        self.assertFalse(ok)
        self.assertTrue(any("session_finished.screens" in line for line in report))


if __name__ == "__main__":
    unittest.main()
