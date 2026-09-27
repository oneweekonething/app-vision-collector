# 通用 App 采集攻略（模板）

没有专门攻略的 App（抖音、微博、淘宝、Plain 浏览器里的任何列表页…）
按这个模板操作。

## 三种采集模式

### 模式 A：手动定位 + 只采当前屏（最稳）

1. 手机上手动打开 App、走到目标页面；
2. `python3 scripts/collect.py --app <app> --no-navigate --no-scroll \
   --task "描述这一屏要采集什么"`；
3. 每跑一次得一屏数据，适合精准、低频采集。

### 模式 B：手动定位 + 自动翻页

1. 手动走到目标列表页；
2. `--no-navigate --max-screens N`，脚本自动逐屏截取并翻页。

### 模式 C：全自动导航（需要调试）

1. 在 `collector/agent/prompts.py` 的 `APP_NAV_TEMPLATES` 里加一条该 App 的
   导航任务模板；
2. 在 `APP_EXTRACT_HINTS` 里补充界面语义说明（气泡方向、卡片构成等），
   这是提升提取准确率最有效的一步；
3. 用 `--verbose` 观察 StepAgent 决策过程，逐步修模板。

## 提取字段约定（通用）

- `type`：message / post / comment / product / profile / search_result / other
- `title`：条目标题；`sender`：作者/发送者；`text`：正文
- `extra`：一切可见的结构化副信息（数量、价格、标签…）
- `bbox`：让模型给出条目大致位置，便于人工在截图上核对

## 停止条件与节奏

- 默认连续 2 屏无新条目即停止；信息流类 App 建议保留默认值。
- 翻页间隔 `AVC_SCROLL_PAUSE`（默认 1.2s）；对风控严格的 App 建议调到 2s+。
- 任何情况下都不要为绕过 App 风控而改造本项目（增加拟人轨迹、多开等
  均不在本项目范围内）。
