"""会话视图：把一个人/一个群在这一刻的相关数据收在一起。

不是新的存储层，只是 store 上的一个只读聚合，方便 dispatcher 和面板用同一套
口径取「人设、关系、情节、开放话题、事实」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..memory.store import MemoryStore


@dataclass
class ChatSession:
    scope_type: str
    scope_id: int
    chat: dict = field(default_factory=dict)
    profiles: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)
    open_loops: list[dict] = field(default_factory=list)
    relationship_events: list[dict] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    summary: str = ""
    persona_path: Path | None = None

    @classmethod
    def load(
        cls,
        store: MemoryStore,
        scope_type: str,
        scope_id: int,
        *,
        subject_qq: int = 0,
        persona_dir: Path | None = None,
    ) -> "ChatSession":
        chat = store.get_chat(scope_type, scope_id) or {}
        profiles = store.list_profiles(scope_type, scope_id, limit=50)
        episodes = store.list_episodes(scope_type, scope_id, limit=3)
        open_loops = store.list_open_loops(
            scope_type, scope_id, subject_qq=subject_qq or None, limit=8
        )
        events = store.list_relationship_events(
            scope_type, scope_id, subject_qq=subject_qq or None, limit=8
        )
        memories = store.list_memories(scope_type, scope_id, qq=subject_qq or None, limit=8)
        summaries = store.latest_summaries(scope_type, scope_id, limit=1)
        summary = str(summaries[0].get("content") or "") if summaries else ""
        persona_path = None
        if persona_dir is not None:
            if scope_type == "group":
                persona_path = Path(persona_dir) / "groups" / f"{scope_id}.md"
            elif scope_type == "private":
                persona_path = Path(persona_dir) / "private" / f"{scope_id}.md"
        return cls(
            scope_type=scope_type,
            scope_id=scope_id,
            chat=chat,
            profiles=profiles,
            episodes=episodes,
            open_loops=open_loops,
            relationship_events=events,
            memories=memories,
            summary=summary,
            persona_path=persona_path,
        )

    @property
    def title(self) -> str:
        return str(self.chat.get("title") or f"{self.scope_type} {self.scope_id}")

    @property
    def enabled(self) -> bool:
        return bool(self.chat.get("enabled", 1))

    @property
    def proactive_enabled(self) -> bool:
        return bool(self.chat.get("proactive_enabled", 1))
