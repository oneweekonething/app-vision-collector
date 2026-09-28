#!/usr/bin/env python3
"""app-vision-collector 采集 CLI。

用视觉大模型 + ADB 控制手机完成 App 内信息采集，全程截屏留存、逐条溯源。

示例:
    # 微信群聊采集
    python3 scripts/collect.py --app wechat --target "AI 交流群" \
        --task "进入该群聊并采集聊天消息" --max-screens 20

    # 小红书搜索结果
    python3 scripts/collect.py --app xiaohongshu --target "手机摄影" \
        --task "浏览搜索结果并采集笔记标题与作者" --max-screens 10

    # 手机手动停到目标页面，只做截图+提取
    python3 scripts/collect.py --app generic --no-navigate \
        --task "采集当前屏幕信息" --max-screens 5

    # 环境自检
    python3 scripts/collect.py --check
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector import adb
from collector.agent import ExtractAgent, StepAgent
from collector.agent.prompts import APP_NAV_TEMPLATES
from collector.config import CHAT_DEDUP_APPS, CollectorConfig
from collector.imaging import average_hash, hamming_distance
from collector.store import SessionStore


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collect",
        description="可溯源的 App 信息采集器（ADB + 视觉大模型）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("示例:")[1] if __doc__ else None,
    )
    parser.add_argument("--app", default="generic",
                        help="目标 App：wechat / xiaohongshu / 自定义名称（默认 generic）")
    parser.add_argument("--target", default="",
                        help="采集目标：群名、搜索关键词等（导航时使用）")
    parser.add_argument("--task", default="",
                        help="自然语言采集任务描述（不填则由 app+target 生成）")
    parser.add_argument("--max-screens", type=int, default=None,
                        help="最多采集多少屏（默认取配置/环境变量 20）")
    parser.add_argument("--nav-steps", type=int, default=None,
                        help="导航 Agent 最大步数（默认 20）")
    parser.add_argument("--device-id", default=None, help="adb 设备 ID（默认取第一台在线设备）")
    parser.add_argument("--data-dir", default=None, help="采集数据根目录（默认 ./collections）")
    parser.add_argument("--no-navigate", action="store_true",
                        help="跳过导航：假设手机已停在目标页面，直接开始采集")
    parser.add_argument("--no-reset", action="store_true",
                        help="导航前不按 Home 复位（默认复位，避免分屏/深层页面导致坐标错乱）")
    parser.add_argument("--no-scroll", action="store_true",
                        help="只采集当前一屏，不翻页")
    parser.add_argument("--dedup", choices=["auto", "chat", "global"], default="auto",
                        help="去重策略：chat=聊天类滑窗去重（同文本远距重现视为真实重复，"
                             "适用于微信聊天）；global=全部内容全局去重（同内容远距重现即"
                             "重复曝光，适用于红果/小红书等信息流）。auto 按 --app 判定")
    parser.add_argument("--check", action="store_true", help="只做环境自检，不采集")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    return parser


def run_check(config: CollectorConfig) -> int:
    """环境自检：ADB、设备、模型配置、依赖。"""
    print("== app-vision-collector 自检 ==")

    try:
        devices = adb.list_devices()
        print(f"[ADB] 可执行文件: {adb.get_adb_executable()}")
        print(f"[ADB] 在线设备: {[d['device_id'] for d in devices] or '无'}")
        adb_ok = bool(devices)
    except Exception as exc:
        print(f"[ADB] 检查失败: {exc}")
        adb_ok = False

    key_ok = bool(config.api_key)
    print(f"[模型] API Key: {'已配置' if key_ok else '未配置（设置 AVC_API_KEY / DASHSCOPE_API_KEY）'}")
    print(f"[模型] API Base: {config.api_base}")
    print(f"[模型] 提取模型: {config.vlm_model} / 导航模型: {config.nav_model}")

    try:
        import PIL  # noqa: F401
        import openai  # noqa: F401

        print("[依赖] Pillow / openai 已安装")
        deps_ok = True
    except ImportError as exc:
        print(f"[依赖] 缺少依赖: {exc}，请执行 pip install -r requirements.txt")
        deps_ok = False

    if adb_ok and key_ok and deps_ok:
        print("== 自检通过 ==")
        return 0
    print("== 自检未通过，请先修复以上标红项 ==")
    return 1


def build_navigation_task(app: str, target: str, explicit_task: str = "") -> str:
    """组装导航任务描述（攻略见 references/*.md）。

    优先级：显式 --task > App 导航模板（含 {target}）> 通用兜底。
    显式任务永远不被模板覆盖——模板只在调用者只给了 target 时补齐
    "先打开哪个 App、走什么路径"的导航语义。
    """
    if explicit_task.strip():
        return explicit_task.strip()
    if target:
        template = APP_NAV_TEMPLATES.get(app) or APP_NAV_TEMPLATES.get(app.lower()) \
            or APP_NAV_TEMPLATES["generic"]
        return template.format(target=target)
    return f"打开 {app} 并停留在需要采集信息的页面"


def resolve_dedup_mode(app: str, choice: str = "auto") -> str:
    """把 --dedup 选项解析为实际去重模式（auto 按 App 形态判定）。"""
    if choice != "auto":
        return choice
    return "chat" if app.lower() in CHAT_DEDUP_APPS else "global"


def run_collection(args: argparse.Namespace, config: CollectorConfig) -> int:
    """主采集流程：导航 → （截屏 → 提取 → 去重）循环 → 收尾。"""
    device_id = adb.ensure_device(args.device_id)
    print(f"[设备] {device_id}")

    if args.max_screens is not None:
        config.max_screens = args.max_screens
    if args.nav_steps is not None:
        config.nav_steps = args.nav_steps
    if args.data_dir:
        config.data_dir = Path(args.data_dir)

    task = args.task or (
        f"采集 {args.app} 中「{args.target}」页面上的信息" if args.target
        else f"采集 {args.app} 当前页面上的信息"
    )

    dedup_mode = resolve_dedup_mode(args.app, args.dedup)
    print(f"[去重] 模式: {dedup_mode}"
          + ("（聊天滑窗：同文本出窗后视为真实重复）" if dedup_mode == "chat"
             else "（全局判重：同内容远距重现视为重复曝光）"))

    store = SessionStore.create(
        data_dir=config.data_dir,
        app=args.app,
        target=args.target,
        task=task,
        device_id=device_id,
        vlm_model=config.vlm_model,
        nav_model=config.nav_model,
        dedup_mode=dedup_mode,
    )
    print(f"[会话] {store.session_dir}")

    stop_reason = "completed"
    error: str | None = None

    try:
        # 1. 导航：由 StepAgent 把手机带到目标页面；失败则本会话不再采集，
        #    避免把错误页面的数据当成合法证据入库
        if not args.no_navigate:
            if not args.no_reset:
                # 从已知状态开始：分屏/悬浮窗/深层页面会让 0-999 坐标映射错乱，
                # 导航模型会反复点空（实测一次跑 20 步烧光配额），先回桌面复位
                adb.home(device_id)
                time.sleep(1.2)
            nav_task = build_navigation_task(args.app, args.target, args.task)
            print(f"[导航] {nav_task.splitlines()[0]}")
            nav = StepAgent(config, device_id=device_id, verbose=args.verbose).run(nav_task)
            print(f"[导航完成] success={nav.success} reason={nav.reason} "
                  f"steps={nav.steps} app={nav.current_app}")
            if nav.message:
                print(f"[导航说明] {nav.message}")
            store.record_navigation(nav.success, nav.reason, nav.message,
                                    nav.steps, nav.current_app)
            if not nav.success:
                stop_reason = "navigation_failed"
                error = f"导航未完成（{nav.reason}）: {nav.message}"
                print(f"[停止] {error}")
                index_path = store.finalize(stop_reason, error=error)
                return _summary(store, stop_reason, error, index_path)

        # 2. 截屏 → 提取 → 翻页 循环
        extractor = ExtractAgent(config, app=args.app, task=task)

        def scroll_forward() -> None:
            adb.swipe_to_next_screen(device_id)
            time.sleep(config.scroll_pause)

        no_new_streak = 0
        consecutive_failures = 0
        stuck_streak = 0          # 相邻屏感知哈希几乎不变 → 翻页已无效
        prev_hash: int | None = None
        max_screens = 1 if args.no_scroll else config.max_screens

        while store.screen_count < max_screens:
            screenshot = adb.capture(device_id)
            record = store.save_screenshot(screenshot)
            print(f"[截屏 {store.screen_count}/{max_screens}] {record['path']} "
                  f"({record['width']}x{record['height']})")

            current_hash = average_hash(screenshot.png_bytes)
            if current_hash is not None and prev_hash is not None \
                    and hamming_distance(current_hash, prev_hash) <= config.stuck_hash_distance:
                stuck_streak += 1
            else:
                stuck_streak = 0
            prev_hash = current_hash

            try:
                extraction = extractor.extract(screenshot)
            except Exception as exc:
                # 单屏提取失败不中断采集：证据与台账照留，跳过该屏继续；
                # 连续失败达到上限才认为模型/链路出了问题，终止会话
                consecutive_failures += 1
                print(f"[提取失败] screen {store.screen_count}: {exc}")
                store.save_extraction_failure(
                    store.screen_count, record, exc,
                    raw_response=getattr(exc, "last_raw", ""))
                if consecutive_failures >= config.max_extract_failures:
                    stop_reason = "extraction_failed"
                    error = f"连续 {consecutive_failures} 屏提取失败: {exc}"
                    print(f"[停止] {error}")
                    break
                if store.screen_count >= max_screens:
                    stop_reason = "max_screens"
                    break
                scroll_forward()
                continue

            consecutive_failures = 0
            stats = store.save_extraction(store.screen_count, record, extraction)
            print(f"[提取] 新增 {stats['new']} 条"
                  f"（提取 {stats['extracted']}，重复 {stats['duplicates']}）")

            if stats["new"] == 0:
                no_new_streak += 1
                # 无新内容 ≠ 到底：长图/大卡片可能连续几屏没有新条目。
                # 只有"画面也翻不动了"或"宽限屏数用尽"才认定信息边界。
                if stuck_streak >= 1:
                    print(f"[停止] 连续 {no_new_streak} 屏无新内容且画面无位移，已到信息边界")
                    stop_reason = "no_new_items"
                    break
                if no_new_streak >= config.no_new_stop_streak + config.no_new_grace_screens:
                    print(f"[停止] 连续 {no_new_streak} 屏无新内容（宽限已用尽），停止采集")
                    stop_reason = "no_new_items"
                    break
            else:
                no_new_streak = 0

            if store.screen_count >= max_screens:
                stop_reason = "max_screens"
                break

            scroll_forward()

    except KeyboardInterrupt:
        stop_reason = "interrupted"
        print("\n[中断] 用户停止采集")
    except Exception as exc:
        stop_reason = "error"
        error = str(exc)
        print(f"[错误] {exc}")

    index_path = store.finalize(stop_reason, error=error)
    return _summary(store, stop_reason, error, index_path)


def _summary(store: SessionStore, stop_reason: str, error: str | None,
             index_path: Path | None) -> int:
    """打印会话收尾摘要并返回退出码。"""
    print("=" * 56)
    summary = (f"[完成] 原因: {stop_reason} | 屏数: {store.screen_count} | "
               f"提取: {store.extracted_total} | 去重后: {len(store.items)}")
    if store.extract_failures_total:
        summary += f" | 提取失败: {store.extract_failures_total} 屏"
    print(summary)
    if error:
        print(f"[错误详情] {error}")
    print(f"[产物] {store.session_dir}")
    print(f"[校验] python3 scripts/inspect_session.py {store.session_dir}")
    print(f"[索引] {index_path}")
    return 0 if stop_reason in {"completed", "no_new_items", "max_screens", "interrupted"} else 2


def main() -> int:
    args = build_arg_parser().parse_args()
    config = CollectorConfig.from_env()

    if args.check:
        return run_check(config)
    return run_collection(args, config)


if __name__ == "__main__":
    sys.exit(main())
