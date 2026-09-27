"""SessionStore 单元测试：无需手机与 API Key。

运行: python3 -m unittest discover tests -v
"""

from __future__ import annotations

import hashlib
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

FAKE_PNG = b"\x89PNG-fake-evidence-bytes-" + b"x" * 2048


def make_screenshot() -> Screenshot:
    return Screenshot(
        png_bytes=FAKE_PNG,
        width=540,
        height=1000,
        captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        sha256=hashlib.sha256(FAKE_PNG).hexdigest(),
    )


ITEM_A = {"type": "message", "sender": "张三", "text": "今晚八点开会",
          "time_hint": "2026年09月27日 19:00", "bbox": [10, 20, 30, 40],
          "confidence": "high", "title": None, "extra": {}}
ITEM_B = {"type": "message", "sender": "李四", "text": "收到",
          "time_hint": "2026年09月27日 19:01", "bbox": None,
          "confidence": "high", "title": None, "extra": {}}


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.store = SessionStore.create(
            data_dir=self.data_dir, app="wechat", target="测试群",
            task="单测任务", device_id="TEST123",
            vlm_model="test-vlm", nav_model="test-nav",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_builds_structure(self):
        d = self.store.session_dir
        self.assertTrue((d / "screenshots").is_dir())
        self.assertTrue((d / "extracted").is_dir())
        self.assertTrue((d / "session.json").is_file())
        self.assertTrue((d / "manifest.jsonl").is_file())
        meta = json.loads((d / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["app"], "wechat")
        self.assertEqual(meta["device_id"], "TEST123")

    def test_screenshot_saved_with_matching_hash(self):
        record = self.store.save_screenshot(make_screenshot())
        path = self.store.session_dir / record["path"]
        self.assertTrue(path.is_file())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])
        self.assertEqual(record["screen"], 1)

    def test_extraction_dedup_and_provenance(self):
        record = self.store.save_screenshot(make_screenshot())
        self.store.save_extraction(1, record, {"items": [ITEM_A, ITEM_B]})

        # 同一屏重复提取 → 全部去重
        stats = self.store.save_extraction(1, record, {"items": [ITEM_A, ITEM_B]})
        self.assertEqual(stats, {"extracted": 2, "new": 0, "duplicates": 2})
        self.assertEqual(len(self.store.items), 2)

        item = self.store.items[0]
        self.assertEqual(item["item_id"], "itm_000001")
        self.assertEqual(item["evidence"]["sha256"], record["sha256"])
        self.assertEqual(item["evidence"]["screen_index"], 1)
        self.assertTrue((self.store.session_dir / record["path"]).is_file())

    def test_cross_screen_dedup(self):
        record1 = self.store.save_screenshot(make_screenshot())
        self.store.save_extraction(1, record1, {"items": [ITEM_A]})
        record2 = self.store.save_screenshot(make_screenshot())
        stats = self.store.save_extraction(2, record2, {"items": [ITEM_A, dict(ITEM_B)]})
        self.assertEqual(stats["new"], 1)  # 只有 ITEM_B 是新的
        self.assertEqual(len(self.store.items), 2)

    def test_finalize_writes_consistent_index(self):
        record = self.store.save_screenshot(make_screenshot())
        self.store.save_extraction(1, record, {"items": [ITEM_A, ITEM_B]})
        path = self.store.finalize("no_new_items")

        index = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(index["totals"]["screens"], 1)
        self.assertEqual(index["totals"]["items_unique"], 2)
        self.assertEqual(index["totals"]["items_extracted"], 2)
        self.assertEqual(index["stop_reason"], "no_new_items")
        self.assertEqual(len(index["items"]), 2)

        events = [
            json.loads(line)
            for line in (self.store.session_dir / "manifest.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[0], "session_started")
        self.assertIn("screenshot", kinds)
        self.assertIn("items", kinds)
        self.assertEqual(kinds[-1], "session_finished")

    def test_finalize_twice_rejected(self):
        self.store.finalize("completed")
        with self.assertRaises(RuntimeError):
            self.store.finalize("completed")

    def test_extraction_failure_keeps_session_verifiable(self):
        # 失败屏：截图照存，提取留失败记录（含最后一次模型输出），会话仍可整体通过校验
        record = self.store.save_screenshot(make_screenshot())
        self.store.save_extraction_failure(1, record, RuntimeError("JSON 解析失败"),
                                           raw_response='{"items": [{"tex')
        payload = json.loads(
            (self.store.session_dir / "extracted" / "screen-0001.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["items"], [])
        self.assertIn("JSON 解析失败", payload["error"])
        self.assertEqual(payload["raw_response"], '{"items": [{"tex')

        self.store.save_screenshot(make_screenshot())
        self.store.save_extraction(2, {"path": "screenshots/screen-0002.png",
                                       "sha256": record["sha256"],
                                       "captured_at": record["captured_at"]},
                                   {"items": [dict(ITEM_A)]})
        index = json.loads(self.store.finalize("completed").read_text(encoding="utf-8"))
        self.assertEqual(index["totals"]["extract_failures"], 1)
        self.assertEqual(index["totals"]["items_unique"], 1)

        ok, report = verify_session(self.store.session_dir)  # 失败屏不破坏每屏提取文件校验
        self.assertTrue(ok, "\n".join(report))


if __name__ == "__main__":
    unittest.main()
