"""主动关怀：很久没聊天的人，主动私聊一句。

默认关闭（config.care.enabled = false）。开了以后也是严格控制频率：
每个人 cooldown_days 天内最多一次，每天全站最多 max_per_day 次。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from ..config import Config
from ..core.ratelimit import RateLimiter
from ..core.sender import Sender
from ..llm.client import DeepSeekClient, LLMError
from ..llm.prompt import PromptBuilder
from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.care")


class CareTaker:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        llm: DeepSeekClient,
        prompts: PromptBuilder,
        sender: Sender,
        limiter: RateLimiter,
    ) -> None:
        self.config = config
        self.store = store
        self.llm = llm
        self.prompts = prompts
        self.sender = sender
        self.limiter = limiter

    async def run_once(self) -> int:
        if not self.config.care.enabled:
            return 0
        if self.config.readonly or self.config.bot.dry_run:
            log.info("care: dry-run 或只读模式，跳过主动关怀")
            return 0
        sent = 0
        now = time.time()
        for friend_qq in self.config.access.allowed_friends:
            last_ts = self.store.last_message_ts("private", friend_qq)
            if not last_ts:
                continue
            idle_days = (now - last_ts) / 86400.0
            if idle_days < self.config.care.idle_days:
                continue
            verdict = self.limiter.check_care(
                friend_qq, self.config.care.cooldown_days, self.config.care.max_per_day
            )
            if not verdict:
                log.debug("care 跳过 %s: %s", friend_qq, verdict.reason)
                continue
            text = await self._compose(friend_qq, int(idle_days))
            if not text:
                continue
            try:
                await self.sender.send_text(
                    "private", friend_qq, text, quote=False, reason="care"
                )
            except Exception as exc:
                log.warning("主动关怀发送失败 %s: %s", friend_qq, exc)
                continue
            self.limiter.note_care(friend_qq)
            self.store.add_message(
                "private",
                friend_qq,
                sender_qq=0,
                sender_name=self.config.bot.name,
                text=text,
                is_bot=True,
            )
            sent += 1
        if sent:
            log.info("本轮主动关怀发送 %d 条", sent)
        return sent

    async def _compose(self, friend_qq: int, idle_days: int) -> str:
        profile = self.store.get_profile("private", friend_qq, friend_qq) or {}
        name = profile.get("display_name") or self.store.get_person(friend_qq) or {}
        if isinstance(name, dict):
            name = name.get("nickname") or str(friend_qq)
        summaries = self.store.latest_summaries("private", friend_qq, limit=1)
        topic = summaries[0]["content"] if summaries else ""
        system, user = self.prompts.care_prompt(str(name), idle_days, topic)
        try:
            text = await self.llm.complete(
                system, user, temperature=1.0, max_tokens=120, purpose="care"
            )
        except LLMError as exc:
            log.debug("care 生成失败: %s", exc)
            return ""
        text = (text or "").strip().strip('"“”')
        return text[:120]

    @staticmethod
    def should_run_now(hour: int = 11) -> bool:
        return datetime.now().hour == hour
