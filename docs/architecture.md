# 架构说明

## 设计原则

1. **证据先行（Evidence First）**：截屏是唯一事实来源，结构化数据只是截屏的
   派生视图。先落盘、后提取，任何提取失败都不影响证据完整性。
2. **零侵入采集**：不逆向协议、不注入、不 Root；只有 ADB 官方通道（截屏、
   input 事件、IME 广播）。代价是速度与覆盖率，换来的是普适性与低风险。
3. **可审计**：manifest 台账只追加且逐条 fsync；提取原始输出留档；
   SHA-256 全程可验；所有落盘走 tmp + fsync + 原子 rename，进程中断
   不会留下半个文件。
4. **只读动作面（两层防线）**：导航动作集合不含写原语；每个动作执行前
   经 ActionGuard 语义护栏——模型申报 intent / target_text，命中
   "发送 / 点赞 / 支付 / 删除 / 授权"等写操作语义即拒绝执行并反馈模型
   重新规划，连续拦截则终止导航。护栏基于模型申报的语义判定，属于
   纵深防御而非形式化验证。
5. **失败即停**：导航返回结构化 NavigationResult（成功 / 失败原因 /
   步数 / 当前 App），finish 后由模型核验"当前页是否满足目标"；导航
   失败立即终止会话，不把错误页面的数据当成合法证据入库。

## 模块与数据流

```
collect.py
  │
  ├─ adb.connection.ensure_device()        设备确认
  │
  ├─ StepAgent.run(nav_task) → NavigationResult   ── 导航阶段（可选）
  │    截屏 ──▶ nav VLM (autoglm-phone)    do(action=...)/finish(...)
  │      ▲            │ 解析（DSL 优先，兼容 JSON）
  │      │            ▼
  │      │      ActionGuard 只读护栏 ── deny ─▶ [GUARD] 反馈，重新规划
  │      │            │ allow
  │      │            ▼
  │      └── adb.input 执行 ◀──┘            Tap/Swipe/Type/Back/Home/Launch/Wait
  │           （失败以 [ACTION_FAILED] 反馈模型，连续失败终止）
  │    finish ──▶ 目标页核验（未通过则要求继续导航）
  │    失败 ──▶ stop_reason=navigation_failed，本会话不采集
  │
  ├─ 循环（采集阶段）
  │    adb.screenshot.capture()  ─▶ SessionStore.save_screenshot()   [证据落盘+台账]
  │    ExtractAgent.extract()    ─▶ SessionStore.save_extraction()   [去重+溯源条目]
  │    adb.input.swipe_to_next_screen() → 下一屏（感知哈希判断画面位移）
  │
  └─ SessionStore.finalize()     ─▶ index.json + session_finished 事件
```

## 关键机制

### 导航（StepAgent，源自 Open-AutoGLM 架构）

- 视觉模型看截图输出 `<think>` + `do(action="Tap", element=[x,y])` DSL，
  解析支持引号转义（`text="他说 \"hi\""`），也接受等价 JSON 动作对象；
- 坐标使用 0-999 归一化空间，护栏校验范围后按真实分辨率换算并夹紧，
  跨机型通用；
- 动作携带 intent / target_text 交 ActionGuard 判定；被拦截的动作以
  [GUARD] 消息反馈，模型换只读路径重新规划；
- adb 命令失败抛 AdbCommandError，以 [ACTION_FAILED] 观察反馈模型，
  连续失败（默认 3 次）以 device_error / action_failed 终止；
- 文本输入走 input_text_safe 事务：切换 ADB Keyboard → 输入 →
  无论成败恢复原输入法；
- 历史消息中图片即用即弃，只留文本，控制上下文成本；
- run() 返回 NavigationResult：finish 后目标页核验（未通过继续导航），
  最大步数 / 设备故障 / 护栏连续拦截均为 success=False。

### 提取（ExtractAgent）

- 单屏一次 VLM 调用，temperature=0.1；
- 提示词注入日历上下文（今天/昨天/本周日期），把截图相对时间换算成
  绝对时间——时间字段的溯源精度由此决定；
- JSON 解析容忍代码围栏与杂质，失败自动重试，重试用尽则该屏标记
  `extraction_failed` 并留在台账中。

### 去重与停止

- 指纹 = SHA-1(type|sender|title|text[|time_hint])。聊天类条目
  （message/comment/system）带可见时间戳时并入指纹；同一个人不同时刻
  发的相同文字因此不会被误杀。
- 聊天类只对最近 3 屏做滑窗判重（60% 重叠下一条内容最多跨 3 屏可见）：
  窗口内的重复是重叠重采，窗口外的相同内容视为真实重复、保留入库。
  非聊天类（note/product 等，卡片有标题）仍全局判重。
- 停止 = "连续 N 屏零新条目"且画面位移消失（相邻屏 8x8 感知哈希汉明
  距离 ≤ 4，说明翻页已无效），或宽限屏数（默认 +2）用尽；`--max-screens`
  仍是硬上限。长图 / 大卡片 / 占位 UI 连续几屏没有新条目时，只要画面
  还在正常位移就继续采集，不再被误判为信息边界。

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
  接口（capture/tap/swipe/input_text_safe/back/home/launch_app）即可替换；
- **新存储后端**：`SessionStore` 与采集流程解耦，可派生 SQLite/远端版本，
  但"先证据后提取"的顺序与不可变台账是规范要求。
