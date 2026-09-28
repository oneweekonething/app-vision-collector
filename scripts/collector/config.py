"""运行配置：全部来自环境变量，CLI 参数可覆盖。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_VLM_MODEL = "qwen3-vl-plus"
DEFAULT_NAV_MODEL = "autoglm-phone"


@dataclass
class CollectorConfig:
    """采集器运行参数。"""

    api_base: str = DEFAULT_API_BASE
    api_key: str = ""
    vlm_model: str = DEFAULT_VLM_MODEL
    nav_model: str = DEFAULT_NAV_MODEL
    data_dir: Path = field(default_factory=lambda: Path("collections"))
    max_screens: int = 20
    nav_steps: int = 20
    scroll_pause: float = 1.2
    extract_retries: int = 3
    # GLM-5.x 等强制思考模型推理可达 9k+ token，预算需给足（T1-T3 简单屏 6k 够用，
    # 密集群聊截图实测 9284）
    extract_max_tokens: int = 16000
    # 导航模型单独限额：autoglm-phone 上限 4096
    nav_max_tokens: int = 4096
    # 提取容错：单屏失败留证据后跳过，连续 N 屏失败才终止会话
    max_extract_failures: int = 2
    # 停止条件：连续 N 屏没有新信息，且画面无位移（或用尽宽限屏数）才停止
    no_new_stop_streak: int = 2
    no_new_grace_screens: int = 2     # 连续无新内容时的额外宽限屏数
    stuck_hash_distance: int = 4      # 64bit 感知哈希汉明距离 ≤ 此值视为画面未移动
    # 导航：finish 后做一次目标页核验；连续设备/动作失败与护栏拦截的终止阈值
    nav_verify: bool = True
    nav_device_failures: int = 3
    nav_guard_denials: int = 3
    # Tap 前用 uiautomator 控件树独立核验点击目标（dump 失败自动降级）
    nav_ui_verify: bool = True

    @classmethod
    def from_env(cls) -> CollectorConfig:
        """从环境变量构建配置。

        Key 的查找顺序：AVC_API_KEY > DASHSCOPE_API_KEY > BIGMODEL_API_KEY >
        ZHIPU_API_KEY > OPENAI_API_KEY，方便直接复用已有的大模型账号。
        """
        api_key = (
            os.environ.get("AVC_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("BIGMODEL_API_KEY")
            or os.environ.get("ZHIPU_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        return cls(
            api_base=os.environ.get("AVC_API_BASE", DEFAULT_API_BASE),
            api_key=api_key,
            vlm_model=os.environ.get("AVC_VLM_MODEL", DEFAULT_VLM_MODEL),
            nav_model=os.environ.get("AVC_NAV_MODEL", DEFAULT_NAV_MODEL),
            data_dir=Path(os.environ.get("AVC_DATA_DIR", "collections")),
            max_screens=int(os.environ.get("AVC_MAX_SCREENS", "20")),
            nav_steps=int(os.environ.get("AVC_NAV_STEPS", "20")),
            scroll_pause=float(os.environ.get("AVC_SCROLL_PAUSE", "1.2")),
            nav_verify=os.environ.get("AVC_NAV_VERIFY", "1") not in {"0", "false", "no"},
            nav_ui_verify=os.environ.get("AVC_NAV_UI_VERIFY", "1") not in {"0", "false", "no"},
        )
