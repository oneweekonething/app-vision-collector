"""SessionStore：一个采集会话的全部证据，落盘为结构化目录。

目录布局（规范详见 docs/data-layout.md）:

    <data_dir>/<日期>/<时间戳>_<app>_<slug>/
    ├── session.json       会话元数据
    ├── screenshots/       原始截屏（证据本体，只增不改）
    ├── extracted/         每屏提取的原始模型输出
    ├── manifest.jsonl     追加式事件台账
    └── index.json         去重后的条目目录（每条含 evidence 溯源指针）

溯源不变量：
1. 先存截屏、后做提取——任何进入 index 的信息必然对应一张已留存截图；
2. manifest.jsonl 只追加、不修改，是事件流水的事实来源（每条追加后 fsync）；
3. index.json 由 finalize() 一次性写出，是给人和程序读的汇总视图；
4. 所有落盘走 tmp + fsync + 原子 rename，进程中断不会留下半个文件。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collector import PROMPT_VERSION, __version__
from collector.adb.screenshot import Screenshot
from collector.store.dedup import Deduplicator


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _slugify(text: str, max_len: int = 40) -> str:
    """把目标名/任务名转成文件名安全的短 slug。"""
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text.strip())
    return (text[:max_len].strip("-")) or "task"


class SessionStore:
    """一个采集会话的证据容器。"""

    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.screenshots_dir = session_dir / "screenshots"
        self.extracted_dir = session_dir / "extracted"
        self.manifest_path = session_dir / "manifest.jsonl"

        self.screen_count = 0
        self.items: list[dict[str, Any]] = []       # 去重后的条目目录
        self.dedup = Deduplicator()                 # 跨屏去重（聊天类滑窗策略）
        self._item_seq = 0
        self._navigation: dict[str, Any] | None = None
        self.extracted_total = 0
        self.duplicates_total = 0
        self.extract_failures_total = 0
        self._finished = False

    # ---------------------------------------------------------------- create

    @classmethod
    def create(
        cls,
        data_dir: Path,
        app: str,
        target: str,
        task: str,
        device_id: str,
        vlm_model: str,
        nav_model: str,
    ) -> SessionStore:
        """创建新会话目录并写入元数据与首条台账事件。"""
        started = datetime.now()
        session_id = started.strftime("%Y%m%d-%H%M%S")
        slug = _slugify(target or task)
        session_dir = data_dir / started.strftime("%Y-%m-%d") / f"{session_id}_{app}_{slug}"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "screenshots").mkdir(exist_ok=True)
        (session_dir / "extracted").mkdir(exist_ok=True)

        store = cls(session_dir)
        metadata = {
            "session_id": f"{session_id}_{app}_{slug}",
            "collector_version": __version__,
            "prompt_version": PROMPT_VERSION,
            "app": app,
            "target": target,
            "task": task,
            "device_id": device_id,
            "vlm_model": vlm_model,
            "nav_model": nav_model,
            "started_at": started.astimezone().isoformat(timespec="seconds"),
        }
        store._session_metadata = metadata
        _write_json(session_dir / "session.json", metadata)
        store._manifest({"event": "session_started", **metadata})
        return store

    # ------------------------------------------------------------ screenshots

    def save_screenshot(self, screenshot: Screenshot) -> dict[str, Any]:
        """保存一屏截图，返回该屏的台账记录。"""
        self.screen_count += 1
        filename = f"screen-{self.screen_count:04d}.png"
        path = self.screenshots_dir / filename
        _atomic_write_bytes(path, screenshot.png_bytes)

        record = {
            "event": "screenshot",
            "screen": self.screen_count,
            "path": f"screenshots/{filename}",
            "sha256": screenshot.sha256,
            "bytes": len(screenshot.png_bytes),
            "width": screenshot.width,
            "height": screenshot.height,
            "captured_at": screenshot.captured_at,
        }
        # 截图写盘后重算哈希，确保台账指向的摘要与磁盘内容一致
        record["sha256"] = _file_sha256(path)
        self._manifest(record)
        return record

    # ------------------------------------------------------------- extraction

    def save_extraction(
        self,
        screen: int,
        screenshot_record: dict[str, Any],
        extraction: dict[str, Any],
    ) -> dict[str, Any]:
        """登记一屏的提取结果：留存原始输出 + 去重 + 生成溯源条目。

        Returns:
            {"extracted": 模型给出条数, "new": 新条数, "duplicates": 重复条数}
        """
        raw = extraction.pop("raw_response", "")
        items = extraction.get("items", [])

        payload = {
            "screen": screen,
            "model": self._session_metadata["vlm_model"],
            "prompt_version": PROMPT_VERSION,
            "extracted_at": _now_iso(),
            "screen_summary": extraction.get("screen_summary", ""),
            "items": items,
            "raw_response": raw,
        }
        if extraction.get("repair_response"):
            payload["repair_response"] = extraction["repair_response"]
        _write_json(self.extracted_dir / f"screen-{screen:04d}.json", payload)

        new_ids: list[str] = []
        for item in items:
            if not self.dedup.admit(screen, item):
                continue

            self._item_seq += 1
            item_id = f"itm_{self._item_seq:06d}"
            entry = {
                "item_id": item_id,
                "app": self._session_metadata["app"],
                "type": item.get("type", "other"),
                "title": item.get("title"),
                "sender": item.get("sender"),
                "text": item.get("text", ""),
                "time_hint": item.get("time_hint"),
                "extra": item.get("extra") or {},
                "confidence": item.get("confidence", "medium"),
                "evidence": {
                    "screenshot": screenshot_record["path"],
                    "sha256": screenshot_record["sha256"],
                    "captured_at": screenshot_record["captured_at"],
                    "screen_index": screen,
                    "bbox": item.get("bbox"),
                    "model": self._session_metadata["vlm_model"],
                    "prompt_version": PROMPT_VERSION,
                },
            }
            self.items.append(entry)
            new_ids.append(item_id)

        extracted = len(items)
        new = len(new_ids)
        self.extracted_total += extracted
        self.duplicates_total += extracted - new

        self._manifest({
            "event": "items",
            "screen": screen,
            "screenshot_sha256": screenshot_record["sha256"],
            "extracted": extracted,
            "new": new,
            "duplicates": extracted - new,
            "item_ids": new_ids,
        })
        return {"extracted": extracted, "new": new, "duplicates": extracted - new}

    def save_extraction_failure(
        self,
        screen: int,
        screenshot_record: dict[str, Any],
        error: Exception | str,
        raw_response: str = "",
    ) -> None:
        """登记一屏提取失败：extracted/ 留失败记录，保证每屏都有提取文件。

        失败屏的截图已在 save_screenshot 落盘，证据链不断——只是该屏
        没有可入库的条目。校验器要求每屏都有提取文件，失败屏也不能缺。
        raw_response 存最后一次模型原始输出（可能是不完整/非法 JSON），
        供审计回查模型当时到底输出了什么。
        """
        message = str(error)
        self.extract_failures_total += 1
        _write_json(self.extracted_dir / f"screen-{screen:04d}.json", {
            "screen": screen,
            "model": self._session_metadata["vlm_model"],
            "prompt_version": PROMPT_VERSION,
            "extracted_at": _now_iso(),
            "screen_summary": "",
            "items": [],
            "raw_response": raw_response,
            "error": message,
        })
        self._manifest({
            "event": "extraction_failed",
            "screen": screen,
            "screenshot_sha256": screenshot_record["sha256"],
            "error": message,
        })

    # -------------------------------------------------------------- navigation

    def record_navigation(self, success: bool, reason: str, message: str = "",
                          steps: int = 0, current_app: str = "") -> None:
        """登记导航结果：入台账，finalize 时并入 index.json 顶层 navigation 字段。"""
        self._navigation = {
            "success": success,
            "reason": reason,
            "message": message,
            "steps": steps,
            "current_app": current_app,
        }
        self._manifest({"event": "navigation", **self._navigation})

    # ----------------------------------------------------------------- finish

    def finalize(self, stop_reason: str, error: str | None = None) -> Path:
        """写出 index.json 并记录收尾事件。"""
        if self._finished:
            raise RuntimeError("会话已收尾，不能重复 finalize")
        self._finished = True

        index = {
            **self._session_metadata,
            "finished_at": _now_iso(),
            "stop_reason": stop_reason,
            "error": error,
            "totals": {
                "screens": self.screen_count,
                "items_extracted": self.extracted_total,
                "items_unique": len(self.items),
                "duplicates": self.duplicates_total,
                "extract_failures": self.extract_failures_total,
            },
            "items": self.items,
        }
        if self._navigation is not None:
            index["navigation"] = self._navigation
        path = self.session_dir / "index.json"
        _write_json(path, index)
        self._manifest({
            "event": "session_finished",
            "stop_reason": stop_reason,
            "error": error,
            **index["totals"],
        })
        return path

    # ---------------------------------------------------------------- internal

    def _manifest(self, event: dict[str, Any]) -> None:
        event = {"ts": _now_iso(), **event}
        # 追加 + fsync：进程被杀后已确认的事件不会只停留在缓冲区
        with self.manifest_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


# -------------------------------------------------------------------- helpers

def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    """fsync 目录句柄，让 rename 后的目录项真正落盘（尽力而为）。"""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """tmp 文件 + fsync + 原子 rename，避免中断留下半个文件。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_bytes(
        path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
