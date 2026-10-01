# app-vision-collector

**可溯源的 App 信息采集 Skill** · Traceable on-device app information collection
powered by a vision language model.

`English | 中文`

---

它通过 ADB 控制一台真实的 Android 手机，直接使用调用者自身的视觉能力打开 App、
搜索、翻页、截图，并把屏幕内容抽取为结构化数据，无需 API Key。与"协议破解 / 注入 Hook"类
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
（默认流程在控件树不可用时拒绝 Tap）。Swipe 被限制为
近垂直滚动——横向滑动会触发列表项删除 / 滑块确认等写操作，一律拒绝。
调用者在连续三次失败时收尾。这些护栏仍需调用者结合截图判断动作语义。

## 功能特性

- **调用者直接看图**：使用调用 skill 的助手自身视觉能力完成导航和提取，无需 API Key、模型端点或模型 SDK；调用者必须能读取本地 PNG。
- **ADB 与只读护栏**：脚本只执行设备操作，Tap 经控件树核验；无法核验时拒绝点击，Swipe 限制为近垂直滚动。
- **截图证据与溯源**：截图先落盘，调用者生成 JSON 后登记；条目携带图片路径、SHA-256、屏号与模型标识。
- **持久会话**：命令之间从台账恢复去重状态；拒绝覆盖同屏提取结果、改写已收尾会话或在上一屏未登记时翻页。
- **按内容形态去重**：微信聊天使用滑窗去重，其他信息流默认全局去重，可用 `--dedup chat/global` 覆盖。
- **可离线校验**：`inspect_session.py` 复核截图哈希、条目来源与会话汇总。

## 系统架构

```text
调用者的视觉/推理能力（看本地 PNG → 决定动作 / 生成提取 JSON）
          │ action.json / result.json                 ▲ 截图路径与提示词
          ▼                                         │
caller_collect.py → ActionGuard → ADB → Android 手机 → 原始 PNG
          └──────── SessionStore → 台账 / 去重 / index.json
```

默认脚本不请求模型服务。旧 `collect.py` 作为[可选 API 兼容模式](docs/api-compatibility.md)保留。

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

调用者需要在搜索框输入中文（微信群名、小红书关键词）时，借助
[ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)——一个专为自动化测试设计的
**虚拟键盘输入法**。它没有可见的键盘界面，而是常驻监听系统广播：adb 发一条
广播，它就把广播携带的文本"敲"进当前聚焦的输入框，因此能输入中文、Emoji
等任意 Unicode，而采集器的输入动作与人工操作在 App 看来完全一致。

**安装与启用：**

1. 下载 APK：从 [Releases](https://github.com/senzhk/ADBKeyBoard/releases) 下载
   （按设备兼容性选择版本），执行
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
> 调用者），纯点击浏览类采集不受影响；也可手动定位后通过 observe 看图、
> capture 保存证据。

## 快速开始

需要 Python 3.10+、Pillow、已连接且目标 App 已登录的 Android 手机，以及**具备视觉能力、能打开本地截图的调用者**。不需要配置任何模型 API Key。

```bash
pip install -r "<skill_dir>/requirements.txt"
python3 "<skill_dir>/scripts/caller_collect.py" check
python3 "<skill_dir>/scripts/caller_collect.py" start \
  --app wechat --target "AI 交流群" \
  --task "打开微信，进入群聊「AI 交流群」，采集当前消息及更早历史的发送者、内容与可见时间" \
  --max-screens 20
```

后续命令使用 start 返回的绝对会话路径：

```bash
python3 "<skill_dir>/scripts/caller_collect.py" observe "<session_dir>"
# 调用者打开返回的 PNG、核验目标；需要导航时生成动作 JSON 并用 action 执行
python3 "<skill_dir>/scripts/caller_collect.py" capture "<session_dir>"
# 调用者打开这次采集的 PNG，按返回的 prompt 生成外部临时 result.json
python3 "<skill_dir>/scripts/caller_collect.py" record "<session_dir>" \
  --result-file "<scratch_dir>/result.json"
# 需要更多屏时：action 滚动 → observe 看图 → capture 看证据图 → record
python3 "<skill_dir>/scripts/caller_collect.py" finish "<session_dir>" --reason completed
python3 "<skill_dir>/scripts/inspect_session.py" "<session_dir>"
```

完整动作参数、提取 JSON 与异常收尾见 [调用者工作流](references/caller-workflow.md)。手机已手动定位时同样先 observe 看图核验；单屏采集在首次 record 后 finish。默认最多 20 屏、40 次动作尝试，持续三次失败由调用者及时收尾。

一条信息的溯源样例（`index.json`）：

```json
{
  "item_id": "itm_000007",
  "app": "wechat",
  "type": "text",
  "sender": "王小明",
  "text": "明晚八点健身房见",
  "time_hint": "2026年09月27日 20:41",
  "evidence": {
    "screenshot": "screenshots/screen-0003.png",
    "sha256": "3f7a…e9c1",
    "captured_at": "2026-09-27T20:41:32+08:00",
    "screen_index": 3,
    "model": "caller-vision",
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
        ├── navigation/        # 看图导航记录（不计入采集屏数）
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
├── requirements.txt        # 默认仅 Pillow
├── requirements-api.txt    # 可选 API 兼容依赖
├── scripts/
│   ├── caller_collect.py    # 默认入口：调用者直接看图
│   ├── collect.py           # 可选 API 兼容入口
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
