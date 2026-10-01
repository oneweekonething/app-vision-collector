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

**只做信息采集。** 只读约束有三层：导航动作集合只含点击 / 滑动 / 返回 /
搜索等浏览原语；每个动作执行前经过 **ActionGuard 语义护栏**——模型随
动作申报意图（intent）与目标元素文字（target_text），命中"发送 / 点赞 /
支付 / 删除 / 授权"等写操作语义即拒绝执行、反馈模型重新规划；Tap 还会
经 **uiautomator 控件树独立核验**——读取点击坐标处真实控件的文本再查
一次关键词，与模型自报无关，模型把"发送"按钮谎报成普通按钮也拦得住
（控件树不可用的页面自动降级为仅自报判定并记录日志）。Swipe 被限制为
近垂直滚动——横向滑动会触发列表项删除 / 滑块确认等写操作，一律拒绝。
连续拦截则终止导航。这是纵深防御而非形式化验证——动作集合不含写原语
正是最后的兜底。

## 功能特性

- 🤖 **VLM 双角色**：导航 Agent（`do(action=...)` DSL，兼容 JSON 动作）负责
  在 App 内走到目标页面；提取 Agent 负责把单屏截图转成结构化 JSON。
- 📱 **通用 App 支持**：内置微信、小红书攻略（`references/`），其他 App 按
  通用模板即可扩展。
- 🛡️ **只读护栏 + 导航核验**：ActionGuard 拦截写操作语义的点击（坐标
  合法性校验 + 模型自报语义 + uiautomator 控件树独立核验三层判定）；
  导航返回结构化结果（成功 / 失败原因 / 步数 / 当前 App），finish 后由
  模型核验"当前页是否满足目标"，导航失败立即终止会话——不把错误页面
  的数据当成合法证据入库。
- 🔍 **全程证据留存**：截屏 → 提取 → 去重 → 台账，结构化目录保存，一条不漏。
- 🧾 **溯源可校验**：SHA-256 完整性校验，`manifest.jsonl` + `index.json` 双层索引；
  所有落盘走 tmp + fsync + 原子 rename，manifest 逐条 fsync，进程中断不留半个文件。
- 🔁 **智能停止**：内容零新增 + 画面位移消失（感知哈希）双重判据 + 宽限屏数
  + 最大屏数上限——长图 / 大卡片跨屏不再被误判为"已到边界"。
- 🧹 **按 App 形态去重**：微信聊天用滑窗去重（同文本出窗后视为真实重复，
  不误杀"张三：收到"×2）；红果免费短剧 / 小红书等信息流用全局去重
  （同内容远距重现即重复曝光）。`--dedup auto` 按 App 自动判定，可显式
  指定 `chat` / `global`。
- 🌐 **任意 OpenAI 兼容视觉模型**：提取与导航模型均可通过环境变量配置，
  填入任意支持视觉的模型即可。

## 系统架构

