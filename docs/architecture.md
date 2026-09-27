# 架构说明

## 设计原则

1. **证据先行（Evidence First）**：截屏是唯一事实来源，结构化数据只是截屏的
   派生视图。先落盘、后提取，任何提取失败都不影响证据完整性。
2. **零侵入采集**：不逆向协议、不注入、不 Root；只有 ADB 官方通道（截屏、
   input 事件、IME 广播）。代价是速度与覆盖率，换来的是普适性与低风险。
3. **可审计**：manifest 台账只追加；提取原始输出留档；SHA-256 全程可验。
4. **只读动作面**：导航动作集合不含任何写操作，从机制上防止采集过程
   改变 App 状态。

## 模块与数据流

```
collect.py
  │
  ├─ adb.connection.ensure_device()        设备确认
  │
  ├─ StepAgent.run(nav_task)               ── 导航阶段（可选）
  │    截屏 ──▶ nav VLM (autoglm-phone)    do(action=...)/finish(...)
  │      ▲            │
  │      └── adb.input 执行 ◀──┘            Tap/Swipe/Type/Back/Home/Launch/Wait
  │
  ├─ 循环（采集阶段）
  │    adb.screenshot.capture()  ─▶ SessionStore.save_screenshot()   [证据落盘+台账]
  │    ExtractAgent.extract()    ─▶ SessionStore.save_extraction()   [去重+溯源条目]
  │    adb.input.swipe_to_next_screen() → 下一屏
  │
  └─ SessionStore.finalize()     ─▶ index.json + session_finished 事件
```

## 关键机制

### 导航（StepAgent，源自 Open-AutoGLM 架构）

- 视觉模型看截图输出 `<think>` + `do(action="Tap", element=[x,y])` DSL；
- 坐标使用 0-999 归一化空间，执行时按真实分辨率换算，跨机型通用；
- 历史消息中图片即用即弃，只留文本，控制上下文成本；
- 上一步无效时由模型自行等待/重试，单步异常不终止任务。

### 提取（ExtractAgent）

- 单屏一次 VLM 调用，temperature=0.1；
- 提示词注入日历上下文（今天/昨天/本周日期），把截图相对时间换算成
  绝对时间——时间字段的溯源精度由此决定；
- JSON 解析容忍代码围栏与杂质，失败自动重试，重试用尽则该屏标记
  `extraction_failed` 并留在台账中。

### 去重与停止

- 条目指纹 = SHA-1(type|sender|title|text)，跨屏去重；
- 相邻屏因 60% 重叠会产生天然重复，重复率高不停止；只有"连续 2 屏
  零新条目"才判定到达信息边界，`--max-screens` 是硬上限。

### 溯源链

```
index.json 条目 ──evidence.screenshot──▶ screenshots/screen-NNNN.png
                     evidence.sha256  ──▶ 与文件实际 SHA-256 比对
extracted/screen-NNNN.json ──▶ 模型原始输出（审计提取行为）
manifest.jsonl ──▶ 全部事件的时序台账
```

`inspect_session.py` 独立于采集流程，可对任何目录做离线校验。

## 扩展点

- **新 App**：`prompts.py` 的 `APP_NAV_TEMPLATES` / `APP_EXTRACT_HINTS`
  加两条描述即可，无需改代码逻辑；
- **新设备后端（iOS 等）**：`collector/adb` 是唯一的设备抽象层，实现同
  接口（capture/tap/swipe/type/back/home/launch_app）即可替换；
- **新存储后端**：`SessionStore` 与采集流程解耦，可派生 SQLite/远端版本，
  但"先证据后提取"的顺序与不可变台账是规范要求。
