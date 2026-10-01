#!/usr/bin/env python3
"""调用者视觉驱动的采集入口。无模型 SDK、网络请求或 API Key。

调用者读取 observe/capture 返回的图片，用自身视觉能力决定动作、生成提取
JSON；脚本只负责设备操作、护栏、证据存储与去重。一次只运行一个命令。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector import adb
from collector.agent.extractor import _parse_json
from collector.agent.prompts import build_extract_prompt
from collector.agent.safety import ActionGuard, GuardVerdict
from collector.agent.step_agent import _denormalize
from collector.config import CHAT_DEDUP_APPS
from collector.store import SessionStore
from collector.store.session_store import _atomic_write_bytes


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="检查 ADB、设备和本地依赖；视觉能力由调用者确认")
    start = commands.add_parser("start", help="创建调用者驱动的会话")
    start.add_argument("--app", required=True)
    start.add_argument("--target", default="")
    start.add_argument("--task", required=True)
    start.add_argument("--model", default="caller-vision",
                       help="调用者已知的模型标识；默认仅标记为 caller-vision，不猜测型号")
    start.add_argument("--device-id")
    start.add_argument("--data-dir", type=Path, default=Path("collections"))
    start.add_argument("--dedup", choices=["auto", "chat", "global"], default="auto")
    start.add_argument("--max-screens", type=_positive, default=20)
    start.add_argument("--max-actions", type=_positive, default=40)
    for name in ("observe", "action", "capture", "record", "finish"):
        command = commands.add_parser(name)
        command.add_argument("session", type=Path)
        if name == "action":
            command.add_argument("--action-file", type=Path, required=True)
        elif name == "record":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--result-file", type=Path)
            source.add_argument("--error", help="无法提取时记录失败，保留截图")
        elif name == "finish":
            command.add_argument("--reason", default="completed", choices=[
                "completed", "no_new_items", "max_screens", "interrupted", "error",
                "navigation_failed", "extraction_failed",
            ])
            command.add_argument("--error")
    return parser


def _events(store: SessionStore) -> list[dict]:
    return [json.loads(line) for line in store.manifest_path.read_text("utf-8").splitlines()
            if line.strip()]


def _pending(store: SessionStore) -> dict | None:
    events = _events(store)
    recorded = {event["screen"] for event in events
                if event["event"] in {"items", "extraction_failed"}}
    return next((event for event in events if event["event"] == "screenshot"
                 and event["screen"] not in recorded), None)


def execute_action(store: SessionStore, action: dict) -> dict:
    """执行调用者看图后提出的动作，复用原有护栏，不调用导航模型。"""
    metadata = store._session_metadata
    device = metadata["device_id"]
    steps = sum(event["event"] == "action_attempt" for event in _events(store))
    if steps >= metadata["max_actions"]:
        raise ValueError("已达到 max-actions，请收尾会话")
    action = {**action, "kind": "do"}
    store._manifest({"event": "action_attempt", "action": action})
    name = action.get("name")
    guard = ActionGuard()
    verdict = guard.check(action)
    pixel = None
    if verdict.allowed and name == "Tap":
        pixel = _denormalize(action["element"], device)
        texts = adb.ui_texts_at_point(*pixel, device)
        if texts is None:
            verdict = GuardVerdict(False, "deny_ui_unavailable",
                                   "控件树不可用，不能独立核验 Tap；可用 Back 或手动定位后重新 observe")
        else:
            verdict = guard.check(action, ui_texts=texts)
    if not verdict.allowed:
        store._manifest({"event": "action", "action": action, "allowed": False,
                         "detail": verdict.detail})
        return {"allowed": False, "code": verdict.code, "detail": verdict.detail}
    if name == "Tap":
        adb.tap(*pixel, device)
    elif name == "Swipe":
        start = _denormalize(action["start"], device)
        end = _denormalize(action["end"], device)
        adb.swipe(*start, *end, device_id=device)
    elif name == "Type":
        adb.input_text_safe(action["text"], device)
    elif name == "Back":
        adb.back(device)
    elif name == "Home":
        adb.home(device)
    elif name == "Launch":
        if not adb.launch_app(action.get("app", ""), device):
            raise ValueError("App 启动失败")
    elif name == "Wait":
        seconds = float(action.get("seconds", 1))
        if not 0 <= seconds <= 10:
            raise ValueError("Wait seconds 必须在 0-10 之间")
        time.sleep(seconds)
    store._manifest({"event": "action", "action": action, "allowed": True})
    return {"allowed": True, "next": "observe"}


def run(args: argparse.Namespace) -> dict:
    if args.command == "check":
        import PIL

        devices = adb.list_devices()
        if not devices:
            raise ValueError("没有在线 Android 设备，请连接并启用 USB 调试")
        return {"devices": devices, "pillow": PIL.__version__, "api_key_required": False,
                "caller_requirement": "调用者必须能读取图片并理解手机界面；此脚本不能代替视觉模型"}
    if args.command == "start":
        device = adb.ensure_device(args.device_id)
        mode = args.dedup
        if mode == "auto":
            mode = "chat" if args.app.lower() in CHAT_DEDUP_APPS else "global"
        store = SessionStore.create(
            args.data_dir.resolve(), args.app, args.target, args.task, device,
            args.model, args.model, dedup_mode=mode, execution_mode="caller",
            max_screens=args.max_screens, max_actions=args.max_actions,
        )
        return {"session": str(store.session_dir), "next": "observe"}

    store = SessionStore.resume(args.session.resolve())
    metadata = store._session_metadata
    if metadata.get("execution_mode") != "caller":
        raise ValueError("此入口只支持 caller 模式会话")
    device = metadata["device_id"]
    pending = _pending(store)
    if pending and args.command not in {"record", "finish"}:
        raise ValueError("上一屏尚未提取；先 record 成功结果或失败原因，避免遗漏证据")
    if args.command == "observe":
        screenshot = adb.capture(device)
        path = store.session_dir / "navigation" / f"observe-{uuid.uuid4().hex}.png"
        path.parent.mkdir(exist_ok=True)
        _atomic_write_bytes(path, screenshot.png_bytes)
        store._manifest({"event": "observation", "path": str(path.relative_to(store.session_dir)),
                         "sha256": screenshot.sha256, "captured_at": screenshot.captured_at})
        return {"screenshot": str(path), "sha256": screenshot.sha256,
                "current_app": adb.get_current_app(device), "task": metadata["task"],
                "next": "调用者打开图片，决定下一步只读动作或核验目标页后 capture"}
    if args.command == "action":
        action = json.loads(args.action_file.read_text("utf-8"))
        if not isinstance(action, dict):
            raise ValueError("动作必须是 JSON 对象")
        try:
            return execute_action(store, action)
        except Exception as exc:
            store._manifest({"event": "action_error", "action": action, "error": str(exc)})
            raise
    if args.command == "capture":
        if store.screen_count >= metadata["max_screens"]:
            raise ValueError("已达到 max-screens，请 finish --reason max_screens")
        # capture 是调用者在 observe 看图核验目标页之后作出的采集决定。
        if store._navigation is None:
            store.record_navigation(True, "caller_verified", "调用者已看图核验目标页",
                                    current_app=adb.get_current_app(device))
        record = store.save_screenshot(adb.capture(device))
        return {**record, "screenshot": str(store.session_dir / record["path"]),
                "prompt": build_extract_prompt(metadata["app"], metadata["task"]),
                "next": "调用者打开这张证据图片，用自身视觉能力生成 JSON，再 record"}
    if args.command == "record":
        if pending is None:
            raise ValueError("没有待提取截图；已记录的结果不能覆盖")
        screen = pending["screen"]
        if args.error is not None:
            store.save_extraction_failure(screen, pending, args.error)
            return {"screen": screen, "extraction_failed": True, "next": "finish 或继续浏览"}
        raw = args.result_file.read_text("utf-8")
        extraction = _parse_json(raw)
        for item in extraction["items"]:
            for field in ("type", "title", "text", "sender", "time_hint"):
                if item.get(field) is not None and not isinstance(item[field], str):
                    raise ValueError(f"{field} 必须是字符串或 null，请调用者修正 JSON")
        extraction["items"] = [item for item in extraction["items"]
                               if (item.get("text") or "").strip()
                               or (item.get("title") or "").strip()]
        extraction["raw_response"] = raw
        stats = store.save_extraction(screen, pending, extraction)
        return {"screen": screen, **stats, "screens": store.screen_count,
                "max_screens": metadata["max_screens"], "next": "按新增条目与画面位移决定翻页或 finish"}
    if args.command == "finish":
        if args.reason == "navigation_failed" and store.screen_count:
            raise ValueError("已有采集截图，不能把会话标为 navigation_failed")
        if args.reason != "navigation_failed" and not store.screen_count:
            raise ValueError("尚未采集；导航失败请 finish --reason navigation_failed")
        if pending:
            if not args.error:
                raise ValueError("待提取截图必须先 record；或用 finish --error 留失败记录")
            store.save_extraction_failure(pending["screen"], pending, args.error)
        if args.reason == "navigation_failed":
            store.record_navigation(False, "caller_failed", args.error or "调用者未到达目标页")
        index = store.finalize(args.reason, error=args.error)
        return {"index": str(index), "stop_reason": args.reason}
    raise ValueError("未知命令")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("allowed") is not False else 2


if __name__ == "__main__":
    sys.exit(main())
