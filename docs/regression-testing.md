# 回归测试

## 离线验证

默认入口测试无需手机、Key 或模型 SDK，设备操作使用模拟对象：

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests -p 'test_caller_collect.py' -v
```

兼容模式及全量测试额外安装 requirements-api.txt；所有测试仍不连接真实设备
或模型 API。CI 先在基础依赖环境跑 caller 测试，再安装可选依赖跑全量测试。

## 真机验收

按 [调用者工作流](../references/caller-workflow.md)逐步操作，用真实截图和
manifest 验证调用者视觉判断，不要用控件文本代替图片。每轮检查以下场景：

| 场景 | 重点 |
|------|------|
| 微信群聊/私聊历史 | 目标标题匹配；手指下滑后可见时间变早；chat 去重 |
| 小红书/红果列表 | 手指上滑；global 去重；卡片跨屏重叠 |
| 长消息/长卡片 | 画面仍位移时不因零新增提前停止 |
| 目标不存在 | navigation_failed 收尾，0 张采集截图，观察图仍保留 |
| Tap 写操作或控件树失败 | 拒绝点击；换只读入口或 Back，连续失败收尾 |
| ADB 离线/拔线 | 记录动作错误，保留已有截图；本地 finish 不依赖连接 |
| 模糊/无法提取页面 | record --error，失败屏留证与统计 |
| 命令中断 | 重新运行恢复去重；待提取屏必须先 record；不覆盖证据 |
| 屏数/动作上限 | 拒绝超限，调用者正确收尾 |

每轮 finish 后运行 inspect_session.py，抽查条目对应的原图，记录停止原因、
失败屏数、去重数与导航是否正确。没有真机执行时只报告离线验证，不能声称
已验证实际 App 的布局、点击安全性或识别准确率。

manifest 中 action_attempt/action/action_error 可复盘动作尝试与拒绝原因。
发现问题附会话路径、具体命令与事件；敏感截图不要提交到仓库。
