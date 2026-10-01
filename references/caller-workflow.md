# 调用者视觉工作流

调用者使用自己的图片查看工具和视觉能力；脚本不会在后台请求模型。
以下 `<skill_dir>`、`<session_dir>`、`<scratch_dir>` 都替换为绝对路径，
scratch 位于会话目录外。每次只运行一条会话命令。

## 开始与看图导航

```bash
python3 "<skill_dir>/scripts/caller_collect.py" check
python3 "<skill_dir>/scripts/caller_collect.py" start \
  --app wechat --target "AI 交流群" \
  --task "打开微信，进入群聊「AI 交流群」，采集当前消息及更早历史的发送者、内容与可见时间" \
  --data-dir "<output_dir>/collections" --max-screens 20 --max-actions 40
```

保存 start 返回的 `session` 绝对路径。多设备时 start 加 `--device-id`。
`--dedup auto`：微信为 chat，其他为 global；可显式覆盖。`--model` 仅用于记录
调用者已知的模型标识，默认 `caller-vision` 是执行模式标签，不是假定的型号。

```bash
python3 "<skill_dir>/scripts/caller_collect.py" observe "<session_dir>"
```

用所在环境的图片查看工具（例如 `view_image`）**打开返回的 screenshot 路径**。
观察当前 App、页面标题、目标内容与可点击位置。尚未到目标时，生成一个动作
JSON 到 `<scratch_dir>/action.json`，然后执行并重新 observe、打开新图：

```bash
python3 "<skill_dir>/scripts/caller_collect.py" action "<session_dir>" \
  --action-file "<scratch_dir>/action.json"
```

动作是 JSON 对象；坐标为 0–999 归一化坐标，取自最新截图：

```json
{"name":"Tap","element":[500,210],"intent":"open_chat","target_text":"AI 交流群"}
```

| name | 参数与用途 |
|------|------------|
| Tap | `element:[x,y]`；必须是只读入口，真实控件树也要通过检查 |
| Swipe | `start:[x,y],end:[x,y]`；只允许近垂直滚动 |
| Type | `text:"搜索词",intent:"search"`；先看图确认焦点在搜索框 |
| Back / Home | 返回 / 主页；执行后重看画面 |
| Launch | `app:"wechat"`（也可使用包名）；启动目标 App |
| Wait | `seconds:1`（0–10 秒）；等待加载后重看画面 |

Tap/Swipe/Type 都提供 `intent` 和可见的 `target_text`，准确描述语义。
禁止用伪造意图绕过护栏。命令返回 `allowed:false` 时未执行动作（退出码 2）；
换只读路径，不重复同一受阻动作。设备错误也消耗动作次数。

## 采集一屏与提取

确认目标正确后：

```bash
python3 "<skill_dir>/scripts/caller_collect.py" capture "<session_dir>"
```

**打开这次返回的 screenshot 文件**。依据同一返回中的 `prompt`，由调用者直接
读图生成提取 JSON，写入 `<scratch_dir>/result.json`：

```json
{
  "screen_summary":"AI 交流群可见聊天记录",
  "items":[
    {"type":"text","sender":"张三","title":null,"text":"今晚八点开会",
     "time_hint":null,"extra":{},"bbox":[100,300,900,430],"confidence":"high"}
  ]
}
```

`items` 必须是对象数组；`type/title/text/sender/time_hint` 为字符串或 null。
不可见时间用 null，图片/语音用攻略规定的占位描述；不可将上一屏内容补入。
没有可见条目用空数组。`bbox` 为 0–999 坐标；evidence 和 item_id 由脚本生成。

可选字段若提供：screen_summary 必须是字符串；extra 必须是 JSON 对象；
confidence 必须为 high/medium/low；bbox 为 null 或四个 0–999 的有限数值，
满足 x1≤x2、y1≤y2。省略时采用默认值，非法字段会在写盘前拒绝。

```bash
python3 "<skill_dir>/scripts/caller_collect.py" record "<session_dir>" \
  --result-file "<scratch_dir>/result.json"
```

查看 `new/duplicates`。JSON 格式错误可修正临时文件后再次 record；登记成功后
同屏不允许覆盖。无法提取则 `record "<session_dir>" --error "具体原因"` 留证。

继续历史聊天的 Swipe 示例（手指下滑）：

```json
{"name":"Swipe","start":[500,300],"end":[500,700],"intent":"browse_history"}
```

信息流下一屏手指上滑，交换 start/end。保留约 60% 画面重叠并等待加载；
每次动作后 observe 看图，再 capture，看证据图、生成新 JSON、record。

## 收尾与校验

```bash
python3 "<skill_dir>/scripts/caller_collect.py" finish "<session_dir>" --reason completed
python3 "<skill_dir>/scripts/inspect_session.py" "<session_dir>"
```

reason 可为 completed/no_new_items/max_screens/interrupted/error/
navigation_failed/extraction_failed。未到目标页用 navigation_failed 并附
`--error "原因"`（0 张采集截图）。已有采集截图时按实际原因收尾。
有待提取截图时先 record，或 `finish --reason extraction_failed --error "原因"`。
异常/中断时尽力完成 finish；连接丢失不影响本地收尾。
