# app-vision-collector

**可溯源的 App 信息采集 Skill** · Traceable on-device app information collection
powered by a vision language model.

`English | 中文`

---

它通过 ADB 控制一台真实的 Android 手机，用视觉大模型（VLM）像人一样打开 App、
搜索、翻页、截图，并把屏幕内容抽取为结构化数据。与"协议破解 / 注入 Hook"类
方案不同，本项目 **不碰 App 进程、不做逆向、不需要 Root**，只依赖屏幕截图，
因此天然适配绝大多数 App 与系统版本。

核心卖点只有一个词：**可溯源（Traceable）**。

- 每采集一屏，原始截屏 PNG 立即落盘，绝不覆盖；
- 每一条结构化信息都携带 `evidence` 指针：截图路径 + SHA-256 + 截屏时刻 + 屏号；
- `manifest.jsonl` 追加式台账记录完整事件流水，`inspect_session.py` 可离线复核
  任何一条信息"来自哪张截图、由哪个模型在什么时间提取"。

**只做信息采集。** 动作集合被限定为点击 / 滑动 / 返回 / 搜索等只读浏览操作，
不含任何发布、发送、点赞、支付等写操作。

## 功能特性

- 🤖 **VLM 双角色**：导航 Agent（Open-AutoGLM 风格 `do(action=...)` DSL）负责
  在 App 内走到目标页面；提取 Agent 负责把单屏截图转成结构化 JSON。
- 📱 **通用 App 支持**：内置微信、小红书攻略（`references/`），其他 App 按
  通用模板即可扩展。
- 🔍 **全程证据留存**：截屏 → 提取 → 去重 → 台账，结构化目录保存，一条不漏。
- 🧾 **溯源可校验**：SHA-256 完整性校验，`manifest.jsonl` + `index.json` 双层索引。
- 🔁 **智能停止**：相邻屏重复率检测 + 最大屏数上限，增量采集不重复劳动。
- 🌐 **任意 OpenAI 兼容视觉模型**：DashScope qwen3-vl-plus、智谱 GLM-4V /
  AutoGLM 等，改环境变量即可切换。

## 系统架构

```
                 ┌──────────────────────────────────────────┐
                 │            collect.py (CLI)              │
                 └───────────────┬──────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────────┐
         ▼                       ▼                           ▼
  StepAgent 导航           SessionStore 证据存储        ExtractAgent 提取
  (Open-AutoGLM DSL)       (screenshots/manifest)       (VLM, JSON)
         │                       ▲                           │
         ▼                       │                           │
  ┌─────────────┐         ┌──────┴───────┐            ┌──────┴──────┐
  │ ADB 设备层   │──────▶ │ Android 手机  │──截屏────▶ │ VLM 云端 API │
  │ tap/swipe/.. │         └──────────────┘            └─────────────┘
  └─────────────┘
```

## 设备准备：ADB 与输入法

### 1. 安装 ADB

