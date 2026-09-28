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


if __name__ == "__main__":
    unittest.main()
