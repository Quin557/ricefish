"""定时任务：记忆归档、清理、每日主动关怀。"""

from __future__ import annotations

import asyncio
import logging
import time

from ..config import Config
from ..memory.store import MemoryStore
from .proactive import CareTaker
from .companion import CompanionPinger

log = logging.getLogger("ricefish.scheduler")


class Scheduler:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        caretaker: CareTaker | None = None,
        report=None,
        companion: CompanionPinger | None = None,
        care_hour: int = 11,
        report_weekday: int = 0,
        report_hour: int = 10,
    ) -> None:
        self.config = config
        self.store = store
        self.caretaker = caretaker
        self.report = report
        self.companion = companion
        self.care_hour = care_hour
        self.report_weekday = report_weekday
        self.report_hour = report_hour
        self._last_maintenance = 0.0
        self._last_vacuum = 0.0
        self._last_care_day = ""
        self._last_report_week = ""
        self._last_companion = 0.0

    async def run_forever(self, tick: float = 60.0) -> None:
        log.info("定时任务已启动（每 %.0fs 检查）", tick)
        while True:
            try:
                await asyncio.sleep(tick)
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("定时任务异常")

    async def step(self, now: float | None = None) -> None:
        now = now or time.time()
        await self._maintenance(now)
        await self._care(now)
        await self._companion(now)
        await self._weekly(now)

    async def _maintenance(self, now: float) -> None:
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now
        try:
            archived = self.store.archive_stale_memories(90)
            pruned = self.store.prune_messages(365)
            stale_loops = self.store.close_stale_open_loops(45)
            if archived or pruned or stale_loops:
                log.info(
                    "维护完成：归档记忆 %d 条，清理消息 %d 条，过期话题 %d 条",
                    archived,
                    pruned,
                    stale_loops,
                )
        except Exception:
            log.exception("维护失败")
        if now - self._last_vacuum > 7 * 86400:
            self._last_vacuum = now
            try:
                await asyncio.to_thread(self.store.vacuum)
                log.info("数据库 VACUUM 完成")
            except Exception:
                log.exception("VACUUM 失败")

    async def _care(self, now: float) -> None:
        if not self.caretaker or not self.config.care.enabled:
            return
        stamp = time.strftime("%Y-%m-%d", time.localtime(now))
        if self._last_care_day == stamp:
            return
        if time.localtime(now).tm_hour < self.care_hour:
            return
        self._last_care_day = stamp
        try:
            await self.caretaker.run_once()
        except Exception:
            log.exception("主动关怀失败")

    async def _companion(self, now: float) -> None:
        if not self.companion or not self.config.companion.enabled:
            return
        # 每分钟检查一次足够；真正发不发由 CompanionPinger 的冷却/配额决定
        if now - self._last_companion < 60:
            return
        self._last_companion = now
        try:
            await self.companion.run_once()
        except Exception:
            log.exception("主动陪伴失败")

    async def _weekly(self, now: float) -> None:
        if not self.report:
            return
        local = time.localtime(now)
        if local.tm_wday != self.report_weekday or local.tm_hour < self.report_hour:
            return
        stamp = time.strftime("%Y-%W", local)
        if self._last_report_week == stamp:
            return
        self._last_report_week = stamp
        try:
            await self.report.run(7)
            log.info("周报已发送")
        except Exception:
            log.exception("周报失败")
