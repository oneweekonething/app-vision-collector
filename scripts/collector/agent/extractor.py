"""ExtractAgent：单屏截图 → 结构化 JSON（VLM 一次调用）。

只负责"一屏"的提取；跨屏去重、编号与台账由 SessionStore 完成。
解析失败先做一次纯文本修复（不带截图、便宜），修复无效才带图重试（贵）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from collector import adb
from collector.agent.prompts import REPAIR_PROMPT, build_extract_prompt
from collector.agent.step_agent import screenshot_b64
from collector.config import CollectorConfig


class ExtractionError(RuntimeError):
    """模型输出无法解析为合法 JSON。"""

    def __init__(self, message: str, last_raw: str = ""):
        super().__init__(message)
        self.last_raw = last_raw  # 最后一次带图调用的原始输出，供失败留证


class ExtractAgent:
    """视觉提取智能体。"""

    def __init__(self, config: CollectorConfig, app: str, task: str):
        self.config = config
        self.app = app
        self.task = task
        self.prompt = build_extract_prompt(app, task)
        self.client = OpenAI(base_url=config.api_base, api_key=config.api_key)

    def extract(self, screenshot: adb.Screenshot) -> dict[str, Any]:
        """对一屏截图做结构化提取，返回 {"screen_summary", "items", ...}。

        解析失败先纯文本修复；修复也无效才带图重试。

        Raises:
            ExtractionError: 重试用尽后仍无法解析。
        """
        last_error: Exception | None = None
        last_raw = ""
        for attempt in range(1, self.config.extract_retries + 1):
            response = self.client.chat.completions.create(
                model=self.config.vlm_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64(screenshot)}"}},
                        {"type": "text", "text": self.prompt},
                    ],
                }],
                temperature=0.1,
                max_tokens=self.config.extract_max_tokens,  # 推理模型 thinking 也计入预算
            )
            raw = response.choices[0].message.content or ""
            last_raw = raw
            try:
                data = _parse_json(raw)
                repaired_raw = None
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                print(f"[提取] 第 {attempt} 次解析失败: {exc}，先试纯文本修复…")
                repaired_raw, data = self._repair(raw, exc)
                if data is None:
                    print("[提取] 纯文本修复无效，带图重试…")
                    continue

            data["items"] = [item for item in data.get("items", []) if str(item.get("text", "")).strip()]
            data["message_count"] = len(data["items"])
            data["raw_response"] = raw  # 证据以带图调用的原始输出为准
            if repaired_raw:
                data["repair_response"] = repaired_raw
            return data

        raise ExtractionError(
            f"JSON 解析失败（已重试 {self.config.extract_retries} 次）: {last_error}", last_raw=last_raw)

    def _repair(self, raw: str, error: Exception) -> tuple[str | None, dict[str, Any] | None]:
        """纯文本修复：把失败输出（不带截图）再喂给模型修成合法 JSON。

        Returns:
            (修复调用的原始输出, 解析结果)；修复无效时为 (None, None)。
        """
        try:
            response = self.client.chat.completions.create(
                model=self.config.vlm_model,
                messages=[{"role": "user", "content": REPAIR_PROMPT.format(error=error, raw=raw)}],
                temperature=0.0,
                max_tokens=self.config.extract_max_tokens,
            )
            repaired_raw = response.choices[0].message.content or ""
            return repaired_raw, _parse_json(repaired_raw)
        except Exception:
            return None, None


def _parse_json(text: str) -> dict[str, Any]:
    """解析模型输出；容忍 ```json 代码围栏与前后杂质。"""
    text = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 截取第一个 { 到最后一个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("输出中找不到 JSON 对象")

    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("JSON 缺少 items 字段")
    return data
