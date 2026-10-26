"""并发闸门。

QQ 机器人最常见的翻车方式之一，是被瞬间涌进来的消息拖垮：
模型调用排队、内存堆积、最后要么全超时，要么一次吐出十几条回复。

这里定三条规矩：
    1. 全局最多 `max_parallel` 个会话同时生成回复（默认 6）
    2. 同一个会话同一时间只处理一条（新消息直接丢，不排队——排队只会越欠越多）
    3. 等待队列有上限，排满了就丢，宁可少回一句也不要把自己挤爆
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

log = logging.getLogger("ricefish.concurrency")


class Busy(Exception):
    """拿不到执行槽位。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ConcurrencyGate:
    def __init__(
        self,
        max_parallel: int = 6,
        max_queue: int = 24,
        acquire_timeout: float = 60.0,
    ) -> None:
        self.max_parallel = max(1, int(max_parallel))
        self.max_queue = max(0, int(max_queue))
        self.acquire_timeout = max(1.0, float(acquire_timeout))
        self._sem = asyncio.Semaphore(self.max_parallel)
        self._active: dict[str, float] = {}
        self._waiting = 0
        self.stats = {
            "active": 0,
            "peak": 0,
            "dropped_busy": 0,
            "dropped_queue_full": 0,
            "timeouts": 0,
            "completed": 0,
        }

    # ------------------------------------------------------------------
    def busy(self, scope_key: str) -> bool:
        return scope_key in self._active

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def waiting_count(self) -> int:
        return self._waiting

    # ------------------------------------------------------------------
    @asynccontextmanager
    async def slot(self, scope_key: str) -> AsyncIterator[None]:
        """占一个槽位；拿不到就抛 Busy，由上层决定怎么记日志。"""
        if scope_key in self._active:
            self.stats["dropped_busy"] += 1
            raise Busy("same_scope")

        if len(self._active) >= self.max_parallel:
            if self._waiting >= self.max_queue:
                self.stats["dropped_queue_full"] += 1
                raise Busy("queue_full")
            self._waiting += 1
            queued = True
        else:
            queued = False

        try:
            await asyncio.wait_for(self._sem.acquire(), self.acquire_timeout)
        except asyncio.TimeoutError as exc:
            self.stats["timeouts"] += 1
            raise Busy("timeout") from exc
        finally:
            if queued:
                self._waiting -= 1

        self._active[scope_key] = time.monotonic()
        self.stats["active"] = len(self._active)
        self.stats["peak"] = max(self.stats["peak"], len(self._active))
        try:
            yield
        finally:
            self._active.pop(scope_key, None)
            self.stats["active"] = len(self._active)
            self.stats["completed"] += 1
            self._sem.release()

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "max_parallel": self.max_parallel,
            "max_queue": self.max_queue,
            "active": len(self._active),
            "waiting": self._waiting,
            "scopes": {
                key: round(now - started, 1) for key, started in self._active.items()
            },
            **{k: v for k, v in self.stats.items() if k != "active"},
        }
