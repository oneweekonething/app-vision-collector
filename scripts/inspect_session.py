#!/usr/bin/env python3
"""session 溯源校验 CLI。

对一次采集会话做离线完整性复核：
1. manifest.jsonl 台账可解析、每行是含 event 字段的对象（坏行按问题报告）；
2. 每张台账截图文件存在且 SHA-256 与台账记录一致；
3. index.json 合法 JSON、结构正确（items 为对象数组、每条含 evidence 对象），
   evidence 与截图文件（存在 + SHA-256）及 manifest 台账（路径已登记、
   哈希一致、屏号一致、条目对应的 items 事件一致）交叉核对，损坏/篡改按问题报告而不抛异常——
   审计工具对任何输入都应给出结论；
4. 每屏都有对应的 extracted/*.json；
5. 导航失败不变量：navigation_failed 会话必须 navigation.success=false
   且 0 截图（0 屏是预期结果）；其余会话 0 截图判为问题；
6. navigation 台账事件与 index.json 顶层 navigation 字段逐字段一致；
7. 会话生命周期：session_started / session_finished 各恰好一条，finished
   为最后一条事件，且 stop_reason / error / totals 与 index 交叉一致。

用法:
    python3 scripts/inspect_session.py collections/2026-09-27/<session-id> [--quiet]
退出码: 0=校验通过, 1=发现问题
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector.store.session_store import _file_sha256  # noqa: E402


def verify_session(session_dir: Path) -> tuple[bool, list[str]]:
    """校验一个 session 目录，返回 (是否通过, 报告行列表)。"""
    report: list[str] = []
    problems = 0

    def problem(message: str) -> None:
        nonlocal problems
        problems += 1
        report.append(f"  ✗ {message}")

    def info(message: str) -> None:
        report.append(f"  · {message}")

    def conclude() -> tuple[bool, list[str]]:
        ok = problems == 0
        report.insert(0, f"session: {session_dir}")
        report.insert(1, f"结果: {'✓ 校验通过' if ok else f'✗ 发现 {problems} 个问题'}")
        return ok, report

    # 1. 基础文件
    manifest_path = session_dir / "manifest.jsonl"
    index_path = session_dir / "index.json"
    for path, name in [(manifest_path, "manifest.jsonl"), (index_path, "index.json")]:
        if not path.exists():
            problem(f"缺少 {name}（会话可能未正常收尾）")
    if problems:
        return False, report

    # 2. 解析台账（每行必须是 JSON 对象；坏行按问题报告并跳过，绝不崩溃）
    events: list[dict] = []
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed_line = json.loads(line)
        except json.JSONDecodeError as exc:
            problem(f"manifest.jsonl 第 {line_no} 行不是合法 JSON: {exc}")
            continue
        if not isinstance(parsed_line, dict):
            problem(f"manifest.jsonl 第 {line_no} 行不是 JSON 对象")
            continue
        if "event" not in parsed_line:
            problem(f"manifest.jsonl 第 {line_no} 行缺少 event 字段")
            continue
        events.append(parsed_line)
    screenshots = [e for e in events if e.get("event") == "screenshot"]
    item_events = [e for e in events if e.get("event") == "items"]
    info(f"台账事件 {len(events)} 条（截图 {len(screenshots)} 屏 / 条目事件 {len(item_events)} 个）")

    # 3. 截图完整性（只有通过 shape 校验的事件进入 valid_screenshots，
    #    后续所有逻辑只使用已验证数据，避免坏字段在下游崩溃）
    valid_screenshots: list[dict] = []
    hash_by_screen: dict[int, dict] = {}
    for event in screenshots:
        rel = event.get("path")
        screen = event.get("screen")
        if not isinstance(rel, str) or not rel or type(screen) is not int or screen < 1:
            problem(f"截图台账事件缺少合法的 path/screen 字段: {event}")
            continue
        path = session_dir / rel
        if not path.is_file():
            problem(f"截图缺失或不是文件: {rel}")
            continue
        actual = _file_sha256(path)
        if actual != event.get("sha256"):
            problem(f"截图哈希不一致: {rel}（台账 {str(event.get('sha256'))[:12]}… vs 实际 {actual[:12]}…）")
        else:
            hash_by_screen[screen] = event
        valid_screenshots.append(event)
    info(f"截图完整性: {len(hash_by_screen)}/{len(valid_screenshots)} 通过 SHA-256 校验")

    # 4. 解析 index（损坏按问题报告，不让审计工具崩溃）
    try:
        parsed = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problem(f"index.json 不是合法 JSON: {exc}")
        return conclude()
    if not isinstance(parsed, dict):
        problem("index.json 顶层不是 JSON 对象")
        return conclude()
    index = parsed

    items = index.get("items", [])
    if not isinstance(items, list):
        problem(f"index.items 不是数组（实际 {type(items).__name__}）")
        items = []

    # 条目溯源交叉核对：evidence 不只要对得上截图文件本身，还必须对得上
    # manifest 台账。只验"文件存在 + 文件哈希=自报哈希"挡不住三类篡改：
    # 删掉 evidence.sha256（无处可比即跳过）、把条目指向一个存在但从未
    # 登记过的截图文件、屏号张冠李戴。台账登记表含全部 path 合法的截图
    # 事件——其自身哈希对不上的已在第 3 步单独报告。
    registered: dict[str, dict] = {
        e["path"]: e for e in screenshots
        if isinstance(e.get("path"), str) and e.get("path")
    }
    # item_id 关联到首次收录它的采集事件，防止 evidence 被整体换成另一屏
    # 的合法路径、哈希与屏号。先验证字段类型，坏台账不能让审计工具崩溃。
    item_sources: dict[str, dict] = {}
    for event in item_events:
        screen = event.get("screen")
        item_ids = event.get("item_ids")
        if type(screen) is not int or screen < 1 or not isinstance(item_ids, list) \
                or not all(isinstance(item_id, str) and item_id for item_id in item_ids):
            problem(f"条目台账事件缺少合法的 screen/item_ids 字段: {event}")
            continue
        screenshot_event = hash_by_screen.get(screen)
        if screenshot_event is None \
                or event.get("screenshot_sha256") != screenshot_event.get("sha256"):
            problem(f"第 {screen} 屏条目台账的 screenshot_sha256 与截图记录不一致")
        for item_id in item_ids:
            if item_id in item_sources:
                problem(f"条目 {item_id} 在 items 台账中重复登记")
            else:
                item_sources[item_id] = event

    items_with_problems = 0
    seen_item_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            problem(f"index.items 中存在非对象条目: {str(item)[:60]}")
            items_with_problems += 1
            continue
        evidence = item.get("evidence")
        if not isinstance(evidence, dict):
            problem(f"{item.get('item_id', '?')} 缺少 evidence 对象")
            items_with_problems += 1
            continue
        rel = evidence.get("screenshot")
        if not isinstance(rel, str) or not rel:
            problem(f"{item.get('item_id')} 缺少 evidence.screenshot 或类型非法")
            items_with_problems += 1
            continue

        flagged_before = problems
        item_id = item.get("item_id")
        source = None
        if not isinstance(item_id, str) or not item_id:
            problem("index 条目缺少合法的 item_id")
        else:
            if item_id in seen_item_ids:
                problem(f"index 条目 {item_id} 重复出现")
            seen_item_ids.add(item_id)
            source = item_sources.get(item_id)
            if source is None:
                problem(f"条目 {item_id} 未在 items 台账中登记")
        screen_index = evidence.get("screen_index")
        if type(screen_index) is not int or screen_index < 1:
            problem(f"{item_id} 缺少合法的 evidence.screen_index（必须是正整数）")
        sha = evidence.get("sha256")
        if not isinstance(sha, str) or not sha:
            problem(f"{item.get('item_id')} 缺少 evidence.sha256（溯源字段不完整）: {rel}")
        else:
            path = session_dir / rel
            if not path.is_file():
                problem(f"{item.get('item_id')} 指向的截图不存在或不是文件: {rel}")
            else:
                actual = _file_sha256(path)
                if actual != sha:
                    problem(f"{item.get('item_id')} 的截图哈希不匹配: {rel}")

        manifest_event = registered.get(rel)
        if manifest_event is None:
            problem(f"{item.get('item_id')} 指向台账未登记的截图: {rel}（证据链断裂）")
        else:
            if isinstance(sha, str) and sha and manifest_event.get("sha256") != sha:
                problem(f"{item.get('item_id')} 的 evidence.sha256 与台账记录不一致: {rel}")
            if type(screen_index) is int \
                    and screen_index != manifest_event.get("screen"):
                problem(f"{item.get('item_id')} 的屏号与台账不符"
                        f"（evidence.screen_index={screen_index}"
                        f" vs 台账 {manifest_event.get('screen')}）: {rel}")
        if source is not None and (
            source.get("screen") != screen_index or source.get("screenshot_sha256") != sha
        ):
            problem(f"条目 {item_id} 的 evidence 与对应的 items 台账事件不一致")
        if problems > flagged_before:
            items_with_problems += 1
    info(f"条目溯源: {len(items)} 条" + (
        "全部通过文件与台账交叉校验" if items_with_problems == 0
        else f"（{items_with_problems} 条存在问题，见上）"))
    for item_id in item_sources.keys() - seen_item_ids:
        problem(f"items 台账中的条目 {item_id} 未出现在 index 中")

    # 5. totals 一致性
    totals = index.get("totals", {})
    if not isinstance(totals, dict):
        problem("index.totals 不是 JSON 对象")
        totals = {}
    if totals.get("items_unique") != len(items):
        problem(f"index.totals.items_unique={totals.get('items_unique')} 与实际条目数 {len(items)} 不符")
    if totals.get("screens") != len(valid_screenshots):
        problem(f"index.totals.screens={totals.get('screens')} 与台账截图数 {len(valid_screenshots)} 不符")

    # 5.5 空会话与导航失败不变量：
    # 导航成功 → 允许（且通常应有）截图；
    # 导航失败（stop_reason=navigation_failed 且 navigation.success=false）
    #   → 截图必须为 0（导航失败即不采集，0 屏是预期结果而非损坏）；
    # 除此之外的 0 屏会话仍判问题——采集根本没开始，vacuous 通过会掩盖问题。
    stop_reason = index.get("stop_reason")
    navigation = index.get("navigation")
    if navigation is None:
        navigation = {}
    elif not isinstance(navigation, dict):
        problem(f"index.navigation 不是 JSON 对象（实际 {type(navigation).__name__}）")
        navigation = {}

    if stop_reason == "navigation_failed":
        if navigation.get("success") is not False:
            problem("stop_reason=navigation_failed 但 navigation.success 不是 false，台账不一致")
        if valid_screenshots:
            problem(f"navigation_failed 会话不应产生采集截图（发现 {len(valid_screenshots)} 张）")
    elif not valid_screenshots:
        problem(f"会话没有任何截图（stop_reason={stop_reason}），疑似运行中断")

    # 5.6 navigation 台账事件与 index 顶层字段逐项一致（跨文件不变量）
    nav_events = [e for e in events if e.get("event") == "navigation"]
    if len(nav_events) > 1:
        problem(f"manifest 中有多条 navigation 事件（{len(nav_events)} 条）")
    if navigation:
        if not nav_events:
            problem("index.json 有 navigation 字段但 manifest 缺少 navigation 事件")
        else:
            for key in ("success", "reason", "message", "steps", "current_app"):
                manifest_value = nav_events[0].get(key)
                if navigation.get(key) != manifest_value:
                    problem(
                        f"navigation.{key} 台账与 index 不一致"
                        f"（manifest={manifest_value!r} vs index={navigation.get(key)!r}）")
    elif nav_events:
        problem("manifest 有 navigation 事件但 index.json 缺少 navigation 字段")

    # 5.7 会话生命周期不变量：started/finished 各恰好一条，finished 是最后
    # 一条事件，且 stop_reason/totals 与 index 一致——防止"index 已写出但
    # 终态事件未落盘"的 crash window 骗过校验。
    started_events = [e for e in events if e.get("event") == "session_started"]
    finished_events = [e for e in events if e.get("event") == "session_finished"]
    if len(started_events) != 1:
        problem(f"session_started 事件应有恰好 1 条，实际 {len(started_events)} 条")
    if len(finished_events) != 1:
        problem(f"session_finished 事件应有恰好 1 条，实际 {len(finished_events)} 条"
                "（缺失说明会话未正常收尾）")
    if finished_events:
        finished = finished_events[0]
        if events and finished is not events[-1]:
            problem("session_finished 不是最后一条台账事件（之后仍有事件写入）")
        for key in ("stop_reason", "error"):
            if finished.get(key) != index.get(key):
                problem(f"session_finished.{key} 与 index 不一致"
                        f"（manifest={finished.get(key)!r} vs index={index.get(key)!r}）")
        for key in ("screens", "items_extracted", "items_unique", "duplicates", "extract_failures"):
            if finished.get(key) != totals.get(key):
                problem(f"session_finished.{key} 与 index.totals 不一致"
                        f"（manifest={finished.get(key)!r} vs index={totals.get(key)!r}）")

    # 6. 每屏提取文件（只使用通过 shape 校验的截图事件）
    missing_extracted = [
        s["screen"] for s in valid_screenshots
        if not (session_dir / "extracted" / f"screen-{s['screen']:04d}.json").is_file()
    ]
    if missing_extracted:
        problem(f"缺少提取结果文件的屏: {missing_extracted}")

    return conclude()


def main() -> int:
    parser = argparse.ArgumentParser(prog="inspect_session", description="session 溯源校验")
    parser.add_argument("session_dir", type=Path, help="采集会话目录")
    args = parser.parse_args()

    if not args.session_dir.is_dir():
        print(f"目录不存在: {args.session_dir}")
        return 1

    ok, report = verify_session(args.session_dir)
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
