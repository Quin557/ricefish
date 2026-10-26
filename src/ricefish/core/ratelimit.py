"""限流与去重：防止刷屏、防止账号被风控。

全部是内存态滑动窗口（重启归零）。日配额按自然日重置。
时间通过 `clock` 注入，方便测试。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ..config import RateLimitConfig
from ..util.text import normalize


@dataclass(slots=True)
class LimitVerdict:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


OK = LimitVerdict(True)


@dataclass
class _DayCounter:
    day: str = ""
    count: int = 0

    def hit(self, day: str) -> int:
        if self.day != day:
            self.day = day
            self.count = 0
        return self.count

    def incr(self, day: str) -> None:
        self.hit(day)
        self.count += 1


class RateLimiter:
    def __init__(
        self,
        cfg: RateLimitConfig,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.clock = clock or time.time
        self._group_min: dict[int, deque[float]] = {}
        self._group_day: dict[int, _DayCounter] = {}
        self._user_hour: dict[int, deque[float]] = {}
        self._proactive_last: dict[int, float] = {}
        self._proactive_day: dict[int, _DayCounter] = {}
        self._recent: dict[str, deque[tuple[float, str]]] = {}
        self._care_last: dict[int, float] = {}
        self._care_day = _DayCounter()
        # 主动插话参数由 trigger.proactive 注入，避免模块间循环依赖
        self._proactive_interval = 600
        self._proactive_quota = 20

    # -- 内部 --------------------------------------------------------
    def _day(self, now: float) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(now))

    def _prune(self, bucket: deque[float], now: float, window: float) -> None:
        while bucket and now - bucket[0] > window:
            bucket.popleft()

    def _prune_pairs(
        self, bucket: deque[tuple[float, str]], now: float, window: float
    ) -> None:
        while bucket and now - bucket[0][0] > window:
            bucket.popleft()

    # -- 发送前检查 --------------------------------------------------
    def check_group_send(self, group_id: int) -> LimitVerdict:
        now = self.clock()
        bucket = self._group_min.setdefault(group_id, deque())
        self._prune(bucket, now, 60)
        if len(bucket) >= self.cfg.group_per_min:
            return LimitVerdict(False, "group_per_min")
        counter = self._group_day.setdefault(group_id, _DayCounter())
        if counter.hit(self._day(now)) >= self.cfg.group_per_day:
            return LimitVerdict(False, "group_per_day")
        return OK

    def check_user(self, user_id: int) -> LimitVerdict:
        """user_per_hour <= 0 表示不限制单个用户。"""
        if self.cfg.user_per_hour <= 0:
            return OK
        now = self.clock()
        bucket = self._user_hour.setdefault(user_id, deque())
        self._prune(bucket, now, 3600)
        if len(bucket) >= self.cfg.user_per_hour:
            return LimitVerdict(False, "user_per_hour")
        return OK

    def check_proactive(self, group_id: int) -> LimitVerdict:
        now = self.clock()
        last = self._proactive_last.get(group_id, 0.0)
        window = self.cfg_proactive_interval()
        if window and now - last < window:
            return LimitVerdict(False, "proactive_interval")
        counter = self._proactive_day.setdefault(group_id, _DayCounter())
        if counter.hit(self._day(now)) >= self.cfg_proactive_quota():
            return LimitVerdict(False, "proactive_daily_quota")
        return OK

    # 主动插话参数来自 trigger.proactive，用 setter 注入以免循环依赖
    def set_proactive_limits(self, interval_sec: int, daily_quota: int) -> None:
        self._proactive_interval = max(0, int(interval_sec))
        self._proactive_quota = max(0, int(daily_quota))

    def cfg_proactive_interval(self) -> int:
        return self._proactive_interval

    def cfg_proactive_quota(self) -> int:
        return self._proactive_quota

    # -- 去重 --------------------------------------------------------
    def is_duplicate(self, scope_key: str, text: str) -> bool:
        key_text = normalize(text)
        if not key_text:
            return False
        now = self.clock()
        bucket = self._recent.setdefault(scope_key, deque())
        self._prune_pairs(bucket, now, self.cfg.duplicate_window_sec)
        return any(prev == key_text for _, prev in bucket)

    # -- 记账 --------------------------------------------------------
    def note_send(self, scope_type: str, scope_id: int, user_id: int, text: str) -> None:
        now = self.clock()
        scope_key = f"{scope_type}:{scope_id}"
        if scope_type == "group":
            self._group_min.setdefault(scope_id, deque()).append(now)
            self._group_day.setdefault(scope_id, _DayCounter()).incr(self._day(now))
        self._user_hour.setdefault(user_id, deque()).append(now)
        bucket = self._recent.setdefault(scope_key, deque())
        bucket.append((now, normalize(text)))
        self._prune_pairs(bucket, now, max(self.cfg.duplicate_window_sec, 60))

    def note_proactive(self, group_id: int) -> None:
        now = self.clock()
        self._proactive_last[group_id] = now
        self._proactive_day.setdefault(group_id, _DayCounter()).incr(self._day(now))

    # -- 主动关怀 ----------------------------------------------------
    def check_care(
        self, user_id: int, cooldown_days: int, max_per_day: int
    ) -> LimitVerdict:
        now = self.clock()
        if self._care_day.hit(self._day(now)) >= max_per_day:
            return LimitVerdict(False, "care_daily_quota")
        last = self._care_last.get(user_id, 0.0)
        if last and now - last < cooldown_days * 86400:
            return LimitVerdict(False, "care_cooldown")
        return OK

    def note_care(self, user_id: int) -> None:
        now = self.clock()
        self._care_last[user_id] = now
        self._care_day.incr(self._day(now))

    # -- 面板 --------------------------------------------------------
    def snapshot(self) -> dict:
        now = self.clock()
        groups = []
        for group_id, counter in self._group_day.items():
            bucket = self._group_min.get(group_id, deque())
            self._prune(bucket, now, 60)
            groups.append(
                {
                    "group_id": group_id,
                    "per_min": len(bucket),
                    "today": counter.hit(self._day(now)),
                    "proactive_today": self._proactive_day.get(
                        group_id, _DayCounter()
                    ).hit(self._day(now)),
                }
            )
        return {"groups": groups, "care_today": self._care_day.hit(self._day(now))}

    def reset(self) -> None:
        self._group_min.clear()
        self._group_day.clear()
        self._user_hour.clear()
        self._proactive_last.clear()
        self._proactive_day.clear()
        self._recent.clear()
        self._care_last.clear()
        self._care_day = _DayCounter()
