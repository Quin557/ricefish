"""主动陪伴：隔一阵没说话，主动发一条像朋友的消息。

和 care 的区别：care 是"好几天没联系"的关怀；这个更像日常陪伴——
私聊聊完一阵没动静，就轻轻接一句。受全局开关、每会话开关、静默时段、
冷却、每日配额限制，默认只对白名单好友生效。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from ..config import Config
from ..core.ratelimit import RateLimiter
from ..core.sender import Sender
from ..llm.client import DeepSeekClient, LLMError
from ..llm.prompt import PromptBuilder
from ..memory.store import MemoryStore
from ..util.text import now_local

log = logging.getLogger("ricefish.companion")


class CompanionPinger:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        llm: DeepSeekClient,
        prompts: PromptBuilder,
        sender: Sender,
        limiter: RateLimiter,
        clock: Callable[[], float] | None = None,
        mood=None,
    ) -> None:
        self.config = config
        self.store = store
        self.llm = llm
        self.prompts = prompts
        self.sender = sender
        self.limiter = limiter
        self.clock = clock or time.time
        self.mood = mood
        self.sent_total = 0

    # -- 入口 --------------------------------------------------------
    async def run_once(self) -> int:
        cfg = self.config.companion
        if not cfg.enabled or self.config.readonly:
            return 0
        now = self.clock()
        local = now_local()
        if cfg.respect_quiet_hours and self.config.rate_limit.is_quiet(
            local.hour, local.minute
        ):
            log.debug("companion: 静默时段，跳过")
            return 0
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._state_int(f"companion:total:{day}") >= cfg.daily_total:
            return 0
        if self.config.bot.dry_run:
            log.debug("companion: dry-run 模式，只检查不发送")
            return 0

        friends = sorted(
            self.config.access.allowed_friends,
            key=lambda qq: self.store.last_message_ts("private", qq),
            reverse=True,
        )
        sent = 0
        for friend_qq in friends:
            if sent >= cfg.max_per_tick:
                break
            if self._state_int(f"companion:total:{day}") >= cfg.daily_total:
                break
            try:
                if await self._maybe_ping(int(friend_qq), now, day):
                    sent += 1
            except Exception:
                log.exception("companion: 处理 %s 失败", friend_qq)
        if sent:
            self.sent_total += sent
            log.info("companion: 本轮主动发送 %d 条", sent)
        return sent

    # -- 单个会话 ----------------------------------------------------
    async def _maybe_ping(self, friend_qq: int, now: float, day: str) -> bool:
        cfg = self.config.companion
        chat = self.store.get_chat("private", friend_qq) or {}
        if not chat.get("enabled", 1):
            return False
        if not chat.get("proactive_enabled", 1):
            return False

        recent = self.store.scope_recent("private", friend_qq, limit=6)
        if not recent:
            return False
        last = recent[0]
        last_ts = int(last.get("ts") or 0)
        if last_ts <= 0:
            return False
        last_is_bot = bool(last.get("is_bot"))
        last_ping = self._state_int(f"companion:last:{friend_qq}")
        if last_is_bot and last_ping >= last_ts:
            # 上一条就是自己主动发的，等对方回了再说
            return False

        threshold = cfg.follow_up_minutes if last_is_bot else cfg.idle_minutes
        if now - last_ts < threshold * 60:
            return False
        if last_ping and now - last_ping < cfg.cooldown_minutes * 60:
            return False
        if self._state_int(f"companion:day:{friend_qq}:{day}") >= cfg.daily_per_chat:
            return False

        text = await self._compose(friend_qq, recent, last_is_bot, now)
        if not text:
            return False
        scope_key = f"private:{friend_qq}"
        if self.limiter.is_duplicate(scope_key, text):
            log.debug("companion: 内容重复，跳过 %s", friend_qq)
            return False
        try:
            await self.sender.send_text(
                "private", friend_qq, text, quote=False, reason="companion"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("companion: 发送失败 %s: %s", friend_qq, exc)
            return False

        self.store.add_message(
            "private",
            friend_qq,
            sender_qq=0,
            sender_name=self.config.bot.name,
            text=text,
            is_bot=True,
        )
        self.store.set_state(f"companion:last:{friend_qq}", int(now))
        self._bump(f"companion:day:{friend_qq}:{day}")
        self._bump(f"companion:total:{day}")
        log.info("companion: 主动私聊 %s -> %s", friend_qq, text[:60])
        return True

    async def _compose(
        self,
        friend_qq: int,
        recent: list[dict],
        last_is_bot: bool,
        now: float,
    ) -> str:
        profile = self.store.get_profile("private", friend_qq, friend_qq) or {}
        person = self.store.get_person(friend_qq) or {}
        name = (
            profile.get("display_name")
            or person.get("nickname")
            or str(friend_qq)
        )
        summaries = self.store.latest_summaries("private", friend_qq, limit=1)
        topic = str(summaries[0].get("content") or "") if summaries else ""
        episodes = self.store.list_episodes("private", friend_qq, limit=1)
        if episodes and not topic:
            topic = str(episodes[0].get("summary") or "")
        relationship_events = self.store.list_relationship_events(
            "private", friend_qq, subject_qq=friend_qq, limit=3
        )
        memories = [
            str(item.get("content") or "").strip()
            for item in self.store.list_memories(
                "private", friend_qq, qq=friend_qq, limit=4
            )
            if str(item.get("content") or "").strip()
        ][:3]
        open_loops = self.store.list_open_loops(
            "private", friend_qq, subject_qq=friend_qq, limit=3
        )
        affinity = max(0, min(100, int(profile.get("affinity") or 50)))
        mood_text = ""
        if self.mood is not None:
            try:
                mood_text = str(self.mood.describe())
            except Exception:  # noqa: BLE001
                mood_text = ""
        local = now_local()
        lines: list[str] = []
        for item in reversed(recent[:6]):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            who = self.config.bot.name if item.get("is_bot") else (
                item.get("sender_name") or "对方"
            )
            lines.append(f"{who}: {text}")
        system, user = self.prompts.companion_prompt(
            str(name),
            topic,
            "\n".join(lines),
            local.strftime("%m-%d %H:%M"),
            last_is_bot,
            relation=str(profile.get("relation") or ""),
            affinity=f"{affinity}（{_affinity_label(affinity)}）",
            tags=str(profile.get("tags") or ""),
            preferences=str(profile.get("preferences") or ""),
            notes=str(profile.get("notes") or ""),
            memories=memories,
            open_loops=[str(item.get("content") or "") for item in open_loops],
            relationship_events=[
                str(item.get("content") or "") for item in relationship_events
            ],
            mood=mood_text,
            period=_period(local.hour),
        )
        try:
            text = await self.llm.complete(
                system,
                user,
                temperature=1.05,
                max_tokens=140,
                purpose="companion",
                thinking="disabled",
            )
        except LLMError as exc:
            log.debug("companion: 生成失败: %s", exc)
            return ""
        return (text or "").strip().strip('"“”').strip()[:140]

    # -- 状态 --------------------------------------------------------
    def _state_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.store.get_state(key) or default)
        except (TypeError, ValueError):
            return default

    def _bump(self, key: str) -> None:
        self.store.set_state(key, self._state_int(key) + 1)

    def snapshot(self) -> dict:
        day = time.strftime("%Y-%m-%d")
        return {
            "enabled": bool(self.config.companion.enabled),
            "sent_total": self.sent_total,
            "today": self._state_int(f"companion:total:{day}"),
        }


def _affinity_label(value: int) -> str:
    if value <= 20:
        return "陌生"
    if value <= 40:
        return "眼熟"
    if value <= 60:
        return "熟人"
    if value <= 80:
        return "亲近"
    return "很熟"


def _period(hour: int) -> str:
    if hour < 6:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 20:
        return "傍晚"
    if hour < 23:
        return "晚上"
    return "深夜"
