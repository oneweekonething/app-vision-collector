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
        # GLM-5.x 等强制思考的模型：无法关闭 thinking，只能调低档位，
        # 且推理 token 计入 max_tokens（实测密集群聊截图会推理 5000-9000 token）。
        # 按模型名判定（glm-4.5v 等不认 effort 参数，不能按域名误伤）
        self.extra_body = (
            {"thinking": {"type": "enabled", "effort": "low"}}
            if "glm-5" in config.vlm_model.lower()
            else None
        )

    def _create(self, messages: list[dict], temperature: float) -> Any:
        kwargs: dict[str, Any] = {}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return self.client.chat.completions.create(
            model=self.config.vlm_model,
            messages=messages,
            temperature=temperature,
            max_tokens=self.config.extract_max_tokens,
            **kwargs
        )

    def extract(self, screenshot: adb.Screenshot) -> dict[str, Any]:
        """对一屏截图做结构化提取，返回 {"screen_summary", "items", ...}。

        解析失败先纯文本修复；修复也无效才带图重试。

        Raises:
            ExtractionError: 重试用尽后仍无法解析。
        """
        last_error: Exception | None = None
        last_raw = ""
        for attempt in range(1, self.config.extract_retries + 1):
            response = self._create(
                [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64(screenshot)}"}},
                        {"type": "text", "text": self.prompt},
                    ],
                }],
                temperature=0.1,
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

            # 空条目过滤：text 或 title 任一有效即保留——微信消息靠 text，
            # 小红书/红果等卡片可能只有 title；两者皆空才是提取噪声。
            # None 先归一为空串再判断：str(None) 是非空的 "None"，会让
            # text/title=null 的噪声条目漏进 index
            data["items"] = [
                item for item in data.get("items", [])
                if str(item.get("text") or "").strip() or str(item.get("title") or "").strip()
            ]
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
            response = self._create(
                [{"role": "user", "content": REPAIR_PROMPT.format(error=error, raw=raw)}],
                temperature=0.0,
            )
            repaired_raw = response.choices[0].message.content or ""
            return repaired_raw, _parse_json(repaired_raw)
        except Exception:
            return None, None


def _parse_json(text: str) -> dict[str, Any]:
    """解析模型输出；容忍 ```json 代码围栏、前后杂质与字符串内换行符。

    除解析外同时校验 items 的 schema（必须是对象数组、每项是对象）——
    合法 JSON 但错误 shape 的输出在这里抛 ValueError，从而进入
    纯文本修复 → 带图重试的现有容错链，而不是在下游过滤时崩溃。
    """
    text = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 截取第一个 { 到最后一个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("输出中找不到 JSON 对象")

    # strict=False：容忍模型在字符串值里输出原始换行/制表符（常见缺陷）
    data = json.loads(text[start : end + 1], strict=False)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError(f"items 必须是数组（实际 {type(items).__name__}）")
    if not all(isinstance(item, dict) for item in items):
        raise ValueError("items 中每一项必须是 JSON 对象")
    return data