| 系统 | 安装方式 |
|------|----------|
| macOS | `brew install --cask android-platform-tools`（或装 Android Studio 后用 `~/Library/Android/sdk/platform-tools/adb`） |
| Ubuntu/Debian | `sudo apt install adb` |
| Windows | 下载 [platform-tools](https://developer.android.com/tools/releases/platform-tools)，解压后把目录加入 PATH |

验证：`adb version` 有输出即可。若 adb 不在 PATH，可设置环境变量
`AVC_ADB=/path/to/adb` 指向可执行文件。

### 2. 手机开启 USB 调试

1. 设置 → 关于手机 → 连续点击「版本号」7 次，开启开发者模式；
2. 设置 → 系统 → 开发者选项 → 打开 **USB 调试**；
3. USB 连接电脑，在手机弹窗上允许调试授权；
4. 验证：`adb devices`，设备状态须为 `device`。

可选无线连接（拔线采集）：USB 连接状态下执行 `adb tcpip 5555`，
拔线后 `adb connect <手机IP>:5555`。

### 3. 安装 ADB Keyboard 输入法（搜索输入必需）

ADB 原生 `input text` 不支持中文，导航 Agent 需要在搜索框输入中文
（微信群名、小红书关键词）时必须借助 [AdbKeyboard](https://github.com/nicnocquee/AdbKeyboard)：

1. 下载安装：从项目 Releases 下载 apk 安装；
2. 手机启用：设置 → 系统 → 语言和输入法 → 勾选启用 **Adb Keyboard**
   （无需设为默认——采集器输入时自动切换、结束自动恢复原输入法）；
3. 验证：`adb shell ime list -s | grep adbkeyboard` 有输出。

> 未安装的影响：仅无法完成"搜索进入"类导航（Type 动作失效），纯点击浏览类采集
> 不受影响；也可手动把手机停到目标页面后用 `--no-navigate` 采集。

## 快速开始

### 0. 环境

- Python 3.10+，macOS / Linux / Windows 均可（运行采集端）
- `adb` 在 PATH 中，一台已开启 USB 调试的 Android 手机，目标 App 已登录
- 一个 OpenAI 兼容的视觉模型 API Key

```bash
git clone https://github.com/<you>/app-vision-collector.git
cd app-vision-collector
pip install -r requirements.txt

export AVC_API_KEY="sk-..."            # 或 DASHSCOPE_API_KEY / BIGMODEL_API_KEY
# export AVC_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
# export AVC_VLM_MODEL="qwen3-vl-plus" # 提取模型
# export AVC_NAV_MODEL="autoglm-phone" # 导航模型
```

### 1. 自检

```bash
python3 scripts/collect.py --check
```

### 2. 采集

```bash
# 微信群聊
python3 scripts/collect.py --app wechat --target "AI 交流群" \
  --task "进入该群聊，采集当前可见消息并向上翻页补充历史" --max-screens 20

# 小红书搜索
python3 scripts/collect.py --app xiaohongshu --target "手机摄影技巧" \
  --task "浏览搜索结果并采集笔记标题、作者、摘要" --max-screens 10

# 通用：手机手动停到目标页面，只做"截图+提取"
python3 scripts/collect.py --app generic --no-navigate \
  --task "采集当前屏幕上的信息" --max-screens 5
```

### 3. 查看与校验

```bash
python3 scripts/inspect_session.py collections/2026-09-27/<session-id>

jq '.items[0]' collections/2026-09-27/<session-id>/index.json
```

一条信息的溯源样例（`index.json`）：

```json
{
  "item_id": "itm_000007",
  "app": "wechat",
  "type": "message",
  "sender": "王小明",
  "text": "明晚八点健身房见",
  "time_hint": "2026年09月27日 20:41",
  "evidence": {
    "screenshot": "screenshots/screen-0003.png",
    "sha256": "3f7a…e9c1",
    "captured_at": "2026-09-27T20:41:32+08:00",
    "screen_index": 3,
    "model": "qwen3-vl-plus",
    "prompt_version": "extract-v1"
  }
}
```

## 采集目录规范

```
collections/
└── 2026-09-27/
    └── 20260927-153000_wechat_AI交流群/
        ├── session.json       # 会话元数据
        ├── screenshots/       # 原始截屏（证据本体，只增不改）
        ├── extracted/         # 每屏提取的原始模型输出
        ├── manifest.jsonl     # 追加式事件台账
        └── index.json         # 去重后的条目目录（含 evidence 指针）
```

完整字段规范见 [docs/data-layout.md](docs/data-layout.md)。

## 项目结构

```
app-vision-collector/
├── SKILL.md                 # Skill 定义（agent 入口）
├── README.md
├── requirements.txt
├── scripts/
│   ├── collect.py           # 采集 CLI
│   ├── inspect_session.py   # 溯源校验 CLI
│   └── collector/           # 核心包
│       ├── adb/             # ADB 设备层（截图/输入/连接）
│       ├── agent/           # StepAgent 导航 + ExtractAgent 提取
│       ├── store/           # SessionStore 证据存储
│       └── config.py
├── references/              # 各 App 采集攻略（按需阅读）
├── docs/                    # 架构 / 数据规范 / 合规
├── examples/                # 示例 session（离线生成）
└── tests/                   # 无设备可跑的单元测试
```

## 合规与免责声明

本项目仅用于 **个人已授权账号内数据** 的采集与整理（例如备份自己的聊天记录、
整理自己浏览过的笔记）。使用时你必须：

1. 遵守目标 App 的用户协议与robots/反爬政策，自行评估账号风险；
2. 遵守所在司法辖区的个人信息保护法律（如中国《个人信息保护法》、GDPR），
   不采集、不存储、不传播他人隐私信息；
3. 不将本项目用于大规模商业化爬取、骚扰、诈骗等任何违法或侵权用途。

本项目按 MIT 许可证开源，作者不对使用者的具体用法承担责任。
详见 [docs/compliance.md](docs/compliance.md)。

## License

[MIT](LICENSE)