```
                 ┌──────────────────────────────────────────┐
                 │            collect.py (CLI)              │
                 └───────────────┬──────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────────┐
         ▼                       ▼                           ▼
  StepAgent 导航           SessionStore 证据存储        ExtractAgent 提取
  (DSL/JSON 解析           (screenshots/manifest         (VLM, JSON)
   + ActionGuard 护栏        /原子落盘)
   + 目标页核验)                   ▲                           │
         │                       │                           │
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

### 3. 安装 ADBKeyBoard 虚拟键盘（中文搜索输入必需）

ADB 原生的 `input text` 只支持 ASCII，输入中文会直接失败：

```bash
adb shell input text '你好'   # ✗ 不支持 Unicode
```

导航 Agent 需要在搜索框输入中文（微信群名、小红书关键词）时，借助
[ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)——一个专为自动化测试设计的
**虚拟键盘输入法**。它没有可见的键盘界面，而是常驻监听系统广播：adb 发一条
广播，它就把广播携带的文本"敲"进当前聚焦的输入框，因此能输入中文、Emoji
等任意 Unicode，而采集器的输入动作与人工操作在 App 看来完全一致。

**安装与启用：**

1. 下载 APK：从 [Releases](https://github.com/senzhk/ADBKeyBoard/releases) 下载
   （Android 16 设备选 v2.5-dev，其余选 v2.4-dev），执行
   `adb install ADBKeyboard.apk`；也可源码构建（`./gradlew installDebug`）；
2. 启用输入法（二选一）：
   - 手机上：设置 → 系统 → 语言和输入法 → 勾选启用 **ADBKeyBoard**；
   - 命令行：`adb shell ime enable com.android.adbkeyboard/.AdbIME`。

   无需设为默认——采集器以事务方式输入：输入前自动切换到 ADBKeyBoard，
   输入结束或失败后自动恢复原输入法；
3. 验证：`adb shell ime list -s | grep adbkeyboard` 有输出。

**工作原理（采集器 `input_text_safe()` 的三步输入事务）：**

```bash
# 1. 切换到 ADBKeyBoard，并记下原输入法 id
adb shell ime set com.android.adbkeyboard/.AdbIME
# 2. 文本 base64 编码后经广播提交到聚焦的输入框（绕开 adb 对 UTF-8 的限制）
adb shell am broadcast -a ADB_INPUT_B64 --es msg "$(printf 'AI交流群' | base64)"
# 3. 恢复原输入法
adb shell ime set <原输入法ID>
```

ADBKeyBoard 还提供其他广播动作，手工调试时可用（完整说明见其 README）：

| 广播动作 | 参数 | 用途 |
|----------|------|------|
| `ADB_INPUT_B64` | `--es msg <base64>` | 任意 Unicode 文本（本项目使用） |
| `ADB_INPUT_TEXT` | `--es msg 'text'` | 明文文本（Android 8+ 的 adb 不再接受 UTF-8 参数，故不采用） |
| `ADB_INPUT_CODE` | `--ei code 67` | 按键码（如 67 = 退格） |
| `ADB_INPUT_CHARS` | `--eia chars '128568,32'` | Unicode 码点，可输入 Emoji |
| `ADB_EDITOR_CODE` | `--ei code 2` | 编辑动作（如 2 = IME_ACTION_GO） |
| `ADB_CLEAR_TEXT` | — | 清空输入框 |

> 未安装的影响：仅无法完成"搜索进入"类导航（Type 动作会失败并把原因反馈给
> 导航模型），纯点击浏览类采集不受影响；也可手动把手机停到目标页面后用
> `--no-navigate` 采集。

## 快速开始

### 0. 环境

- Python 3.10+，macOS / Linux / Windows 均可（运行采集端）
- `adb` 在 PATH 中，一台已开启 USB 调试的 Android 手机，目标 App 已登录
- 一个 OpenAI 兼容的视觉模型 API Key

```bash
git clone https://github.com/<you>/app-vision-collector.git
cd app-vision-collector
pip install -r requirements.txt

export AVC_API_KEY="sk-..."            # 任意 OpenAI 兼容视觉模型服务的 API Key
# export AVC_API_BASE="https://<openai-compatible-endpoint>/v1"
# export AVC_VLM_MODEL="<vision-model>" # 提取模型，任意支持视觉的模型即可
# export AVC_NAV_MODEL="<vision-model>" # 导航模型，任意支持视觉的模型即可
```

### 1. 自检

```bash
python3 scripts/collect.py --check
```

### 2. 采集

```bash
# 微信群聊（--task 会原样作为导航任务，必须写全 App 与目标；
# 微信翻历史由采集器自动用手指下滑完成）
python3 scripts/collect.py --app wechat --target "AI 交流群" \
  --task "打开微信，进入群聊「AI 交流群」，采集当前可见的聊天消息，再向历史方向翻页采集更早的消息" \
  --max-screens 20

# 微信当前历史位置向更新消息采集，或浏览朋友圈：显式覆盖手指方向
python3 scripts/collect.py --app wechat --no-navigate --scroll-direction up \
  --task "采集当前页面的信息并继续浏览下方内容" --max-screens 10

# 小红书搜索
python3 scripts/collect.py --app xiaohongshu --target "手机摄影技巧" \
  --task "打开小红书，搜索「手机摄影技巧」并进入搜索结果页，逐屏浏览并采集笔记标题、作者、摘要" \
  --max-screens 10

# 红果免费短剧等信息流 App：全局内容去重（auto 即可自动判定）
python3 scripts/collect.py --app 红果免费短剧 --no-navigate \
  --task "采集当前榜单的短剧标题与作者" --max-screens 10

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
    "model": "<vision-model>",
    "prompt_version": "extract-v1"
  }
}
```

## 采集目录规范

```
collections/
└── 2026-09-27/
    └── 20260927-153000-483921-a1b2c3d4_wechat_AI交流群/
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
│       ├── adb/             # ADB 设备层（截图/输入/连接/输入法事务）
│       ├── agent/           # StepAgent 导航 + ExtractAgent 提取 + ActionGuard 护栏
│       ├── store/           # SessionStore 证据存储 + 滑窗去重
│       ├── imaging.py       # 感知哈希（停止策略的画面位移判据）
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
