# 可选 API 兼容模式

当前 skill 默认用 `caller_collect.py`，直接使用调用者的视觉能力，不需要 Key。
此页仅用于用户明确选择独立模型 API 驱动的旧自动化流程；它不属于默认前置条件。

安装额外 SDK 并配置模型服务：

```bash
pip install -r "<skill_dir>/requirements-api.txt"
export AVC_API_KEY="<your-key>"
export AVC_API_BASE="https://<openai-compatible-endpoint>/v1"
export AVC_VLM_MODEL="<vision-model>"
export AVC_NAV_MODEL="<vision-model>"
python3 "<skill_dir>/scripts/collect.py" --check
```

兼容入口通过 StepAgent / ExtractAgent 调用配置的模型 API，会向该服务提交截图。
默认服务地址/型号保留于 `scripts/collector/config.py`；请按实际提供商覆盖。
也支持 DASHSCOPE_API_KEY / BIGMODEL_API_KEY，均为此模式的配置。

```bash
python3 "<skill_dir>/scripts/collect.py" --app wechat --target "AI 交流群" \
  --task "打开微信，进入群聊「AI 交流群」，采集当前可见消息及更早的历史" \
  --max-screens 20
```

显式 task 原样使用，必须包含 App、目标、字段与范围。`--no-navigate` 可跳过
导航，`--no-scroll` 仅采一屏；`--scroll-direction auto/up/down` 选择手指方向
（auto：微信下滑查历史，其他上滑查下一屏）。与 caller 模式使用同一证据格式
和校验器，但会话不能混用或交给 caller 入口恢复。

此模式保留自动 JSON 修复/重试、感知哈希停止策略，以及旧 Tap 控件树不可用
时降级到语义护栏的行为。默认 caller 模式在控件树不可用时拒绝 Tap。
