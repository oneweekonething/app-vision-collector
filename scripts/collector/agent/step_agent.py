"""StepAgent：Open-AutoGLM 风格的 UI 导航智能体。

循环：截屏 → 视觉模型决策 → 解析（DSL 优先，兼容 JSON）→ ActionGuard
只读护栏 → ADB 执行 → 失败/拦截原因反馈给模型 → 下一屏。

只读保证由两层机制构成：
1. 动作集合不含写原语（Tap/Swipe/Type/Back/Home/Launch/Wait）；
2. 每个 Tap 在执行前经过 ActionGuard 语义检查（intent/target_text 命中
   写操作关键词即拒绝并要求模型重新规划）。

run() 返回结构化 NavigationResult：导航未成功（最大步数、设备故障、
护栏连续拦截、目标核验失败）时 success=False，主流程据此终止采集，
避免"采错页面但证据链看似合法"。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from collector import adb
from collector.adb.connection import AdbCommandError
from collector.agent.prompts import NAV_VERIFY_PROMPT, build_nav_system_prompt
from collector.agent.safety import ActionGuard, GuardVerdict
from collector.config import CollectorConfig

# 设备/命令类异常：可反馈重试，连续达到阈值即终止导航
_DEVICE_ERRORS = (AdbCommandError, adb.ScreenshotError, subprocess.TimeoutExpired)

_ACTION_ALIASES = {
    "tap": "Tap", "swipe": "Swipe", "type": "Type", "back": "Back",
    "home": "Home", "launch": "Launch", "wait": "Wait", "finish": "finish",
}


@dataclass
class NavigationResult:
    """导航任务的结构化结果。

    reason ∈ {finished, max_steps, device_error, safety_aborted, action_failed}
    """

    success: bool
    reason: str
    message: str = ""
    steps: int = 0
    current_app: str = ""


class _AbortNavigation(Exception):
    """护栏/动作连续失败达到阈值，导航应立即终止。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class StepAgent:
    """导航智能体。"""

    def __init__(self, config: CollectorConfig, device_id: str | None = None,
                 verbose: bool = False, client: Any | None = None):
        self.config = config
        self.device_id = device_id
        self.verbose = verbose
        self.max_steps = config.nav_steps
        self.client = client or OpenAI(base_url=config.api_base, api_key=config.api_key)
        self.guard = ActionGuard()
        # GLM-5.x 强制思考模型：调低思考档位（按模型名判定，详见 extractor）
        self.extra_body = (
            {"thinking": {"type": "enabled", "effort": "low"}}
            if "glm-5" in config.nav_model.lower()
            else None
        )
        self.context: list[dict[str, Any]] = []
        self.step_count = 0
        self._fail_streak = 0
        self._guard_streak = 0
        self._last_app = "unknown"

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    # ------------------------------------------------------------------- run

    def run(self, task: str) -> NavigationResult:
        """执行一个导航任务；finish 后核验目标页，未通过则要求继续导航。"""
        self.context = [{"role": "system", "content": build_nav_system_prompt()}]
        self.step_count = 0
        self._fail_streak = 0
        self._guard_streak = 0
        first_prompt: str | None = task

        try:
            while self.step_count < self.max_steps:
                self.step_count += 1
                try:
                    outcome = self._execute_step(first_prompt, is_first=first_prompt is not None)
                    first_prompt = None
                    if outcome["finished"]:
                        if not self.config.nav_verify:
                            self._fail_streak = 0
                            return NavigationResult(
                                True, "finished", outcome.get("message", ""),
                                self.step_count, self._last_app)
                        satisfied, reason = self._verify_target(task)
                        if satisfied:
                            self._fail_streak = 0
                            return NavigationResult(
                                True, "finished",
                                f"{outcome.get('message', '')}；目标页核验通过: {reason}",
                                self.step_count, self._last_app)
                        self._feedback(
                            f"[VERIFY] 目标页核验未通过: {reason}。"
                            "当前页面不满足导航目标，请继续导航，到达后再 finish。")
                except _DEVICE_ERRORS as exc:
                    self._fail_streak += 1
                    self._log(f"[设备/命令失败 {self._fail_streak}/"
                              f"{self.config.nav_device_failures}] {exc}")
                    if self._fail_streak >= self.config.nav_device_failures:
                        return NavigationResult(
                            False, "device_error", str(exc), self.step_count, self._last_app)
                    time.sleep(1.0)
            return NavigationResult(
                False, "max_steps", f"达到最大导航步数（{self.max_steps}）",
                self.step_count, self._last_app)
        except _AbortNavigation as abort:
            return NavigationResult(
                False, abort.reason, abort.message, self.step_count, self._last_app)

    # ------------------------------------------------------------------ step

    def _execute_step(self, user_prompt: str | None = None,
                      is_first: bool = False) -> dict[str, Any]:
        screenshot = adb.capture(self.device_id)
        current_app = adb.get_current_app(self.device_id)
        self._last_app = current_app
        self._log(f"[导航 {self.step_count}/{self.max_steps}] 当前应用: {current_app}")

        screen_info = f"** Screen Info **\n\n{current_app}"
        text = f"{user_prompt}\n\n{screen_info}" if is_first else screen_info

        self.context.append({
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{screenshot_b64(screenshot)}"}},
                {"type": "text", "text": text},
            ],
        })

        content = self._ask(self.context, max_tokens=self.config.nav_max_tokens)
        self._log(f"[模型决策] {content[:200]}")

        # 图片用完即弃，历史里只保留文本
        self.context[-1] = _strip_images(self.context[-1])

        action = _parse_action(content)
        self.context.append(_assistant_record(content))

        return self._execute_action(action)

    def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """护栏检查（自报语义 + Swipe 形状 + 控件树独立核验）+ 执行动作。

        顺序固定为 校验 → 解引用坐标 → 独立核验 → 执行：缺 element、
        坐标非法都由护栏以 [GUARD] 反馈给模型重新规划，而不是 KeyError
        冒泡终止整个会话。
        """
        if action["kind"] == "finish":
            return {"finished": True, "message": action.get("message", "finished")}

        name = str(action.get("name", ""))

        verdict = self.guard.check(action)
        tap_pixel: tuple[int, int] | None = None
        if verdict.allowed and name == "Tap":
            # element 已通过护栏坐标校验，此处解引用是安全的
            tap_pixel = _denormalize(action["element"], self.device_id)
            if self.config.nav_ui_verify:
                verdict = self._verify_tap_target(action, tap_pixel)
        if not verdict.allowed:
            self._guard_denied(verdict)
            return {"finished": False, "message": ""}
        self._guard_streak = 0

        try:
            if name == "Tap":
                adb.tap(*tap_pixel, self.device_id)
            elif name == "Swipe":
                x1, y1 = _denormalize(action["start"], self.device_id)
                x2, y2 = _denormalize(action["end"], self.device_id)
                adb.swipe(x1, y1, x2, y2, duration_ms=500, device_id=self.device_id)
            elif name == "Type":
                adb.input_text_safe(action.get("text", ""), self.device_id)
            elif name == "Back":
                adb.back(self.device_id)
            elif name == "Home":
                adb.home(self.device_id)
            elif name == "Launch":
                if not adb.launch_app(action.get("app", ""), self.device_id):
                    self._note_failure("action_failed",
                                       f"Launch {action.get('app')}: 启动失败（包名不存在或被系统拒绝）")
                    return {"finished": False, "message": ""}
            elif name == "Wait":
                seconds = float(re.sub(r"[^\d.]", "", action.get("duration", "1")) or 1)
                time.sleep(min(seconds, 10))
            else:
                self._log(f"[警告] 未知动作: {name}")
        except _DEVICE_ERRORS as exc:
            # 失败原因作为观察反馈给下一轮决策，而不是吞掉装作已执行
            self._note_failure("device_error", f"{name}: {exc}")
            return {"finished": False, "message": ""}

        self._fail_streak = 0
        return {"finished": False, "message": ""}

    def _guard_denied(self, verdict: GuardVerdict) -> None:
        """登记一次护栏拦截：反馈模型重新规划；连续达阈值终止导航。"""
        self._guard_streak += 1
        self._log(f"[护栏拦截 {self._guard_streak}/{self.config.nav_guard_denials}] "
                  f"{verdict.code}: {verdict.detail}")
        self._feedback(f"[GUARD] {verdict.detail}")
        if self._guard_streak >= self.config.nav_guard_denials:
            raise _AbortNavigation(
                "safety_aborted",
                f"连续 {self._guard_streak} 个动作被只读护栏拒绝"
                f"（最后原因: {verdict.detail}）")

    def _verify_tap_target(self, action: dict[str, Any],
                           pixel: tuple[int, int]) -> GuardVerdict:
        """控件树独立核验：uiautomator 给出点击坐标处的真实控件文本。

        模型自报语义干净时仍可能谎报/漏报（把"发送"按钮写成 open_detail），
        这一层与模型无关。dump 失败（FLAG_SECURE 等）返回 None，退化为
        仅自报判定并记录降级。
        """
        ui_texts = adb.ui_texts_at_point(pixel[0], pixel[1], self.device_id)
        if ui_texts is None:
            self._log("[护栏降级] uiautomator dump 不可用，本次 Tap 仅按自报语义核验")
            return self.guard.check(action)
        verdict = self.guard.check(action, ui_texts=ui_texts)
        if ui_texts and verdict.allowed:
            self._log(f"[控件核验] 点击目标文本: {ui_texts}")
        return verdict

    def _note_failure(self, kind: str, detail: str) -> None:
        """登记一次动作级失败：反馈模型 + 连续失败达到阈值则终止。"""
        self._fail_streak += 1
        self._feedback(f"[ACTION_FAILED] {detail}。请根据当前屏幕状态重新决策。")
        self._log(f"[动作失败 {self._fail_streak}/{self.config.nav_device_failures}] {detail}")
        if self._fail_streak >= self.config.nav_device_failures:
            raise _AbortNavigation(kind, f"连续 {self._fail_streak} 次动作失败: {detail}")

    def _feedback(self, text: str) -> None:
        """把护栏/失败/核验反馈以纯文本 user 消息注入对话历史。"""
        self.context.append({"role": "user", "content": [{"type": "text", "text": text}]})

    # ---------------------------------------------------------------- verify

    def _verify_target(self, task: str) -> tuple[bool, str]:
        """导航完成后核验当前页面是否真的满足目标，防止采错页面。"""
        screenshot = adb.capture(self.device_id)
        current_app = adb.get_current_app(self.device_id)
        self._last_app = current_app
        content = self._ask([{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{screenshot_b64(screenshot)}"}},
                {"type": "text",
                 "text": NAV_VERIFY_PROMPT.format(task=task, current_app=current_app)},
            ],
        }], max_tokens=1024)
        satisfied, reason = _parse_verdict(content)
        self._log(f"[目标核验] {'通过' if satisfied else '未通过'}: {reason}")
        if satisfied is None:
            return False, f"核验输出无法解析: {reason}"
        return satisfied, reason

    # ----------------------------------------------------------------- model

    def _ask(self, messages: list[dict[str, Any]], max_tokens: int) -> str:
        kwargs: dict[str, Any] = {"extra_body": self.extra_body} if self.extra_body else {}
        response = self.client.chat.completions.create(
            model=self.config.nav_model,
            messages=messages,
            max_tokens=max_tokens,  # autoglm-phone 等模型上限 4096
            temperature=0.0,
            **kwargs,
        )
        return response.choices[0].message.content or ""


