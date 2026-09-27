"""StepAgent：Open-AutoGLM 风格的 UI 导航智能体。

循环：截屏 → 视觉模型决策（do(action=...)/finish(message=...)）→ 正则解析
→ ADB 执行 → 剥离历史中的图片以节省上下文 → 下一屏。

动作集合刻意收窄为只读浏览（Tap/Swipe/Type/Back/Home/Launch/Wait），
从机制上杜绝导航过程中的任何写操作。
"""

from __future__ import annotations

import re
import time
from typing import Any

from openai import OpenAI

from collector import adb
from collector.agent.prompts import build_nav_system_prompt
from collector.config import CollectorConfig


class StepAgent:
    """导航智能体。"""

    def __init__(self, config: CollectorConfig, device_id: str | None = None, verbose: bool = False):
        self.config = config
        self.device_id = device_id
        self.verbose = verbose
        self.max_steps = config.nav_steps
        self.client = OpenAI(base_url=config.api_base, api_key=config.api_key)
        # GLM-5.x 强制思考模型：调低思考档位（按模型名判定，详见 extractor）
        self.extra_body = (
            {"thinking": {"type": "enabled", "effort": "low"}}
            if "glm-5" in config.nav_model.lower()
            else None
        )
        self.context: list[dict[str, Any]] = []
        self.step_count = 0

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def run(self, task: str) -> str:
        """执行一个导航任务，返回 finish 消息或超步说明。"""
        self.context = [{"role": "system", "content": build_nav_system_prompt()}]
        self.step_count = 0

        result = self._execute_step(task, is_first=True)
        while not result["finished"] and self.step_count < self.max_steps:
            result = self._execute_step()
        return result.get("message") or "Max steps reached"

    # ------------------------------------------------------------------ step

    def _execute_step(self, user_prompt: str | None = None, is_first: bool = False) -> dict[str, Any]:
        self.step_count += 1

        screenshot = adb.capture(self.device_id)
        current_app = adb.get_current_app(self.device_id)
        self._log(f"[导航 {self.step_count}/{self.max_steps}] 当前应用: {current_app}")

        screen_info = f"** Screen Info **\n\n{current_app}"
        text = f"{user_prompt}\n\n{screen_info}" if is_first else screen_info

        self.context.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64(screenshot)}"}},
                {"type": "text", "text": text},
            ],
        })

        kwargs: dict[str, Any] = {"extra_body": self.extra_body} if self.extra_body else {}
        response = self.client.chat.completions.create(
            model=self.config.nav_model,
            messages=self.context,
            max_tokens=self.config.nav_max_tokens,  # autoglm-phone 等模型上限 4096
            temperature=0.0,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        self._log(f"[模型决策] {content[:200]}")

        # 图片用完即弃，历史里只保留文本
        self.context[-1] = _strip_images(self.context[-1])

        action = _parse_action(content)
        self.context.append(_assistant_record(content))

        return self._execute(action)

    def _execute(self, action: dict[str, Any]) -> dict[str, Any]:
        """执行解析后的动作，返回 {finished, message}。"""
        if action["kind"] == "finish":
            return {"finished": True, "message": action.get("message", "finished")}

        name = action.get("name", "")
        try:
            if name == "Tap":
                x, y = _denormalize(action["element"], self.device_id)
                adb.tap(x, y, self.device_id)
            elif name == "Swipe":
                x1, y1 = _denormalize(action["start"], self.device_id)
                x2, y2 = _denormalize(action["end"], self.device_id)
                adb.swipe(x1, y1, x2, y2, duration_ms=500, device_id=self.device_id)
            elif name == "Type":
                adb.type_text(action.get("text", ""), self.device_id)
            elif name == "Back":
                adb.back(self.device_id)
            elif name == "Home":
                adb.home(self.device_id)
            elif name == "Launch":
                if not adb.launch_app(action.get("app", ""), self.device_id):
                    return {"finished": False, "message": f"启动失败: {action.get('app')}"}
            elif name == "Wait":
                seconds = float(re.sub(r"[^\d.]", "", action.get("duration", "1")) or 1)
                time.sleep(min(seconds, 10))
            else:
                self._log(f"[警告] 未知动作: {name}")
        except Exception as exc:  # 单步失败不终止任务，把错误留给下一轮决策
            self._log(f"[动作执行失败] {name}: {exc}")
        return {"finished": False, "message": ""}


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
    """把 0-999 归一化坐标换算成真实像素。"""
    width, height = adb.get_screen_size(device_id)
    return int(point[0] / 999 * width), int(point[1] / 999 * height)


def _parse_action(content: str) -> dict[str, Any]:
    """解析模型输出为动作字典。

    优先匹配 do(action=...) / finish(message=...) DSL，回退到 <answer> 标签。
    """
    content = content.strip()

    finish_match = re.search(r'finish\(message="(.*?)"\)', content, re.DOTALL)
    if finish_match:
        return {"kind": "finish", "message": finish_match.group(1)}

    do_match = re.search(r'do\(\s*action="([^"]+)"(.*?)\)\s*$', content, re.DOTALL | re.MULTILINE)
    if not do_match:
        # 宽松回退：整个文本里找 do(...)
        do_match = re.search(r'do\(\s*action="([^"]+)"(.*)', content, re.DOTALL)
    if do_match:
        action = {"kind": "do", "name": do_match.group(1)}
        rest = do_match.group(2)

        for key, pattern in [
            ("element", r"element=\[(\d+),\s*(\d+)\]"),
            ("start", r"start=\[(\d+),\s*(\d+)\]"),
            ("end", r"end=\[(\d+),\s*(\d+)\]"),
        ]:
            m = re.search(pattern, rest)
            if m:
                action[key] = [int(m.group(1)), int(m.group(2))]

        m = re.search(r'text="([^"]*)"', rest)
        if m:
            action["text"] = m.group(1)
        m = re.search(r'app="([^"]*)"', rest)
        if m:
            action["app"] = m.group(1)
        m = re.search(r'duration="([^"]*)"', rest)
        if m:
            action["duration"] = m.group(1)
        return action

    # 完全无法解析：当作无害的等待，让下一屏截图重新决策
    return {"kind": "do", "name": "Wait", "duration": "1 seconds"}
