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


if __name__ == "__main__":
    unittest.main()
