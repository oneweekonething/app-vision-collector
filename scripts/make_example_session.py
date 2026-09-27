#!/usr/bin/env python3
"""生成一个离线示例采集会话（无需手机与 API Key）。

用 Pillow 画出两张"假微信聊天截图"，再走一遍 SessionStore 的
保存/提取/收尾流程，产出与真实采集完全同构的 examples/demo-session/。
用途：
1. 让使用者在接设备前直观了解目录结构与溯源字段；
2. 作为 inspect_session.py 的现成校验对象；
3. 充当 SessionStore 的端到端冒烟测试。

用法: python3 scripts/make_example_session.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector.adb.screenshot import Screenshot  # noqa: E402
from collector.store import SessionStore  # noqa: E402

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent / "examples"


def draw_chat_screenshot(messages: list[tuple[str, str, bool]], seed: int) -> bytes:
    """画一张简化版聊天界面截图（白/绿气泡）。"""
    from PIL import Image, ImageDraw

    width, height = 540, 1000
    image = Image.new("RGB", (width, height), color="#EDEDED")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 70], fill="#EDEDED")
    draw.text((width / 2 - 60, 24), "AI hobby club (demo)", fill="black")

    y = 110
    for sender, text, is_self in messages:
        bubble_width = min(width - 160, 12 * len(text) + 30)
        if is_self:
            x0, fill = width - 20 - bubble_width, "#95EC69"
        else:
            x0, fill = 20, "#FFFFFF"
        draw.text((x0, y - 14), sender, fill="#888888")
        draw.rounded_rectangle(
            [x0, y, x0 + bubble_width, y + 46], radius=8, fill=fill
        )
        draw.text((x0 + 12, y + 14), text, fill="black")
        y += 110

    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


FAKE_SCREENS = [
    {
        "summary": "群聊消息 3 条（demo 数据）",
        "messages": [
            ("Wang", "hi, demo msg 1", False),
            ("Me", "hello back", True),
            ("Li Lei", "see you at 8pm", False),
        ],
        "items": [
            {"type": "message", "sender": "王小明", "text": "hi, demo msg 1",
             "time_hint": "2026年09月27日 20:40", "bbox": [20, 110, 300, 160],
             "confidence": "high", "title": None, "extra": {}},
            {"type": "message", "sender": "我", "text": "hello back",
             "time_hint": "2026年09月27日 20:41", "bbox": [240, 220, 520, 270],
             "confidence": "high", "title": None, "extra": {}},
            {"type": "message", "sender": "李雷", "text": "see you at 8pm",
             "time_hint": "2026年09月27日 20:42", "bbox": [20, 330, 320, 380],
             "confidence": "high", "title": None, "extra": {}},
        ],
    },
    {
        "summary": "上滑后的历史消息 2 条（其中 1 条与上一屏重复，演示去重）",
        "messages": [
            ("Li Lei", "see you at 8pm", False),
            ("Han Meimei", "bring the demo chart", False),
        ],
        "items": [
            {"type": "message", "sender": "李雷", "text": "see you at 8pm",
             "time_hint": "2026年09月27日 20:42", "bbox": [20, 110, 320, 160],
             "confidence": "high", "title": None, "extra": {}},
            {"type": "message", "sender": "韩梅梅", "text": "bring the demo chart",
             "time_hint": "2026年09月27日 20:15", "bbox": [20, 220, 360, 270],
             "confidence": "medium", "title": None, "extra": {}},
        ],
    },
]


def main() -> int:
    import shutil

    store = SessionStore.create(
        data_dir=EXAMPLE_ROOT / ".build",
        app="wechat",
        target="demo-session",
        task="离线示例：展示目录结构与溯源字段（非真实采集）",
        device_id="demo-device",
        vlm_model="demo-vlm",
        nav_model="demo-nav",
    )
    print(f"[示例会话] {store.session_dir}")

    for index, screen in enumerate(FAKE_SCREENS, 1):
        png = draw_chat_screenshot(screen["messages"], seed=index)
        screenshot = Screenshot(
            png_bytes=png,
            width=540,
            height=1000,
            captured_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            sha256="pending",
        )
        record = store.save_screenshot(screenshot)

        extraction = {"screen_summary": screen["summary"], "items": screen["items"]}
        stats = store.save_extraction(record["screen"], record, extraction)
        print(f"[屏 {index}] 新增 {stats['new']} / 重复 {stats['duplicates']}")

    index_path = store.finalize("no_new_items")

    # 把日期桶目录归位到固定的 examples/demo-session，方便引用与校验
    final_dir = EXAMPLE_ROOT / "demo-session"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(store.session_dir), str(final_dir))
    shutil.rmtree(EXAMPLE_ROOT / ".build")

    print(f"[完成] index: {final_dir / 'index.json'}")
    print(f"[校验] python3 scripts/inspect_session.py {final_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
