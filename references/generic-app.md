# 通用 App 采集攻略

使用 [调用者工作流](caller-workflow.md)，新 App 无需配置模型服务或修改代码。

## 定位与范围

- 用户已手动停到目标页：start 写明 App、当前页面与字段；observe 看图确认后 capture。
- 单屏任务：首次 record 后 finish，无需滚动。
- 列表任务：调用者先看图定位；在 record 后用近垂直 Swipe 浏览下一屏，再看图、保存证据并提取。
- 需要搜索：仅在确认的搜索框中 Type；中文依赖 ADBKeyBoard。不要在聊天/评论编辑框输入。

## 提取字段

`type` 用 message/post/comment/product/profile/search_result/other 等实际语义；
`title/sender/text` 记录可见标题、作者、正文；`extra` 放可见的价格、数量、标签。
`bbox` 为 0–999 归一化位置，模糊内容降低 confidence，不推测屏幕外信息。

## 节奏与停止

滚动后等待加载并重新 observe，坐标从最新截图确定。保留约 60% 重叠；
连续两屏零新增且画面不再位移时收尾，仍有位移则在用户范围内继续。
达到 max-screens 或连续三次失败时停止。不要为绕过风控修改护栏或扩大采集范围。
