"""Agent 层：StepAgent（UI 导航）与 ExtractAgent（内容提取）。"""

from collector.agent.extractor import ExtractAgent, ExtractionError
from collector.agent.step_agent import StepAgent

__all__ = ["ExtractAgent", "ExtractionError", "StepAgent"]