# -------------------------------------------------------------------- utils

def screenshot_b64(screenshot: adb.Screenshot) -> str:
    import base64

    return base64.b64encode(screenshot.png_bytes).decode("ascii")


def _assistant_record(content: str) -> dict[str, Any]:
    """把模型原始输出整理为写入历史的 assistant 消息。"""
    match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
    thinking = match.group(1).strip() if match else ""
    return {"role": "assistant", "content": f"<think>{thinking}</think><answer>{content.strip()}</answer>"}


def _strip_images(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("content"), list):
        message["content"] = [p for p in message["content"] if p.get("type") == "text"]
    return message


def _denormalize(point: list[int], device_id: str | None) -> tuple[int, int]:
    """把 0-999 归一化坐标换算成真实像素，并夹紧到屏幕范围内。"""
    width, height = adb.get_screen_size(device_id)
    x = min(max(int(point[0] / 999 * width), 0), max(width - 1, 0))
    y = min(max(int(point[1] / 999 * height), 0), max(height - 1, 0))
    return x, y


# ------------------------------------------------------------- action parse

def _parse_action(content: str) -> dict[str, Any]:
    """解析模型输出为动作字典。

    依次尝试：do(...)/finish(...) DSL（autoglm-phone 原生格式，扫描引号
    感知的参数段，支持转义引号）→ JSON 动作对象（其他模型友好）。
    坐标范围与语义合法性由 ActionGuard 在执行前校验。
    """
    text = _parseable_text(content)
    action = _try_parse_dsl(text)
    if action is None:
        action = _try_parse_json_action(text)
    if action is None:
        # 完全无法解析：当作无害的等待，让下一屏截图重新决策
        return {"kind": "do", "name": "Wait", "duration": "1 seconds"}
    return action


