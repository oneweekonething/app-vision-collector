"""Deduplicator 滑窗去重 + 感知哈希单元测试。"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from collector.imaging import average_hash, hamming_distance  # noqa: E402
from collector.store.dedup import Deduplicator, fingerprint  # noqa: E402


def message(text="收到", sender="张三", time=None, **extra):
    item = {"type": "message", "sender": sender, "text": text, "title": None}
    if time:
        item["time_hint"] = time
    item.update(extra)
    return item


class FingerprintTest(unittest.TestCase):
    def test_time_hint_distinguishes_repeats(self):
        a = message(time="2026年09月27日 19:00")
        b = message(time="2026年09月27日 20:30")
        self.assertNotEqual(fingerprint(a), fingerprint(b))

    def test_no_time_falls_back_to_content(self):
        self.assertEqual(fingerprint(message()), fingerprint(message()))

    def test_non_chat_ignores_time(self):
        a = {"type": "note", "title": "同一篇笔记", "text": "", "time_hint": "x"}
        b = {"type": "note", "title": "同一篇笔记", "text": "", "time_hint": "y"}
        self.assertEqual(fingerprint(a), fingerprint(b))


class DeduplicatorWindowTest(unittest.TestCase):
    def setUp(self):
        self.dedup = Deduplicator(window=3)

    def test_same_screen_duplicate_dropped(self):
        item = message()
        self.assertTrue(self.dedup.admit(1, item))
        self.assertFalse(self.dedup.admit(1, dict(item)))  # 提取器重复输出

    def test_overlap_in_window_dropped(self):
        item = message()
        self.assertTrue(self.dedup.admit(1, item))
        for screen in (2, 3, 4):  # 60% 重叠窗口内重采
            self.assertFalse(self.dedup.admit(screen, dict(item)), screen)

    def test_real_repeat_beyond_window_kept(self):
        item = message()  # 张三隔了很久又发了一遍"收到"，且无可见时间戳
        self.assertTrue(self.dedup.admit(1, item))
        for screen in (2, 3, 4):
            self.dedup.admit(screen, dict(item))
        self.assertTrue(self.dedup.admit(5, dict(item)))  # 窗口外 → 真实重复

    def test_repeat_with_time_always_kept(self):
        first = message(time="2026年09月27日 19:00")
        second = message(time="2026年09月27日 20:30")
        self.assertTrue(self.dedup.admit(1, first))
        self.assertTrue(self.dedup.admit(2, second))  # 相邻屏不同时间 → 不误杀

    def test_non_chat_global_dedup(self):
        note = {"type": "note", "title": "手机摄影技巧", "text": "摘要"}
        self.assertTrue(self.dedup.admit(1, dict(note)))
        self.assertFalse(self.dedup.admit(5, dict(note)))  # 窗口外仍是重复曝光

    def test_failed_screen_gap_keeps_window_working(self):
        # 提取失败的屏不产生指纹；窗口按"有收录的屏"滑动
        item = message()
        self.assertTrue(self.dedup.admit(1, item))
        self.assertFalse(self.dedup.admit(3, dict(item)))  # 屏 2 失败被跳过


def png_bytes(upper: int, lower: int) -> bytes:
    from PIL import Image

    img = Image.new("L", (64, 64))
    for y in range(64):
        value = upper if y < 32 else lower
        for x in range(64):
            img.putpixel((x, y), value)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


class PerceptualHashTest(unittest.TestCase):
    def test_identical_images_zero_distance(self):
        data = png_bytes(255, 0)
        self.assertEqual(hamming_distance(average_hash(data), average_hash(data)), 0)

    def test_different_images_far_apart(self):
        solid = png_bytes(128, 128)
        split = png_bytes(255, 0)
        self.assertGreater(hamming_distance(average_hash(solid), average_hash(split)), 10)

    def test_corrupt_image_returns_none(self):
        self.assertIsNone(average_hash(b"not a png"))


if __name__ == "__main__":
    unittest.main()
