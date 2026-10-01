---
name: app-vision-collector
description: >-
  Collect on-screen information from any mobile app (WeChat, Xiaohongshu/RED,
  Douyin, Taobao, etc.) using an ADB-controlled Android phone and a vision
  language model, with full screenshot evidence and provenance for every item.
  Use whenever the user asks to 采集/抓取/收集 app 内信息、群聊消息、笔记、帖子、
  评论、商品列表 etc., wants traceable/evidence-backed extraction from a phone
  screen, or mentions screenshot-based data collection from Android apps.
  Collection only — never posts, sends, likes, or modifies anything.
---

# App Vision Collector — 可溯源的 App 信息采集

通过 ADB 控制一台真实 Android 手机，用视觉大模型（VLM）像人一样浏览 App、
翻页截图、识别内容。每一条采集到的信息都关联到本地留存的原始截屏（含 SHA-256），
做到 **信息可溯源、证据可校验**。**只做信息采集**：本 skill 的所有动作都是
只读浏览（点击、滑动、返回），禁止任何发布、发送、点赞、购买等写操作。

## 何时使用

- 用户要求采集某个 App 内的信息（微信群聊消息、小红书笔记、评论区、商品列表等）
- 用户强调"要可溯源 / 要有依据 / 要能对上截图"
- 用户希望基于手机截屏做结构化数据抽取

## 前置条件（逐项确认，缺一不可）

1. `adb` 已安装且一台 Android 手机已连接（`adb devices` 可见，已开启 USB 调试）
2. 手机上目标 App 已登录且处于可用状态
3. 已设置环境变量（任选一个 OpenAI 兼容的视觉模型服务）：
   - `AVC_API_KEY`（或 `DASHSCOPE_API_KEY` / `BIGMODEL_API_KEY`）
   - 可选：`AVC_API_BASE`（默认 DashScope 兼容地址）、`AVC_VLM_MODEL`（默认 `qwen3-vl-plus`）、
     `AVC_NAV_MODEL`（默认 `autoglm-phone`，用于 UI 导航）
4. Python 3.10+，依赖：`pip install -r "<skill_dir>/requirements.txt"`（openai / Pillow）

## 标准工作流

**路径约定**：本 skill 可能安装在任何位置，执行时当前目录通常是你自己的
项目目录。下文所有 `<skill_dir>` 指本 SKILL.md 所在的 skill 安装目录——
调用脚本一律用基于它的绝对路径（如
`python3 "<skill_dir>/scripts/collect.py"`），路径加引号，不要假设当前目录就是 skill 目录。

### 1. 检查设备与能力

```bash
adb devices                      # 确认设备在线
python3 "<skill_dir>/scripts/collect.py" --check   # 自检：ADB、模型 API、依赖
```

### 2. 选择采集攻略

按目标 App 阅读 `<skill_dir>/references/` 下对应攻略，再开始采集：

- `references/wechat.md` — 微信（群聊/单聊消息）
- `references/xiaohongshu.md` — 小红书（搜索结果、笔记、评论）
- `references/generic-app.md` — 通用流程模板（其他 App 先读这个）

攻略里规定了导航任务描述怎么写、滚动节奏、提取字段建议。没有对应攻略的 App，
按 generic-app.md 的模板现写一段再执行。

### 3. 执行采集

显式 `--task` 会**原样**作为导航与提取任务，脚本不会再把 `--app`/`--target`
拼进去。因此任务描述必须自包含：写明打开哪个 App、进入哪个群/搜索什么词，
以及采集范围。采集阶段的手指方向由 `--scroll-direction auto/up/down` 控制，
不会从任务文字推断：`auto` 对微信下滑采集历史，其他 App 上滑；在微信历史
位置采集更新消息或采集朋友圈时，显式用 `--scroll-direction up`。

```bash
# 例：采集微信群「AI 交流群」的聊天消息
python3 "<skill_dir>/scripts/collect.py" \
  --app wechat \
  --target "AI 交流群" \
  --task "打开微信，进入群聊「AI 交流群」，采集当前可见的聊天消息，再向历史方向翻页采集更早的消息" \
  --max-screens 20

# 例：采集小红书搜索结果
python3 "<skill_dir>/scripts/collect.py" \
  --app xiaohongshu \
  --target "手机摄影技巧" \
  --task "打开小红书，在搜索框搜索「手机摄影技巧」，进入搜索结果页后逐屏浏览并采集笔记标题、作者、摘要" \
  --max-screens 10
```

脚本会自动：进入目标页面 → 每屏截图留存 → VLM 结构化提取 → 去重 →
滚动到下一屏，直到出现"无新内容"或达到 `--max-screens`。全程输出落到
一个 session 目录（见下）。

### 4. 校验溯源

```bash
python3 "<skill_dir>/scripts/inspect_session.py" "collections/<session-dir>"
```

重新计算每张截图的 SHA-256，与 manifest.jsonl 中的记录比对，打印每屏的
采集统计。校验失败（截图被改动/缺失）会以非零码退出。

## 证据目录结构（务必保持）

每个采集 session 一个独立目录，截屏为原始证据，永不覆盖：

```
collections/
└── 2026-09-27/
    └── 20260927-153000_wechat_AI交流群/
        ├── session.json          # 会话元数据：设备、模型、任务、时间
        ├── screenshots/          # 原始截屏（PNG，文件名即证据编号）
        │   ├── screen-0001.png
        │   └── screen-0002.png
        ├── extracted/            # 每屏的结构化提取结果（模型原始输出）
        │   ├── screen-0001.json
        │   └── screen-0002.json
        ├── manifest.jsonl        # 追加式台账：截图/条目事件的完整流水
        └── index.json            # 收尾汇总：去重后的条目目录 + 溯源指针
```

`index.json` 中每条信息的 `evidence` 字段指向截图路径、SHA-256、截屏时间、
屏号与提取模型——这就是"可溯源"的落点。详细规范见 `docs/data-layout.md`。

## 硬性约束（不可绕过）

1. **只读采集**：导航动作集合不含发送/发布/点赞/删除等写原语；每个动作
   执行前经 ActionGuard 三层判定——坐标合法性、模型自报语义
   （intent/target_text）、uiautomator 控件树独立核验（点击坐标处真实
   控件文本，与模型自报无关）；不要为了"采得更全"而给 agent 增加写
   操作指令或放宽护栏关键词。
2. **截屏先行**：先存截图、后做提取；任何信息项没有对应截图就不允许进入 index。
3. **证据不可变**：session 目录一旦生成，不修改历史截图与 manifest，只追加。
4. **合规使用**：仅采集用户自己有权访问的账号与数据，遵守目标 App 用户协议与
   适用法律（个人信息保护等），不得用于爬取他人隐私或批量商业化。详见
   `docs/compliance.md`。

## 排错

- 截图全黑 / `is_sensitive`：目标页面是敏感页（支付等），换页面重试。
- 导航 agent 反复失败：降低 `--nav-steps`、检查 App 是否强制定位到引导页；
  也可以手动把手机停到目标页面后用 `--no-navigate` 只做采集。
- 提取 JSON 解析失败：脚本自动重试 3 次；若仍失败，该屏的截图与错误已记录在
  manifest 中，可换更强的 VLM（`AVC_VLM_MODEL`）后重跑。
