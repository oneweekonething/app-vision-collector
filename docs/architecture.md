# 架构说明

默认入口是 `caller_collect.py`。视觉与推理由调用 skill 的助手提供：打开本地
截图、核验目标页、决定动作、生成提取 JSON。脚本不创建模型客户端、不请求
模型服务、不读取 API Key；基础依赖仅 Pillow。

## 数据流

```text
start → SessionStore.create（execution_mode=caller）
observe → ADB 截图 → navigation/ + observation 台账 → 调用者打开 PNG
调用者动作 JSON → action → ActionGuard → ADB → 再次 observe 看图
调用者确认目标 → capture → screenshots/ + screenshot 台账
调用者打开证据 PNG → 根据提取提示词生成 JSON → record
record → schema 校验 → SessionStore.save_extraction → 去重 + items 台账
finish → index.json + session_finished 台账 → inspect_session.py 校验
```

## 设备动作与目标核验

坐标使用 0–999 空间，护栏先校验再转换到设备分辨率。Tap 自报 intent /
 target_text 通过后，uiautomator 独立读取真实控件文本，命中写操作关键词
则拒绝；控件树不可用也拒绝。Swipe 只允许近垂直滚动，避开两侧手势区。
Type 仅由调用者在看图确认的搜索框使用，中文经 ADBKeyBoard 输入，结束或
失败后恢复原输入法。护栏不能证明任意点击无副作用，仍需要调用者看图判断。

导航观察图不计入采集屏数；capture 是调用者看图核验目标后的明确采集决定，
首次 capture 记录 caller_verified 导航结果。脚本不能证明调用者实际看过图片，
该行为由 skill 工作流要求。导航失败 finish 使用 navigation_failed，保持
navigation.success=false、0 张采集截图，校验器验证此形态。

## 持久状态与停止

每个命令从 manifest 重建 SessionStore 的计数、条目 ID、去重窗口与导航状态，
核对历史截图哈希和提取统计。命令串行执行，不支持同一会话并发操作。
有待提取截图时禁止观察、动作与再次 capture；先登记成功或失败结果。
同屏提取不能覆盖，已收尾会话拒绝恢复。崩溃后有文件但缺对应台账的中间状态
会拒绝继续写入，需要人工审计后另建会话，不自动猜测或修复证据。

max-screens 和 max-actions 是脚本上限；动作尝试（包括拒绝/设备错误）均计数。
调用者根据用户范围、画面位移与新增统计决定提前结束，连续三次失败时收尾。
默认流程的停止判断由调用者做，脚本不会调用额外模型或自动翻页。

## 去重与证据

chat 模式对消息类（含微信 text/image/voice/video/link/sticker/system）使用
最近 5 屏滑窗，指纹包含可见 time_hint；窗口外相同内容作为真实重复保留。
global 模式对信息流全局去重，指纹不含时间。auto 以微信为 chat，其他为 global。

所有条目由 SessionStore 添加 evidence（截图路径、SHA-256、屏号、时间、模型标识）。
模型标识默认 caller-vision，仅说明由调用者提取；已知真实型号时可通过 --model
记录。截图先保存再提取，失败屏也保留提取文件与错误台账。证据格式和校验
见 [data-layout.md](data-layout.md)。

## 可选 API 兼容模式

[API 兼容入口](api-compatibility.md)使用 collect.py、StepAgent 和 ExtractAgent，
仅在用户明确选择时安装 requirements-api.txt 并配置模型服务。它保留 DSL/JSON
导航动作解析、模型目标页核验、JSON 修复重试与感知哈希停止机制。
OpenAI SDK 延迟到客户端实例化才导入，因此不影响默认 caller 入口。
两种模式共享 ADB、护栏、提取提示词和证据存储，不混用会话。
