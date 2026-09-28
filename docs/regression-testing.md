# 真机回归测试清单

代码层冻结后的验收阶段。目标是**用真实 session、manifest 和失败日志**驱动
下一轮修复，替代继续的逐行代码 review。每类场景至少 2~3 轮。

## 场景矩阵

| # | 场景 | 命令要点 | 重点观察 |
|---|------|----------|----------|
| 1 | 微信群聊 | `--app wechat --target "<群名>"` | chat 滑窗去重、消息时间戳提取 |
| 2 | 微信私聊 | `--app wechat --target "<联系人>"` | 同 1 |
| 3 | 小红书搜索/列表 | `--app xiaohongshu --target "<关键词>"` | 卡片去重（global）、笔记字段 |
| 4 | 红果信息流 | `--app 红果免费短剧 --no-navigate` | global 去重、大卡片跨屏 |
| 5 | 长消息/长卡片 | 群里置顶超长消息 / 长图文笔记 | 单条内容跨 4~5 屏不被重复入库 |
| 6 | 网络断开 | 采集中途开飞行模式 | AdbCommandError 反馈、device_error 收尾、会话可校验 |
| 7 | 模型超时/限流 | 断网 Wi-Fi / 低配额 | 提取失败留证、max_extract_failures 终止 |
| 8 | ADB offline | 采集/导航中途拔线 | 同 6；导航侧 device_error |
| 9 | UI tree dump 失败 | 进入 FLAG_SECURE 页面（支付/密码） | `[护栏降级]` 日志、导航仍能退出该页 |
| 10 | 导航错误 | `--target "<不存在的群>"` | navigation_failed 会话、0 截图、inspect 通过 |
| 11 | 中途 Ctrl+C | 任意场景采集到一半 | interrupted 收尾、无 .tmp 残留、可校验 |
| 12 | 连续 30~50 屏 | 群聊 `--max-screens 50` | 长跑内存/上下文、智能停止是否误停 |
| 13 | 导航护栏触发 | 目标群名含"关注/点赞"等关键词 | `[GUARD]` 反馈后模型能换搜索入口完成 |

## 每轮收集的指标

跑完后对当日所有 session 统计（命令见下），记录到本轮回归报告：

1. **护栏拦截分布**：各判定码（deny_write_action / deny_non_scroll_swipe /
   deny_invalid_coordinates）出现次数——高频说明提示词需要调，而不是护栏问题；
2. **护栏降级率**：`[护栏降级] uiautomator dump 不可用` 占 Tap 步数比例，
   超过 ~30% 需要加兜底策略；
3. **导航失败原因分布**：max_steps / safety_aborted / device_error / verify_failed；
4. **停止原因分布**：completed / no_new_items / max_screens / interrupted；
5. **去重率**：`items_unique / items_extracted`（chat 与 global 模式分开看）；
6. **目标页核验**：verify 未通过的次数与理由；
7. **耗时**：每屏平均耗时（截屏/提取/翻页），导航平均步数。

## 快速统计命令

```bash
DIR=collections/$(date +%F)

# 所有会话校验（应全部通过）
find "$DIR" -name index.json -exec dirname {} \; | xargs -n1 python3 scripts/inspect_session.py

# 停止原因分布
jq -r .stop_reason "$DIR"/*/index.json | sort | uniq -c

# 导航结果分布
jq -r '.navigation.reason // "no_navigation"' "$DIR"/*/index.json | sort | uniq -c

# 去重率
jq -r '[.totals.items_unique, .totals.duplicates] | @tsv' "$DIR"/*/index.json

# 提取失败屏数
jq -r '.totals.extract_failures' "$DIR"/*/index.json | paste -sd+ - | bc
```

护栏拦截/降级发生在 verbose 日志里：用 `--verbose` 采集并留存终端输出，
或事后从 manifest 的事件流复盘导航过程。

## 通过标准

一轮回归算通过，当且仅当：

1. 所有 session `inspect_session.py` 校验通过（含 navigation_failed 形态）；
2. 人工抽查 2~3 个 session：没有任何实际发生的写操作（未发送消息、
   未点赞/关注、输入法已恢复）；
3. navigation_failed 会话均为 0 截图；
4. 无 `.tmp` 残留文件、无未收尾（缺 index.json）的会话目录；
5. 上述指标无异常突变（如拦截率、降级率、去重率）。

发现问题时，附 session 目录路径 + verbose 日志 + 复现命令再 review，
不要再做无凭据的逐行猜测式审查。
