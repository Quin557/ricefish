"""OneBot 11 事件解析。只处理机器人真正需要的几类。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from . import segments as segmod


@dataclass(slots=True)
class MessageEvent:
    """收到一条消息（群聊或私聊）。"""

    message_id: int
    scope_type: str  # "group" | "private"
    scope_id: int  # group_id 或 user_id
    user_id: int
    sender_name: str
    segments: list[dict[str, Any]]
    text: str
    time: int
    sub_type: str
    self_id: int
    at_ids: list[int] = field(default_factory=list)
    reply_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def scope_key(self) -> str:
        return scope_key(self.scope_type, self.scope_id)

    @property
    def is_group(self) -> bool:
        return self.scope_type == "group"

    @property
    def is_self(self) -> bool:
        return self.user_id == self.self_id

    def mentions_me(self) -> bool:
        return self.self_id in self.at_ids


def scope_key(scope_type: str, scope_id: int | str) -> str:
    return f"{scope_type}:{scope_id}"


@dataclass(slots=True)
class NoticeEvent:
    """戳一戳、撤回、入群等通知。"""

    kind: str  # poke / group_recall / friend_recall / group_increase / group_decrease
    scope_type: str
    scope_id: int
    user_id: int
    operator_id: int
    target_id: int
    message_id: int
    self_id: int
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def scope_key(self) -> str:
        return scope_key(self.scope_type, self.scope_id)

    @property
    def is_group(self) -> bool:
        return self.scope_type == "group"


@dataclass(slots=True)
class MetaEvent:
    """生命周期 / 心跳。"""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


Event = MessageEvent | NoticeEvent | MetaEvent


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sender_name(raw: dict[str, Any], group: bool) -> str:
    sender = raw.get("sender") or {}
    if not isinstance(sender, dict):
        sender = {}
    if group:
        for key in ("card", "nickname"):
            value = str(sender.get(key) or "").strip()
            if value:
                return value
    else:
        nickname = str(sender.get("nickname") or "").strip()
        if nickname:
            return nickname
    return str(raw.get("user_id", ""))


def parse_message(raw: dict[str, Any], self_id: int) -> MessageEvent | None:
    if raw.get("post_type") != "message":
        return None
    message_type = str(raw.get("message_type", ""))
    if message_type == "group":
        scope_type, scope_id = "group", _to_int(raw.get("group_id"))
    elif message_type == "private":
        scope_type, scope_id = "private", _to_int(raw.get("user_id"))
    else:
        return None

    segs = segmod.parse(raw.get("message"))
    text = str(raw.get("raw_message") or "") or segmod.to_plain(segs)
    return MessageEvent(
        message_id=_to_int(raw.get("message_id")),
        scope_type=scope_type,
        scope_id=scope_id,
        user_id=_to_int(raw.get("user_id")),
        sender_name=_sender_name(raw, scope_type == "group"),
        segments=segs,
        text=text.strip(),
        time=_to_int(raw.get("time")) or int(time.time()),
        sub_type=str(raw.get("sub_type", "")),
        self_id=self_id,
        at_ids=segmod.at_targets(segs),
        reply_id=segmod.reply_target(segs),
        raw=raw,
    )


_NOTICE_MAP = {
    ("notify", "poke"): "poke",
    ("group_recall", ""): "group_recall",
    ("friend_recall", ""): "friend_recall",
    ("group_increase", ""): "group_increase",
    ("group_decrease", ""): "group_decrease",
    ("group_admin", ""): "group_admin",
    ("friend_add", ""): "friend_add",
}


def parse_notice(raw: dict[str, Any], self_id: int) -> NoticeEvent | None:
    if raw.get("post_type") != "notice":
        return None
    notice_type = str(raw.get("notice_type", ""))
    sub_type = str(raw.get("sub_type", ""))
    kind = _NOTICE_MAP.get((notice_type, sub_type))
    if kind is None:
        kind = notice_type or "unknown"

    group_id = _to_int(raw.get("group_id"))
    if group_id:
        scope_type, scope_id = "group", group_id
    else:
        scope_type = "private"
        scope_id = _to_int(raw.get("user_id"))

    return NoticeEvent(
        kind=kind,
        scope_type=scope_type,
        scope_id=scope_id,
        user_id=_to_int(raw.get("user_id")),
        operator_id=_to_int(raw.get("operator_id")),
        target_id=_to_int(raw.get("target_id")),
        message_id=_to_int(raw.get("message_id")),
        self_id=self_id,
        raw=raw,
    )


def parse(raw: dict[str, Any], self_id: int) -> Event | None:
    post_type = raw.get("post_type")
    if post_type == "message":
        return parse_message(raw, self_id)
    if post_type == "notice":
        return parse_notice(raw, self_id)
    if post_type == "meta_event":
        return MetaEvent(
            kind=str(raw.get("meta_event_type", "")),
            data=raw,
        )
    return None
