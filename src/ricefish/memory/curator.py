"""记忆策展器：决定什么时候抽取、什么时候摘要，并控制预算。

聊天主流程不等待它；它由定时 tick 驱动，静默几分钟后自然跑一次。
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..config import Config
from ..llm.extractor import Extractor
from .store import MemoryStore

log = logging.getLogger("ricefish.curator")


class Curator:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        extractor: Extractor,
        concurrency: int = 2,
        semantic=None,
    ) -> None:
        self.config = config
        self.store = store
        self.extractor = extractor
        self.semantic = semantic
        self._sem = asyncio.Semaphore(concurrency)
        self._running: set[str] = set()
        self.stats = {"extracted": 0, "summarized": 0, "skipped_budget": 0, "errors": 0}

    # -- 游标 --------------------------------------------------------
    def _cursor(self, kind: str, scope_key: str) -> int:
        try:
            return int(self.store.get_state(f"cur:{kind}:{scope_key}") or 0)
        except ValueError:
            return 0

    def _set_cursor(self, kind: str, scope_key: str, value: int) -> None:
        self.store.set_state(f"cur:{kind}:{scope_key}", value)

    def _budget_left(self, scope_key: str) -> bool:
        day = time.strftime("%Y-%m-%d")
        key = f"budget:extract:{scope_key}:{day}"
        try:
            used = int(self.store.get_state(key) or 0)
        except ValueError:
            used = 0
        if used >= self.config.memory.extractor.daily_budget_per_chat:
            return False
        self.store.set_state(key, used + 1)
        return True

    # -- 主循环 ------------------------------------------------------
    async def tick(self) -> None:
        if not self.config.memory.extractor.enabled:
            return
        since = int(time.time()) - 3 * 86400
        for scope_type, scope_id in self.store.active_scopes(since):
            scope_key = f"{scope_type}:{scope_id}"
            if scope_key in self._running:
                continue
            try:
                await self._maybe_process(scope_type, scope_id, scope_key)
            except Exception:
                self.stats["errors"] += 1
                log.exception("记忆策展失败 %s", scope_key)

    async def _maybe_process(
        self, scope_type: str, scope_id: int, scope_key: str
    ) -> None:
        last_id = self.store.last_message_id(scope_type, scope_id)
        last_ts = self.store.last_message_ts(scope_type, scope_id)
        if last_id <= 0:
            return
        idle = time.time() - last_ts

        extract_cursor = self._cursor("extract", scope_key)
        pending = last_id - extract_cursor
        extract_cfg = self.config.memory.extractor
        want_extract = pending >= extract_cfg.every_messages or (
            pending > 0 and idle >= extract_cfg.idle_minutes * 60
        )

        summary_cursor = self._cursor("summary", scope_key)
        pending_summary = last_id - summary_cursor
        summary_cfg = self.config.memory
        want_summary = pending_summary >= summary_cfg.summary_every_messages or (
            pending_summary > 0 and idle >= summary_cfg.summary_idle_minutes * 60
        )

        if not want_extract and not want_summary:
            return

        self._running.add(scope_key)
        try:
            async with self._sem:
                if want_extract:
                    if self._budget_left(scope_key):
                        messages = self.store.messages_after(
                            scope_type, scope_id, extract_cursor, 60
                        )
                        if messages:
                            added = await self.extractor.extract(
                                scope_type,
                                scope_id,
                                messages,
                                subject_qq=_last_human(messages),
                            )
                            self.stats["extracted"] += added
                            self._set_cursor("extract", scope_key, messages[-1]["id"])
                    else:
                        self.stats["skipped_budget"] += 1
                if self.semantic is not None and getattr(self.semantic, "enabled", False):
                    try:
                        await self.semantic.index_pending(scope_type, scope_id)
                    except Exception:
                        log.debug("向量索引失败 %s", scope_key, exc_info=True)
                if want_summary:
                    messages = self.store.messages_after(
                        scope_type, scope_id, summary_cursor, 80
                    )
                    if messages and len(messages) >= 5:
                        text = await self.extractor.summarize(
                            scope_type, scope_id, messages
                        )
                        if text:
                            self.stats["summarized"] += 1
                        self._set_cursor("summary", scope_key, messages[-1]["id"])
                    elif messages:
                        self._set_cursor("summary", scope_key, messages[-1]["id"])
        finally:
            self._running.discard(scope_key)

    async def run_forever(self, interval: float = 30.0) -> None:
        log.info("记忆策展器已启动（每 %.0fs 检查一次）", interval)
        while True:
            try:
                await asyncio.sleep(interval)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("策展器循环异常")


def _last_human(messages: list[dict]) -> int:
    for item in reversed(messages):
        if not item.get("is_bot") and item.get("sender_qq"):
            return int(item["sender_qq"])
    return 0
