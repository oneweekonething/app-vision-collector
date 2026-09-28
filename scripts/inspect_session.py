#!/usr/bin/env python3
"""session 溯源校验 CLI。

对一次采集会话做离线完整性复核：
1. manifest.jsonl 台账可解析、结构完整；
2. 每张台账截图文件存在且 SHA-256 与台账记录一致；
3. index.json 中每条信息的 evidence 指向存在的截图且哈希匹配；
4. 每屏都有对应的 extracted/*.json；
5. 导航失败不变量：navigation_failed 会话必须 navigation.success=false
   且 0 截图（0 屏是预期结果）；其余会话 0 截图判为问题。

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

    # 1. 基础文件
    manifest_path = session_dir / "manifest.jsonl"
    index_path = session_dir / "index.json"
    for path, name in [(manifest_path, "manifest.jsonl"), (index_path, "index.json")]:
        if not path.exists():
            problem(f"缺少 {name}（会话可能未正常收尾）")
    if problems:
        return False, report

    # 2. 解析台账
    events: list[dict] = []
    for line_no, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            problem(f"manifest.jsonl 第 {line_no} 行不是合法 JSON: {exc}")
    screenshots = [e for e in events if e.get("event") == "screenshot"]
    item_events = [e for e in events if e.get("event") == "items"]
    info(f"台账事件 {len(events)} 条（截图 {len(screenshots)} 屏 / 条目事件 {len(item_events)} 个）")

    # 3. 截图完整性
    hash_by_screen: dict[int, dict] = {}
    for event in screenshots:
        path = session_dir / event["path"]
        if not path.exists():
            problem(f"截图缺失: {event['path']}")
            continue
        actual = _file_sha256(path)
        if actual != event.get("sha256"):
            problem(f"截图哈希不一致: {event['path']}（台账 {str(event.get('sha256'))[:12]}… vs 实际 {actual[:12]}…）")
        else:
            hash_by_screen[event["screen"]] = event
    info(f"截图完整性: {len(hash_by_screen)}/{len(screenshots)} 通过 SHA-256 校验")

    # 4. index 条目的 evidence 指向
    index = json.loads(index_path.read_text(encoding="utf-8"))
    items = index.get("items", [])
    for item in items:
        evidence = item.get("evidence", {})
        rel = evidence.get("screenshot")
        if not rel:
            problem(f"{item.get('item_id')} 缺少 evidence.screenshot")
            continue
        path = session_dir / rel
        if not path.exists():
            problem(f"{item.get('item_id')} 指向的截图不存在: {rel}")
            continue
        actual = _file_sha256(path)
        if evidence.get("sha256") and actual != evidence["sha256"]:
            problem(f"{item.get('item_id')} 的截图哈希不匹配: {rel}")
    info(f"条目溯源: {len(items)} 条全部指向已校验截图" if not problems or all(
        "不存在" not in line and "不匹配" not in line and "缺失" not in line for line in report
    ) else f"条目溯源: {len(items)} 条（存在问题见上）")

    # 5. totals 一致性
    totals = index.get("totals", {})
    if totals.get("items_unique") != len(items):
        problem(f"index.totals.items_unique={totals.get('items_unique')} 与实际条目数 {len(items)} 不符")
    if totals.get("screens") != len(screenshots):
        problem(f"index.totals.screens={totals.get('screens')} 与台账截图数 {len(screenshots)} 不符")

    # 5.5 空会话与导航失败不变量：
    # 导航成功 → 允许（且通常应有）截图；
    # 导航失败（stop_reason=navigation_failed 且 navigation.success=false）
    #   → 截图必须为 0（导航失败即不采集，0 屏是预期结果而非损坏）；
    # 除此之外的 0 屏会话仍判问题——采集根本没开始，vacuous 通过会掩盖问题。
    stop_reason = index.get("stop_reason")
    navigation = index.get("navigation") or {}

    if stop_reason == "navigation_failed":
        if navigation.get("success") is not False:
            problem("stop_reason=navigation_failed 但 navigation.success 不是 false，台账不一致")
        if screenshots:
            problem(f"navigation_failed 会话不应产生采集截图（发现 {len(screenshots)} 张）")
    elif not screenshots:
        problem(f"会话没有任何截图（stop_reason={stop_reason}），疑似运行中断")

    # 6. 每屏提取文件
    missing_extracted = [
        s["screen"] for s in screenshots
        if not (session_dir / "extracted" / f"screen-{s['screen']:04d}.json").exists()
    ]
    if missing_extracted:
        problem(f"缺少提取结果文件的屏: {missing_extracted}")

    ok = problems == 0
    report.insert(0, f"session: {session_dir}")
    report.insert(1, f"结果: {'✓ 校验通过' if ok else f'✗ 发现 {problems} 个问题'}")
    return ok, report


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
