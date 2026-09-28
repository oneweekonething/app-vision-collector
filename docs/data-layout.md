# 数据规范：采集会话目录（v1）

一次采集会话 = 一个独立目录。目录自包含：拿到目录即可离线审计全部证据。

```
<AVC_DATA_DIR>/                          # 默认 ./collections
└── 2026-09-27/                          # 按日期分桶
    └── 20260927-153000-483921-a1b2c3d4_wechat_AI交流群/
        #                  └微秒┘ └uuid8┘  目录名全局唯一：
        #  <秒级时间>-<微秒>-<随机后缀>_<app>_<目标slug>，并行/重跑不复用
        ├── session.json
        ├── screenshots/
        │   ├── screen-0001.png
        │   └── screen-0002.png
        ├── extracted/
        │   ├── screen-0001.json
        │   └── screen-0002.json
        ├── manifest.jsonl
        └── index.json
```

## session.json —— 会话元数据

| 字段 | 说明 |
|------|------|
| `session_id` | 目录名一致的全局唯一 ID |
| `collector_version` / `prompt_version` | 采集器与提示词版本（复现实验用） |
| `app` / `target` / `task` | 采集对象与任务描述 |
| `device_id` | adb 设备标识 |
| `vlm_model` / `nav_model` | 提取与导航所用模型 |
| `started_at` | 开始时间（ISO8601 含时区） |
| `dedup_mode` | 去重策略：`chat`（聊天滑窗，同文本出窗后视为真实重复）/ `global`（信息流全局判重，同内容远距重现视为重复曝光）；会话内不可变 |

## screenshots/ —— 证据本体

- `screen-NNNN.png`，NNNN 为屏号（1 起），与会话内其他文件的引用一一对应；
- 由 `adb exec-out screencap -p` 原始字节直接写盘，**不做任何压缩或处理**；
- 只增不改：即使重跑采集也生成新会话目录，绝不覆盖旧截图。

## extracted/screen-NNNN.json —— 单屏提取原始输出

```json
{
  "screen": 1,
  "model": "qwen3-vl-plus",
  "prompt_version": "extract-v1",
  "extracted_at": "2026-09-27T15:30:05+08:00",
  "screen_summary": "群聊消息 8 条",
  "items": [ ... 模型输出的条目数组 ... ],
  "raw_response": "模型原始返回文本（含 JSON）",
  "repair_response": "（可选）解析失败经纯文本修复成功时，修复调用的原始输出"
}
```

保留 `raw_response` 是为了审计模型行为：即使解析有误也能回查。

**提取失败的屏**同样会有 `extracted/screen-NNNN.json`：`items` 为空数组、
`raw_response` 为最后一次模型原始输出（可能是不完整/非法 JSON）、
多一个 `error` 字段记录失败原因；截图与台账照常留存，
会话继续采集后续屏（连续多屏失败才终止）。`index.json` 的
`totals.extract_failures` 记录失败屏数。

## manifest.jsonl —— 追加式事件台账

每行一个 JSON 事件，按发生顺序追加：

| event | 关键字段 |
|-------|----------|
| `session_started` | 会话元数据全量 |
| `screenshot` | `screen`, `path`, `sha256`, `bytes`, `width/height`, `captured_at` |
| `items` | `screen`, `screenshot_sha256`, `extracted/new/duplicates`, `item_ids` |
| `extraction_failed` | `screen`, `screenshot_sha256`, `error` |
| `session_finished` | `stop_reason`, `error`, 各项 totals |

## index.json —— 条目目录（去重后）

```json
{
  "session_id": "...",
  "stop_reason": "no_new_items",
  "totals": {"screens": 3, "items_extracted": 21, "items_unique": 19, "duplicates": 2, "extract_failures": 0},
  "items": [
    {
      "item_id": "itm_000001",
      "app": "wechat",
      "type": "message",
      "sender": "王小明",
      "text": "明晚八点健身房见",
      "time_hint": "2026年09月27日 20:41",
      "confidence": "high",
      "evidence": {
        "screenshot": "screenshots/screen-0001.png",
        "sha256": "<截图文件 SHA-256>",
        "captured_at": "2026-09-27T15:30:05+08:00",
        "screen_index": 1,
        "bbox": [120, 410, 900, 470],
        "model": "qwen3-vl-plus",
        "prompt_version": "extract-v1"
      }
    }
  ]
}
```

### 去重指纹

`SHA-1( type | sender | title | text )`，各字段做空白归一化与小写化。
指纹仅用于跨屏去重，不参与溯源校验（溯源以截图 SHA-256 为准）。

## 校验

```bash
python3 scripts/inspect_session.py <session-dir>
```

校验内容：台账可解析、截图存在且哈希一致、index 每条 evidence 可回溯、
totals 与台账一致、每屏有提取文件。任何一项失败退出码为 1。
