# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [2.1.1] - 2026-07-10

### 修复
- 移除 `on_msg_sent` 中对消息 `get_plain_text()` 的过滤——纯图片、合并转发等非纯文本消息现在也能被计数
- 每日报告中的「昨日总计」改为「消息总数」——标题已经是昨日日期，无需再加"昨日"

## [2.1.0] - 2026-07-09

### 新增
- `show_tokens_in_msg_stats` 配置项：「看看消息」附带 Token 统计（默认关闭）
- `show_tokens_in_all_stats` 配置项：「看看全部消息」附带 Token 统计（默认关闭）

## [2.0.0] - 2026-07-09

### 新增
- Token 追踪：`on_llm_response` 钩子实时追踪每次 LLM 调用的 Token 消耗
- Token 持久化：累计 Token 保存到 `data.json`，重启不丢失
- 每日午夜报告：跨天自动发送昨日消息统计 + Token 用量到汇报群
- `看看token` 指令：手动查看今日和累计 Token 用量
- 配置项 `daily_report_enabled`、`track_tokens_enabled`

## [1.2.0] - 2026-07-08

### 新增
- 全局报表含群名和用户昵称（通过 OneBot API 并行查询）
- 名称缓存，1 小时内复用

### 变更
- 报表数据恢复原版干净风格，当前对话反馈保留喵味

## [1.1.0] - 2026-07-08

### 新增
- `看看全部消息` 指令：全局统计报表，发送到配置的汇报群
- 同时追踪群聊和私聊消息
- 配置项 `report_group_id`

## [1.0.0] - 2026-07-08

### 新增
- `看看消息` 指令：当前群聊发言统计 + 合并转发
- `after_message_sent` 钩子记录 AI 发出的群聊消息
- 管理员权限限制
- 配置项 `bot_display_name`、`max_forward_count`、`max_message_length`
