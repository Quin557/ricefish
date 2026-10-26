"""肥鱼的真实心情。

存在数据库里、跨会话延续，会随时间慢慢淡回中性。
被夸会高兴，被说胖/被冷落会闷，深夜会困——这些都会真的影响下一句话的口气。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field

from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.mood")

STATE_KEY = "mood:global"
LOW, HIGH = -5.0, 5.0
#: 每小时向 0 衰减的比例
DECAY_PER_HOUR = 0.45
#: 同一个人夸/骂，5 分钟内只算一次，避免刷屏式加分
COOLDOWN_SEC = 300

_PRAISE = re.compile(r"可爱|好棒|真棒|厉害|喜欢你|谢谢|贴心|好看|牛|强|爱你|靠谱|乖")
_INSULT = re.compile(r"胖|大肥鱼|笨|蠢|讨厌|烦人|丑|傻|滚")
_PET = re.compile(r"摸摸|摸摸头|捏捏|贴贴|抱抱")


@dataclass
class MoodState:
    score: float = 0.0
    reason: str = ""
    updated_at: int = 0
    changes: int = 0
    history: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "MoodState":
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return cls()
        if not isinstance(data, dict):
            return cls()
        state = cls()
        try:
            state.score = max(LOW, min(HIGH, float(data.get("score") or 0.0)))
        except (TypeError, ValueError):
            state.score = 0.0
        state.reason = str(data.get("reason") or "")[:40]
        state.updated_at = int(data.get("updated_at") or 0)
        state.changes = int(data.get("changes") or 0)
        history = data.get("history")
        if isinstance(history, list):
            state.history = [str(item)[:40] for item in history][-5:]
        return state


class MoodTracker:
    def __init__(self, store: MemoryStore | None, clock=time.time) -> None:
        self.store = store
        self.clock = clock
        self._last: dict[str, float] = {}

    # -- 持久化 ------------------------------------------------------
    def load(self) -> MoodState:
        raw = ""
        if self.store is not None:
            try:
                raw = self.store.get_state(STATE_KEY, "") or ""
            except Exception:  # pragma: no cover
                log.debug("读取心情失败", exc_info=True)
        state = MoodState.from_json(raw)
        state = self._decay(state, self.clock())
        return state

    def _save(self, state: MoodState) -> None:
        if self.store is None:
            return
        try:
            self.store.set_state(STATE_KEY, state.to_json())
        except Exception:  # pragma: no cover
            log.debug("保存心情失败", exc_info=True)

    def _decay(self, state: MoodState, now: float) -> MoodState:
        if state.updated_at:
            hours = max(0.0, (now - state.updated_at) / 3600.0)
            if hours > 0 and state.score:
                factor = max(0.0, 1.0 - DECAY_PER_HOUR * hours)
                state.score *= factor
                if abs(state.score) < 0.2:
                    state.score = 0.0
        state.updated_at = int(now)
        return state

    # -- 事件 --------------------------------------------------------
    def bump(self, delta: float, reason: str = "") -> MoodState:
        now = self.clock()
        state = self.load()
        if delta:
            state.score = max(LOW, min(HIGH, state.score + float(delta)))
            state.changes += 1
            if reason:
                state.reason = reason
                state.history = (state.history + [reason])[-5:]
            state.updated_at = int(now)
            self._save(state)
        return state

    def note_text(self, text: str, *, from_owner: bool = False) -> MoodState | None:
        """从一句话里读出情绪信号；没有信号就返回 None。"""
        text = (text or "").strip()
        if not text:
            return None
        weight = 1.4 if from_owner else 1.0
        reason = ""
        delta = 0.0
        if _PET.search(text):
            delta, reason = 0.35 * weight, "被摸头了"
        elif _PRAISE.search(text):
            delta, reason = 0.6 * weight, "被夸了"
        elif _INSULT.search(text):
            delta, reason = -0.5 * weight, "被说胖/被嫌弃"
        if not delta:
            return None
        key = reason or "misc"
        now = self.clock()
        if now - self._last.get(key, 0) < COOLDOWN_SEC:
            return None
        self._last[key] = now
        return self.bump(delta, reason)

    def note_poke(self) -> MoodState:
        return self.bump(0.3, "被戳了一下")

    def note_lonely(self) -> MoodState:
        return self.bump(-0.3, "没人理我")

    # -- 描述 --------------------------------------------------------
    def label(self, state: MoodState | None = None) -> str:
        score = (state or self.load()).score
        if score >= 3:
            return "心情很好，尾巴一直在拍地板"
        if score >= 1:
            return "心情不错"
        if score > -1:
            return "心情平平"
        if score > -3:
            return "有点闷闷的，不太想说话"
        return "心情非常差，谁惹谁倒霉"

    def describe(self, state: MoodState | None = None) -> str:
        state = state or self.load()
        text = self.label(state)
        if state.reason and abs(state.score) >= 0.5:
            text += f"（因为{state.reason}）"
        return text

    def snapshot(self) -> dict:
        state = self.load()
        return {
            "score": round(state.score, 2),
            "label": self.label(state),
            "reason": state.reason,
            "changes": state.changes,
            "updated_at": state.updated_at,
        }
