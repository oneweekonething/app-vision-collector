"""collector: 可溯源的 App 信息采集核心包。

分层结构:
- adb/    ADB 设备层（截屏、输入、连接）
- agent/  导航 Agent（Open-AutoGLM 风格）与提取 Agent（VLM 结构化）
- store/  SessionStore 证据存储（截屏留存 + manifest 台账 + index 索引）
"""

__version__ = "0.1.0"
PROMPT_VERSION = "extract-v1"
