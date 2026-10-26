"""触发决策：这条消息要不要回、为什么。

保持纯函数式：所有外部状态（权限、静默、冷却、随机数）都由调用方传进来，
方便表驱动测试。真正的副作用（限流计数、LLM 相关性判定）不在这里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Config

# 决策结果
REPLY = "reply"
PROACTIVE = "proactive_candidate"
IGNORE = "ignore"


@dataclass(slots=True)
class TriggerInput:
    scope_type: str  # group / private
    scope_id: int
    user_id: int
    self_id: int
    text: str = ""
    at_ids: list[int] = field(default_factory=list)
    reply_id: int | None = None
    is_self: bool = False
    quoted_is_me: bool = False  # 引用的那条消息是机器人发的
    other_bot: bool = False  # 发送者是另一个机器人
    allowed: bool = True  # 白名单是否放行
    is_quiet: bool = False  # 当前是否静默时段
    proactive_ready: bool = True  # 主动插话冷却/配额是否允许
    rng_value: float = 1.0  # 预先取好的 [0,1) 随机数
    has_content: bool = True  # 是否包含文本/图片等可读内容


@dataclass(slots=True)
class TriggerDecision:
    action: str
    reason: str

    @property
    def should_reply(self) -> bool:
        return self.action in (REPLY, PROACTIVE)

    @property
    def is_proactive(self) -> bool:
        return self.action == PROACTIVE

    def __bool__(self) -> bool:
        return self.should_reply


_WS = re.compile(r"\s+")


def _matched_keyword(text: str, keywords: list[str]) -> str | None:
    if not keywords:
        return None
    compact = _WS.sub("", text or "")
    for keyword in keywords:
        if keyword and (keyword in text or keyword in compact):
            return keyword
    return None


def _matched_alias(text: str, aliases: list[str]) -> str | None:
    return _matched_keyword(text, aliases)


def decide(config: Config, inp: TriggerInput) -> TriggerDecision:
    """核心判定。顺序即优先级，改顺序会改变行为，测试里有对应用例。"""
    if config.readonly:
        return TriggerDecision(IGNORE, "readonly")
    if inp.is_self:
        return TriggerDecision(IGNORE, "self")
    if not inp.allowed:
        return TriggerDecision(IGNORE, "not_allowed")
    if inp.user_id in config.access.blocked_users:
        return TriggerDecision(IGNORE, "blocked_user")
    if not inp.has_content:
        return TriggerDecision(IGNORE, "empty")

    if inp.scope_type == "group":
        return _decide_group(config, inp)
    return _decide_private(config, inp)


def _decide_group(config: Config, inp: TriggerInput) -> TriggerDecision:
    group_cfg = config.trigger.group

    if group_cfg.at_or_reply:
        if inp.self_id in inp.at_ids:
            return TriggerDecision(REPLY, "at_me")
        if inp.reply_id is not None and inp.quoted_is_me:
            return TriggerDecision(REPLY, "reply_to_me")

    keyword = _matched_keyword(inp.text, group_cfg.keywords)
    if keyword:
        return TriggerDecision(REPLY, f"keyword:{keyword}")

    alias = _matched_alias(inp.text, config.bot.aliases)
    if alias:
        return TriggerDecision(REPLY, f"alias:{alias}")

    if inp.other_bot and not group_cfg.reply_to_other_bot:
        return TriggerDecision(IGNORE, "other_bot")

    # ---- 以下都是"主动插话"，条件更严 ----
    if config.passive:
        return TriggerDecision(IGNORE, "passive_mode")
    if not config.trigger.proactive.enabled:
        return TriggerDecision(IGNORE, "proactive_disabled")
    if inp.is_quiet and config.rate_limit.quiet_group_proactive:
        return TriggerDecision(IGNORE, "quiet_hours")
    if not inp.proactive_ready:
        return TriggerDecision(IGNORE, "proactive_cooldown")
    if inp.rng_value >= config.trigger.proactive.probability:
        return TriggerDecision(IGNORE, "proactive_roll")

    return TriggerDecision(PROACTIVE, "proactive")


def _decide_private(config: Config, inp: TriggerInput) -> TriggerDecision:
    if inp.is_quiet and not config.rate_limit.quiet_private_passive:
        return TriggerDecision(IGNORE, "quiet_hours")
    if config.trigger.private.always_reply:
        return TriggerDecision(REPLY, "private")
    alias = _matched_alias(inp.text, config.bot.aliases)
    if alias:
        return TriggerDecision(REPLY, "private_alias")
    return TriggerDecision(IGNORE, "private_not_always")