def _parseable_text(content: str) -> str:
    """剥离 think 块与 answer 标签，留下待解析的动作文本。"""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
    if match:
        text = match.group(1)
    return text.strip()


_ARG_RE = re.compile(r'(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|\[([^\]]*)\])')


def _try_parse_dsl(text: str) -> dict[str, Any] | None:
    idx = text.find("finish(")
    if idx != -1:
        parsed = _parse_args(_read_call(text, idx + len("finish")))
        if "message" in parsed:
            return {"kind": "finish", "message": str(parsed["message"])}

    idx = text.find("do(")
    if idx != -1:
        parsed = _parse_args(_read_call(text, idx + 2))
        name_raw = str(parsed.get("action", ""))
        action: dict[str, Any] = {
            "kind": "do",
            "name": _ACTION_ALIASES.get(name_raw.lower(), name_raw),
        }
        for key in ("element", "start", "end"):
            value = parsed.get(key)
            if isinstance(value, list) and len(value) == 2:
                action[key] = value
        for key in ("text", "app", "duration", "intent", "target_text"):
            if key in parsed:
                action[key] = parsed[key]
        return action
    return None


def _read_call(text: str, open_idx: int) -> str:
    """返回 text[open_idx]（'('）到配对 ')' 之间的参数串，引号内的括号不计。"""
    depth = 0
    in_string = False
    escaped = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
    return text[open_idx + 1:]  # 未闭合：取到末尾，交给参数解析兜底


