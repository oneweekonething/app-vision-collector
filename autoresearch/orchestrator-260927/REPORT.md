# 真机测试报告：app-vision-collector（6 次真机采集）

- 日期：2026-09-27 · 设备：小米（MIUI）`4a9641` · 分辨率 1440×3200
- 成功谓词：6 个 session 全部通过 `inspect_session.py` 溯源校验，且 ≥4 次提取到非空条目
- **结论：`CONVERGED` — 6/6 校验通过，6/6 非空（要求 ≥4）**

## 测试矩阵

| # | 场景 | 提取模型 | 导航模型 | 屏 | 去重后条目 | 校验 |
|---|------|----------|----------|----|-----------|------|
| T1 | 通用·视频播放页（分享面板） | glm-5.3-flash | glm-5.3-flash | 2 | 24 | ✓ |
| T2 | 通用·Android 设置页 | glm-5.3-flash | glm-5.3-flash | 2 | 26 | ✓ |
| T3 | 微信·聊天列表（群发现） | glm-5.3-flash | — | 1 | 9 | ✓ |
| T4 | 微信·导航进群+消息采集 | glm-5.3-flash | glm-5.3-flash | 2 | 11 | ✓ |
| T5 | 微信·从列表导航进群（完整链路） | glm-4.5v | autoglm-phone | 2 | 18 | ✓ |
| T6 | 微信·全链路复测 | glm-4.5v | autoglm-phone | 2 | 13 | ✓ |

溯源抽样：群聊消息（"我: 做测试呢。。。"、"数字海河: 66.7 MB 已过期"）等均可
通过 `evidence.screenshot` + SHA-256 回溯到 `screenshots/screen-NNNN.png` 原图。

## 测试中发现并修复的缺陷（真机测试的价值所在）

1. **KeyError 'thinking'**：导航重写遗留 bug，首跑即崩 → 抽出 `_assistant_record`
   纯函数并补单测（tests/test_step_agent.py，现 5 例）。
2. **强制思考模型吞掉提取输出**：glm-5.3-flash 对密集群聊截图推理 5367~8383
   token，`max_tokens=6000` 下正文为 0 字 → `extract_max_tokens=16000`、
   glm-5* 自动附加 `thinking effort=low`、`json.loads(strict=False)` 容忍字符串内换行。
3. **分屏坐标错乱**：设备意外进入分屏后 0-999 归一化坐标映射失准，导航模型
   连点 19 步未进群（烧掉大量配额，是 z.ai 资源包耗尽的直接原因）→ 导航前
   自动 Home 复位（`--no-reset` 可关）+ 提示词增加分屏识别纪律。
4. **per-model token 上限**：autoglm-phone 限 4096 → 新增独立 `nav_max_tokens`。
5. **模型可用性**：qwen3-vl-plus（DashScope）对含 do/finish DSL 的 system prompt
   静默返回空 content（completion_tokens=1），未解，建议导航用 autoglm-phone /
   glm-5 系。

## 配额事件

z.ai key（glm-5.3-flash）在 T5 导航阶段耗尽资源包（1113）；BIGMODEL key 为同一
智谱账户同样耗尽。T5/T6 改用 `auto_data_local/.env` 的 `ZHIPUAI_API_KEY`
（autoglm-phone + glm-4.5v）完成。**T1-T4 为纯 glm-5.3-flash 验证，T5/T6 引擎
已在 results.tsv 标注。**

## 单元测试

25/25 通过（含新增 step_agent 解析/assistant 记录用例）。注意：并行会话在
同一工作区添加了 test_extractor.py 等测试（09:13 前后出现过 3 个瞬态 error，
为其编辑中间态，现已全绿）。

## 遗留建议

- `inspect_session.py` 可增加"0 屏会话告警"（本次空会话 101844 会 vacuous 通过）
- 离线示例截图的中文发送者名因 PIL 默认字体显示为方框（数据无影响）
- 修复代码尚未提交/推送（工作区还混有并行会话的未提交改动，建议一起评审后提交）
