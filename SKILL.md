---
name: app-vision-collector
description: >-
  Collect information visible in Android apps (chat messages, notes, posts,
  comments, product lists) through ADB with screenshot evidence for every item.
  Uses the invoking assistant's own vision capability, without an API key or
  separate model service. Use for 采集/收集 app 内信息 or traceable extraction
  from phone screens. Read-only collection; requires an image-capable caller.
---

# App Vision Collector — 可溯源的 App 信息采集

直接使用**调用者自身的视觉与推理能力**看图、决定导航动作、提取内容。
Python 脚本只负责 ADB 操作、截图、护栏、去重和证据存储，**不调用模型 API，
不需要 API Key、模型端点或 OpenAI SDK**。调用者必须能通过所在环境的图片
查看工具读取本地 PNG 并理解界面；仅有文本能力时，说明缺少视觉能力并停止，
不要用控件树文字代替看图，也不要让用户配置 Key。

## 前置条件

- 调用者能打开本地图片并进行视觉识别。
- Python 3.10+；安装 `requirements.txt`（仅 Pillow）。
- `adb` 可用，Android 手机已开启 USB 调试并在线；目标 App 已登录。
- 中文搜索输入需要手机安装 ADBKeyBoard；也可由用户手动定位后开始看图采集。

`<skill_dir>` 始终指本 SKILL.md 所在的绝对目录。脚本路径、会话路径都加引号，
不要假设工作目录在 skill 内。

## 工作流

先读 [references/caller-workflow.md](references/caller-workflow.md)，了解命令和
动作/提取 JSON；再按 App 选择 [微信攻略](references/wechat.md)、
[小红书攻略](references/xiaohongshu.md) 或 [通用攻略](references/generic-app.md)。

1. 用 `caller_collect.py check` 检查本地依赖与设备；调用者自己确认视觉能力，
   设备自检不能证明模型能看图。
2. 用 `start` 创建会话。任务写明 App、目标页面、字段和采集范围；默认
   `--max-screens 20 --max-actions 40`，按用户范围调整。
3. 用 `observe` 获取导航截图，**实际打开返回的 PNG**，根据画面选择只读动作。
   将动作 JSON 写到会话目录外的临时文件，交 `action` 执行；每次动作后重新
   observe、看图，不沿用旧坐标。手动定位的页面也必须看图核验。
4. 看图确认已到正确目标页后用 `capture` 保存采集证据。**打开这次 capture
   返回的那张 PNG**，依据返回的提取提示词，由调用者直接生成 JSON；写到外部
   临时文件，再用 `record --result-file` 登记。不要用上一张导航图提取。
5. 需要继续时，提交近垂直 Swipe，再 observe → 看图 → capture → 看图 → record。
   微信历史用手指下滑，信息流下一屏用手指上滑，具体方向服从用户范围。
   下一步翻页前必须登记上一屏结果或 `record --error`，避免遗漏证据。
6. 达到用户范围/屏数上限即 finish；连续两屏没有新增且画面没有继续位移时，
   用 `--reason no_new_items` 收尾。只因零新增而画面仍在变化，不认定已到边界。
   连续三次动作失败/护栏拒绝或提取失败时停止并记录原因；不要无限重试。
7. 用 `inspect_session.py "<session_dir>"` 校验证据，向用户提供目录、条目/屏数、
   停止原因与失败屏数。部分成功要明确说明，校验通过不等于内容识别完全准确。

## 证据与只读约束

- 所有设备动作交给 `caller_collect.py action`，不要直接用 ADB 绕过护栏。
  仅在已确认的搜索框使用 Type；禁止发送、发布、点赞、购买、删除等动作。
  Tap 会核验真实控件文本；控件树不可用时拒绝 Tap，不允许降级放行。
- 截图先落盘再提取；不捏造不可见内容、发送者或时间，模糊信息标低置信度。
- 脚本生成 `evidence`，调用者无需编造哈希、屏号或条目 ID。
- 会话命令串行执行；历史截图、提取文件、元数据和台账不手工修改。
  已收尾会话不可继续，重新采集用新会话。
- 只采集用户授权范围内可访问的数据。详见 [数据规范](docs/data-layout.md)
  和 [使用指引](docs/compliance.md)。

导航失败时 `finish --reason navigation_failed --error "原因"`，保持 0 张采集截图；
导航观察图单独留在 `navigation/`。提取 JSON 有误时先修正外部临时文件再 record；
无法识别则记录失败，保留原截图。中断后可对未收尾会话继续执行命令，脚本会
从台账恢复去重状态；损坏或已收尾的会话会拒绝恢复。

旧 `collect.py` 仅保留为[可选 API 兼容入口](docs/api-compatibility.md)，只有用户
明确选择独立 API 自动化时才使用，不作为当前 skill 的默认流程。
