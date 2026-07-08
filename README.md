# AI 消息 & Token 统计

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A5%204.0-58a6ff)](https://astrbot.app)
[![Version](https://img.shields.io/badge/version-2.0.0-3fb950)](CHANGELOG.md)

AstrBot 插件，统计 AI 在所有群聊和私聊中的发言次数与 LLM Token 用量。支持合并转发、全局报表、每日午夜报告和持久化 Token 池。

## 功能

| 功能 | 说明 |
|---|---|
| 📝 消息计数 | 自动记录 AI 在群聊和私聊中发出的每条消息 |
| 📊 合并转发 | `看看消息` 在当前群聊发送统计表格 + 合并转发最近消息 |
| 🌐 全局报表 | `看看全部消息` 统计所有群聊和私聊的发言数，发送到汇报群 |
| 🔢 Token 追踪 | 实时追踪每次 LLM 调用的输入/输出 Token |
| 💾 Token 持久化 | 累计 Token 保存到文件，重启不丢失 |
| 🌙 每日报告 | 每天 00:00 后自动发送昨日消息统计 + Token 用量到汇报群 |

## 安装

**方式一：插件市场**
AstrBot 后台 → 插件市场 → 搜索「AI消息&Token统计」→ 安装

**方式二：手动安装**
将本仓库克隆到 `data/plugins/` 目录：
```bash
cd AstrBot/data/plugins
git clone https://github.com/yuebai5203/astrbot_plugin_msg_stat.git
```

## 指令

所有指令仅 **AstrBot 管理员** 可用，群聊需 @bot。

| 指令 | 说明 |
|---|---|
| `看看消息` | 当前群聊 1h/30min 发言统计 + 合并转发 |
| `看看全部消息` | 全局统计报表，发到配置的汇报群 |
| `看看token` | 今日和累计 Token 用量 |

## 配置

在 AstrBot 后台 → 插件管理 → AI消息&Token统计 → 配置面板：

| 配置项 | 说明 | 默认 |
|---|---|---|
| `机器人显示名称` | 合并转发中显示的昵称 | 留空=QQ号 |
| `汇报群号` | 报表投递目标群（填 QQ 群号） | 必填 |
| `启用每日报告` | 是否每天 00:00 自动发报告 | ✅ 开启 |
| `启用 Token 追踪` | 是否追踪 Token 用量 | ✅ 开启 |
| `合并转发最大条数` | 看看消息转发条数上限 | 90 |
| `转发消息截断长度` | 单条消息超过此字符数截断 | 500 |

## 每日报告样例

```
📊 每日统计报告 — 2026-07-08
━━━━━━━━━━━━━━━━━━━━

【消息统计】
群聊 2 个群，共 35 条
  技术群(1057687343)：22 条
  摸鱼群(987654321)：13 条
私聊 1 个用户，共 12 条
  月白(123456789)：12 条
昨日总计：47 条

【Token 统计】
昨日消耗：15.2万 tokens
  ├ 输入：12.1万
  └ 输出：3.1万
累计总量：128.5万 tokens
━━━━━━━━━━━━━━━━━━━━
```

## 文件结构

```
astrbot_plugin_msg_stat/
├── main.py              # 插件主逻辑
├── metadata.yaml        # 插件元信息
├── _conf_schema.json    # 配置面板定义
├── README.md            # 项目说明
├── LICENSE              # Apache 2.0
├── CHANGELOG.md         # 更新日志
├── CONTRIBUTING.md      # 贡献指南
├── .gitignore
├── .github/workflows/   # GitHub Actions
└── docs/                # GitHub Pages 文档页
```

## 依赖

- AstrBot >= 4.0.0
- aiocqhttp 平台适配器（QQ / NapCat）

## 许可证

[Apache License 2.0](LICENSE)

## 作者

[yuebai](https://github.com/yuebai5203)
