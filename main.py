"""
astrbot_plugin_msg_stat - AI 消息 & Token 统计插件

功能：
- 记录 AI 在所有群聊/私聊中发出的消息
- 追踪 LLM Token 使用量，持久化累计
- 每晚 00:00 自动发送昨日统计报表到指定群聊

触发指令（仅管理员）：
- 看看消息       → 当前群聊统计 + 合并转发
- 看看全部消息   → 全局统计报表（含群名/昵称）
- 看看token      → 今日/累计 Token 使用量

Author: yuebai
Version: 2.1.3
"""

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.message_components import Plain, Node, Nodes
from astrbot.api.provider import LLMResponse
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import TokenUsage

# 上海时区
TZ = timezone(timedelta(hours=8))

# 数据持久化文件（放在插件目录下）
DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")


def _today() -> str:
    """返回今天的日期字符串（上海时区）。"""
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _yesterday() -> str:
    """返回昨天的日期字符串（上海时区）。"""
    return (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")


@register(
    "astrbot_plugin_msg_stat",
    "yuebai",
    "AI消息&Token统计：消息计数、Token持久化追踪、每日午夜报表",
    "2.1.3",
)
class MsgStat(Star):
    """
    AI 消息 & Token 统计插件 v2.0

    - 消息计数（群聊 + 私聊，滑动窗口 + 每日累计）
    - Token 追踪（每日 + 累计，持久化到文件）
    - 每日午夜报表（昨日消息 + 今日 Token + 累计 Token）
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # ── 消息滑动窗口（1 小时）──
        self._group_records: dict[str, list[tuple[float, str, str, str]]] = defaultdict(list)
        self._private_records: dict[str, list[tuple[float, str, str, str]]] = defaultdict(list)
        self._last_clean_group: dict[str, float] = {}
        self._last_clean_private: dict[str, float] = {}

        # ── 每日消息计数（内存）──
        self._daily_group_msgs: dict[str, int] = defaultdict(int)
        self._daily_private_msgs: dict[str, int] = defaultdict(int)

        # ── Token 追踪（内存 + 持久化）──
        self._daily_tokens = TokenUsage()
        self._total_tokens = TokenUsage()
        self._today_str = _today()
        self._yesterday_str = _yesterday()

        # ── 每日报告状态 ──
        self._last_report_date: str = ""  # 上次已发送报告的日期
        self._report_lock = asyncio.Lock()

        # ── 持久化锁 ──
        self._save_lock = asyncio.Lock()

        # ── 名称缓存 ──
        self._group_name_cache: dict[str, str] = {}
        self._user_name_cache: dict[str, str] = {}

        # ── 加载持久化数据 ──
        self._load_data()

    # ══════════════════════════════════════════════════════════
    #  持久化
    # ══════════════════════════════════════════════════════════

    def _load_data(self):
        """从文件加载累计 Token 和上次报告日期。"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                t = raw.get("total_tokens", {})
                self._total_tokens = TokenUsage(
                    input_other=t.get("input_other", 0),
                    input_cached=t.get("input_cached", 0),
                    output=t.get("output", 0),
                )
                self._last_report_date = raw.get("last_report_date", "")
                logger.info(
                    f"[MsgStat] 已加载持久化数据 | "
                    f"累计Token={self._total_tokens.total} | "
                    f"最后报告={self._last_report_date}"
                )
        except Exception as e:
            logger.error(f"[MsgStat] 加载数据文件失败: {e}")

    async def _save_data(self):
        """保存累计 Token 和每日快照到文件（异步加锁，防止并发写损坏）。"""
        async with self._save_lock:
            try:
                data = {
                    "total_tokens": {
                        "input_other": self._total_tokens.input_other,
                        "input_cached": self._total_tokens.input_cached,
                        "output": self._total_tokens.output,
                    },
                    "last_report_date": self._last_report_date,
                    "yesterday_snapshot": {
                        "groups": dict(self._daily_group_msgs),
                        "private": dict(self._daily_private_msgs),
                    },
                }
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"[MsgStat] 保存数据文件失败: {e}")

    # ══════════════════════════════════════════════════════════
    #  辅助方法
    # ══════════════════════════════════════════════════════════

    def _clean_records(self, records: dict[str, list], key: str, now: float | None = None):
        if now is None:
            now = time.time()
        cutoff = now - 3600
        before = len(records[key])
        records[key] = [r for r in records[key] if r[0] > cutoff]
        if before != len(records[key]):
            logger.debug(f"[MsgStat] 清理 {key}：{before} → {len(records[key])}")

    def _cleanup_dead_keys(self):
        """清理超过 24 小时无新消息的空记录 key，防止 dict 无限膨胀。"""
        now = time.time()
        deadline = now - 86400
        for records_name, last_clean_dict in [
            ("_group_records", self._last_clean_group),
            ("_private_records", self._last_clean_private),
        ]:
            records = getattr(self, records_name)
            dead_keys = [
                k for k, lst in records.items()
                if not lst and last_clean_dict.get(k, 0) < deadline
            ]
            for k in dead_keys:
                del records[k]
                last_clean_dict.pop(k, None)
            if dead_keys:
                logger.debug(f"[MsgStat] 清理死 key {len(dead_keys)} 个 ({records_name})")

    def _cleanup_all_expired(self):
        """全局扫一遍清理所有过期（超过 1 小时）的消息记录。"""
        now = time.time()
        for records_name in ("_group_records", "_private_records"):
            records = getattr(self, records_name)
            before_total = sum(len(v) for v in records.values())
            keys_to_drop = []
            for k, lst in list(records.items()):
                records[k] = [r for r in lst if r[0] > now - 3600]
                if not records[k]:
                    keys_to_drop.append(k)
            for k in keys_to_drop:
                del records[k]
            after_total = sum(len(v) for v in records.values())
            if before_total != after_total:
                logger.info(
                    f"[MsgStat] 全局清理 {records_name}："
                    f"{before_total} → {after_total} 条，移除 {len(keys_to_drop)} 个空 key"
                )

    def _name_with_cache(self, cache: dict[str, str], id_: str) -> str:
        name = cache.get(id_, "")
        if name:
            return f"{name}({id_})"
        return id_

    def _format_token(self, n: int) -> str:
        """格式化 token 数字，千分位分隔。"""
        return f"{n:,}"

    @staticmethod
    def _extract_private_user_id(session_or_raw: str) -> str:
        """从 session_id 字符串中提取纯数字用户 ID。

        可能的格式：
        - aiocqhttp:PrivateMessage:123456789
        - 123456789（纯数字）
        返回：纯数字字符串，失败返回原值。
        """
        if ":" in session_or_raw:
            parts = session_or_raw.rsplit(":", 1)
            candidate = parts[-1]
            if candidate.isdigit():
                return candidate
        return session_or_raw

    async def _resolve_names(self, platform_id: str, group_ids: list[str], user_ids: list[str]):
        """通过 OneBot API 并行查询群名和用户昵称。"""
        platform = self.context.get_platform_inst(platform_id)
        if not platform:
            return
        bot = getattr(platform, "bot", None)
        if not bot or not hasattr(bot, "call_action"):
            return

        pending = []
        for gid in group_ids:
            if gid not in self._group_name_cache:
                pending.append(("group", gid))
        for uid in user_ids:
            if uid not in self._user_name_cache:
                pending.append(("user", uid))
        if not pending:
            return

        coros = []
        valid_pending = []  # 只保留能正常构造 API 调用的项，保持与 coros 一一对应
        for kind, id_ in pending:
            if kind == "group":
                try:
                    gid_int = int(id_)
                except ValueError:
                    logger.warning(f"[MsgStat] 无效的群号: {id_}")
                    continue
                coros.append(bot.call_action("get_group_info", group_id=gid_int))
                valid_pending.append((kind, id_))
            else:
                uid_str = self._extract_private_user_id(id_)
                try:
                    uid_int = int(uid_str)
                except ValueError:
                    logger.warning(f"[MsgStat] 无效的用户ID: {id_} → {uid_str}")
                    continue
                coros.append(bot.call_action("get_stranger_info", user_id=uid_int))
                valid_pending.append((kind, id_))

        if not coros:
            return

        results = await asyncio.gather(*coros, return_exceptions=True)
        for (kind, id_), result in zip(valid_pending, results):
            if isinstance(result, Exception):
                continue
            try:
                if kind == "group":
                    name = result.get("group_name") or result.get("data", {}).get("group_name", "")
                    if name:
                        self._group_name_cache[id_] = name
                else:
                    name = result.get("nickname") or result.get("data", {}).get("nickname", "")
                    if name:
                        self._user_name_cache[id_] = name
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════
    #  日期变更检测 & 每日报告
    # ══════════════════════════════════════════════════════════

    async def _check_day_change(self, event: AstrMessageEvent | None):
        """检测是否跨天，如果是则触发昨日报告。"""
        if not self.config.get("daily_report_enabled", True):
            return

        today = _today()
        if today == self._today_str:
            return  # 同一天

        # ── 跨天了 ──
        yesterday = self._today_str  # 刚过去的那天
        self._yesterday_str = yesterday
        self._today_str = today

        # 保存昨天的消息快照
        yesterday_groups = dict(self._daily_group_msgs)
        yesterday_private = dict(self._daily_private_msgs)
        yesterday_tokens = self._daily_tokens

        # 重置每日计数器
        self._daily_group_msgs.clear()
        self._daily_private_msgs.clear()
        self._daily_tokens = TokenUsage()

        # 清理死 key（超过 24 小时无活动的空记录）
        self._cleanup_dead_keys()

        # 发送报告（加锁防止并发重复发送）
        async with self._report_lock:
            if self._last_report_date == yesterday:
                return  # 已经发过了
            await self._send_daily_report(
                event, yesterday, yesterday_groups, yesterday_private, yesterday_tokens
            )
            self._last_report_date = yesterday
            # 报告发完，全局清理过期消息记录，防止堆成屎山
            await self._cleanup_all_expired()
            await self._save_data()

    async def _send_daily_report(
        self,
        event: AstrMessageEvent | None,
        date_str: str,
        groups: dict[str, int],
        private: dict[str, int],
        tokens: TokenUsage,
    ):
        """构建并发送每日报告到配置的群聊。"""
        report_group = self.config.get("report_group_id", "").strip()
        if not report_group:
            logger.warning("[MsgStat] 未配置 report_group_id，跳过每日报告")
            return

        # 获取 platform_id
        if event:
            platform_id = event.get_platform_id()
        else:
            # 没有 event 时从上下文获取第一个可用平台
            platforms = getattr(self.context, "platform_manager", None)
            if platforms and platforms.platform_insts:
                platform_id = platforms.platform_insts[0].meta().id
            else:
                logger.error("[MsgStat] 无法获取平台ID，跳过每日报告")
                return

        # 查找名称
        group_ids = list(groups.keys())
        user_ids = list(private.keys())
        await self._resolve_names(platform_id, group_ids, user_ids)

        # 统计
        total_group = sum(groups.values())
        total_private = sum(private.values())
        total_msgs = total_group + total_private

        # 今日 token（新的一天已经开始，daily_tokens 已重置，所以今日是 0）
        # 累计 token = 文件中的 + 昨天 + 今天（今天=0，因为刚重置）
        cumulative = self._total_tokens

        # 构建报告
        lines = []
        lines.append(f"📊 每日统计报告 — {date_str}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # ── 消息统计 ──
        lines.append("【消息统计】")
        lines.append(f"群聊 {len(group_ids)} 个群，共 {total_group} 条")
        if group_ids:
            for gid in sorted(group_ids, key=lambda x: groups[x], reverse=True):
                label = self._name_with_cache(self._group_name_cache, gid)
                lines.append(f"  {label}：{groups[gid]} 条")
        else:
            lines.append("  （无）")
        lines.append(f"私聊 {len(user_ids)} 个用户，共 {total_private} 条")
        if user_ids:
            for uid in sorted(user_ids, key=lambda x: private[x], reverse=True):
                label = self._name_with_cache(self._user_name_cache, uid)
                lines.append(f"  {label}：{private[uid]} 条")
        else:
            lines.append("  （无）")
        lines.append(f"消息总数：{total_msgs} 条")
        lines.append("")

        # ── Token 统计 ──
        lines.append("【Token 统计】")
        lines.append(f"昨日消耗：{self._format_token(tokens.total)} tokens")
        lines.append(f"  ├ 输入：{self._format_token(tokens.input)}")
        lines.append(f"  └ 输出：{self._format_token(tokens.output)}")
        lines.append(f"累计总量：{self._format_token(cumulative.total)} tokens")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        report_text = "\n".join(lines)
        session_str = f"{platform_id}:GroupMessage:{report_group}"
        chain = MessageChain().message(report_text)

        try:
            await self.context.send_message(session_str, chain)
            logger.info(f"[MsgStat] 每日报告已发送到群 {report_group} | {date_str}")
        except Exception as e:
            logger.error(f"[MsgStat] 发送每日报告失败: {e}")

    # ══════════════════════════════════════════════════════════
    #  Hook: LLM 响应 → Token 追踪
    # ══════════════════════════════════════════════════════════

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, response: LLMResponse):
        """追踪每次 LLM 调用的 Token 消耗。"""
        if not self.config.get("track_tokens_enabled", True):
            return

        usage = getattr(response, "usage", None)
        if not usage:
            return

        # 累加到今日
        self._daily_tokens = self._daily_tokens + usage
        # 累加到总计
        self._total_tokens = self._total_tokens + usage

        # 保存持久化数据
        await self._save_data()

        # 检查跨天
        await self._check_day_change(event)

    # ══════════════════════════════════════════════════════════
    #  Hook: 消息发送后 → 消息记录
    # ══════════════════════════════════════════════════════════

    @filter.after_message_sent()
    async def on_msg_sent(self, event: AstrMessageEvent):
        result = event.get_result()
        if not result:
            return

        # 提取消息文本（修复 plain 未定义问题）
        if hasattr(result, 'get_plain_text'):
            plain = result.get_plain_text()
        elif hasattr(result, 'chain'):
            plain = "".join(c.text for c in result.chain if hasattr(c, 'text'))
        else:
            plain = str(result)

        self_id = event.get_self_id()
        display_name = self.config.get("bot_display_name", "") or self_id
        now = time.time()

        group_id = event.get_group_id()

        if group_id:
            self._group_records[group_id].append((now, plain, self_id, display_name))
            self._daily_group_msgs[group_id] += 1
            last = self._last_clean_group.get(group_id, 0)
            if now - last > 300 or len(self._group_records[group_id]) > 300:
                self._clean_records(self._group_records, group_id, now)
                self._last_clean_group[group_id] = now
        else:
            raw_session = event.session_id
            if raw_session:
                # 从 session_id 提取纯数字用户 ID（如 123456789），
                # 避免用一长串 aiocqhttp:PrivateMessage:xxx 做 key
                user_id = self._extract_private_user_id(raw_session)
                self._private_records[user_id].append((now, plain, self_id, display_name))
                self._daily_private_msgs[user_id] += 1
                last = self._last_clean_private.get(user_id, 0)
                if now - last > 300 or len(self._private_records[user_id]) > 300:
                    self._clean_records(self._private_records, user_id, now)
                    self._last_clean_private[user_id] = now

        # 保存持久化数据（每日消息快照）
        await self._save_data()

        # 检查跨天
        await self._check_day_change(event)

    # ══════════════════════════════════════════════════════════
    #  指令：看看消息
    # ══════════════════════════════════════════════════════════

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("看看消息")
    async def show_stats(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("这个命令只能在群聊中使用哦~")
            return

        self._clean_records(self._group_records, group_id)
        records = self._group_records[group_id]
        now = time.time()
        count_1h = len(records)
        count_30m = sum(1 for r in records if now - r[0] <= 1800)

        lines = []
        lines.append("📊 我在本群的发言统计")
        lines.append("━━━━━━━━━━━━━━")
        lines.append(f"近 1 小时：{count_1h} 条")
        lines.append(f"近 30 分钟：{count_30m} 条")

        # 可选：附带 Token 统计
        if self.config.get("show_tokens_in_msg_stats", False):
            lines.append("")
            lines.append("【今日 Token】")
            lines.append(f"输入：{self._format_token(self._daily_tokens.input)}")
            lines.append(f"输出：{self._format_token(self._daily_tokens.output)}")
            lines.append(f"合计：{self._format_token(self._daily_tokens.total)}")
            lines.append(f"累计：{self._format_token(self._total_tokens.total)}")

        lines.append("━━━━━━━━━━━━━━")
        yield event.plain_result("\n".join(lines))

        if not records:
            yield event.plain_result("（最近 1 小时没有发言记录）")
            return

        max_forward = self.config.get("max_forward_count", 90)
        max_length = self.config.get("max_message_length", 500)
        recent = records[-max_forward:]
        nodes = []
        for ts, text, uin, name in recent:
            display_text = text if len(text) <= max_length else text[:max_length] + "…"
            node = Node(content=[Plain(display_text)], uin=uin, name=name)
            nodes.append(node)
        yield event.chain_result([Nodes(nodes=nodes)])

    # ══════════════════════════════════════════════════════════
    #  指令：看看全部消息
    # ══════════════════════════════════════════════════════════

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("看看全部消息")
    async def show_all_stats(self, event: AstrMessageEvent):
        now = time.time()
        platform_id = event.get_platform_id()

        # 群聊
        group_keys = list(self._group_records.keys())
        for gid in group_keys:
            self._clean_records(self._group_records, gid, now)
        active_group_ids = []
        group_1h = 0
        group_30m = 0
        group_stats = {}
        for gid in sorted(group_keys):
            records = self._group_records[gid]
            if not records:
                continue
            active_group_ids.append(gid)
            c1 = len(records)
            c30 = sum(1 for r in records if now - r[0] <= 1800)
            group_1h += c1
            group_30m += c30
            group_stats[gid] = (c1, c30)

        # 私聊
        private_keys = list(self._private_records.keys())
        for uid in private_keys:
            self._clean_records(self._private_records, uid, now)
        active_user_ids = []
        private_1h = 0
        private_30m = 0
        private_stats = {}
        for uid in sorted(private_keys):
            records = self._private_records[uid]
            if not records:
                continue
            active_user_ids.append(uid)
            c1 = len(records)
            c30 = sum(1 for r in records if now - r[0] <= 1800)
            private_1h += c1
            private_30m += c30
            private_stats[uid] = (c1, c30)

        await self._resolve_names(platform_id, active_group_ids, active_user_ids)

        total_1h = group_1h + private_1h
        total_30m = group_30m + private_30m

        lines = []
        lines.append("📊 AI 全局发言统计")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"总计：1小时{total_1h}条  30分钟{total_30m}条")
        lines.append("")

        active_groups = len(active_group_ids)
        lines.append(f"【群聊】{active_groups} 个群  1h={group_1h}条  30m={group_30m}条")
        if active_group_ids:
            for gid in sorted(active_group_ids):
                c1, c30 = group_stats[gid]
                label = self._name_with_cache(self._group_name_cache, gid)
                lines.append(f"  {label}：1h={c1}条  30m={c30}条")
        else:
            lines.append("  （无记录）")
        lines.append("")

        active_users = len(active_user_ids)
        lines.append(f"【私聊】{active_users} 个用户  1h={private_1h}条  30m={private_30m}条")
        if active_user_ids:
            for uid in sorted(active_user_ids):
                c1, c30 = private_stats[uid]
                label = self._name_with_cache(self._user_name_cache, uid)
                lines.append(f"  {label}：1h={c1}条  30m={c30}条")
        else:
            lines.append("  （无记录）")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        # 可选：附带 Token 统计
        if self.config.get("show_tokens_in_all_stats", False):
            lines.append("")
            lines.append("【Token 统计】")
            lines.append(f"今日消耗：{self._format_token(self._daily_tokens.total)} tokens")
            lines.append(f"  ├ 输入：{self._format_token(self._daily_tokens.input)}")
            lines.append(f"  └ 输出：{self._format_token(self._daily_tokens.output)}")
            lines.append(f"累计总量：{self._format_token(self._total_tokens.total)} tokens")
            lines.append("━━━━━━━━━━━━━━━━━━━━")

        report_text = "\n".join(lines)
        report_group = self.config.get("report_group_id", "").strip()
        if not report_group:
            yield event.plain_result(report_text)
            logger.warning("[MsgStat] 未配置 report_group_id，报表发回当前对话")
            return

        session_str = f"{platform_id}:GroupMessage:{report_group}"
        chain = MessageChain().message(report_text)
        try:
            success = await self.context.send_message(session_str, chain)
            if success:
                yield event.plain_result("喵～汇报已经送过去啦 ✨")
            else:
                yield event.plain_result("❌ 发送失败，未找到平台适配器")
        except Exception as e:
            logger.error(f"[MsgStat] 发送全局报表失败: {e}")
            yield event.plain_result(f"❌ 发送报表失败：{e}")

    # ══════════════════════════════════════════════════════════
    #  指令：看看token
    # ══════════════════════════════════════════════════════════

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("看看token")
    async def show_tokens(self, event: AstrMessageEvent):
        today = self._daily_tokens
        total = self._total_tokens

        lines = []
        lines.append("📊 Token 用量统计")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("【今日用量】")
        lines.append(f"输入：{self._format_token(today.input)} tokens")
        lines.append(f"输出：{self._format_token(today.output)} tokens")
        lines.append(f"合计：{self._format_token(today.total)} tokens")
        lines.append("")
        lines.append("【累计总量】")
        lines.append(f"输入：{self._format_token(total.input)} tokens")
        lines.append(f"输出：{self._format_token(total.output)} tokens")
        lines.append(f"合计：{self._format_token(total.total)} tokens")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

        yield event.plain_result("\n".join(lines))