def _parse_args(args: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for match in _ARG_RE.finditer(args):
        key = match.group(1)
        if match.group(2) is not None:
            out[key] = _unescape(match.group(2))
        else:
            nums = [int(v) for v in re.findall(r"-?\d+", match.group(3))]
            if len(nums) == 2:
                out[key] = nums
    return out


def _unescape(raw: str) -> str:
    """还原模型输出的转义字符串（\\" 等）；非法转义时原样返回。"""
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _try_parse_json_action(text: str) -> dict[str, Any] | None:
    candidates = []
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict) and "action" in data:
            return _action_from_json(data)
    return None


def _action_from_json(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("action", "")).strip().lower()
    if name == "finish":
        return {"kind": "finish", "message": str(data.get("message", ""))}

    action: dict[str, Any] = {"kind": "do", "name": _ACTION_ALIASES.get(name, name)}
    for key in ("intent", "target_text", "text", "app", "duration"):
        if key in data:
            action[key] = str(data[key])
    coord_sources = {"element": ("element", "point", "position"),
                     "start": ("start",), "end": ("end",)}
    for key, sources in coord_sources.items():
        for source in sources:
            value = data.get(source)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                try:
                    action[key] = [_coerce_number(v) for v in value]
                except (TypeError, ValueError):
                    pass
                break
    return action


def _coerce_number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise TypeError("bool 不是坐标")
    if isinstance(value, (int, float)):
        return value
    return float(value)


# ------------------------------------------------------------- verification

def _parse_verdict(content: str) -> tuple[bool | None, str]:
    """解析目标核验输出，返回 (结论, 说明)；无法解析时结论为 None。"""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    reason = " ".join(text.split())[:160]

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    match = re.match(r"(?i)\b(yes|no)\b", first_line)
    if match:
        return match.group(1).lower() == "yes", reason
    if first_line in {"是", "否"}:
        return first_line == "是", reason
    match = re.search(r"(?i)\b(yes|no)\b", text)
    if match:
        return match.group(1).lower() == "yes", reason
    return None, reason
