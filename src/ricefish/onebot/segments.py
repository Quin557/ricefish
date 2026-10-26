"""OneBot 11 消息段（segment）的构造、解析与文本化。"""

from __future__ import annotations

import html
import re
from typing import Any, Iterable

Segment = dict[str, Any]

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_\-\.]+)((?:,[^\]]*)?)\]")


# --------------------------------------------------------------------------
# 构造
# --------------------------------------------------------------------------
def text(content: str) -> Segment:
    return {"type": "text", "data": {"text": content}}


def at(qq: int | str) -> Segment:
    return {"type": "at", "data": {"qq": str(qq)}}


def reply(message_id: int | str) -> Segment:
    return {"type": "reply", "data": {"id": str(message_id)}}


def image(file: str) -> Segment:
    return {"type": "image", "data": {"file": file}}


def face(face_id: int | str) -> Segment:
    return {"type": "face", "data": {"id": str(face_id)}}


def record(file: str) -> Segment:
    return {"type": "record", "data": {"file": file}}


def text_segments(content: str) -> list[Segment]:
    """把纯文本包成消息段列表；空字符串返回空列表。"""
    return [text(content)] if content else []


def with_reply(reply_to: int | None, segments: Iterable[Segment]) -> list[Segment]:
    out: list[Segment] = []
    if reply_to:
        out.append(reply(reply_to))
    out.extend(segments)
    return out


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def _parse_cq_string(raw: str) -> list[Segment]:
    """解析 CQ 码字符串（messagePostFormat=string 时）。"""
    segments: list[Segment] = []
    pos = 0
    for match in _CQ_RE.finditer(raw):
        if match.start() > pos:
            plain = raw[pos : match.start()]
            if plain:
                segments.append(text(html.unescape(plain)))
        cq_type = match.group(1)
        data: dict[str, str] = {}
        for pair in match.group(2).split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            data[key] = html.unescape(value)
        segments.append({"type": cq_type, "data": data})
        pos = match.end()
    if pos < len(raw):
        tail = raw[pos:]
        if tail:
            segments.append(text(html.unescape(tail)))
    return segments


def parse(message: Any) -> list[Segment]:
    """把 OneBot 的 `message` 字段统一成段列表。"""
    if message is None:
        return []
    if isinstance(message, str):
        return _parse_cq_string(message)
    if isinstance(message, dict):
        return [message]
    if isinstance(message, (list, tuple)):
        out: list[Segment] = []
        for item in message:
            if isinstance(item, str):
                out.extend(_parse_cq_string(item))
            elif isinstance(item, dict):
                out.append(item)
        return out
    return []


def seg_type(segment: Segment) -> str:
    return str(segment.get("type", ""))


def seg_data(segment: Segment) -> dict[str, Any]:
    data = segment.get("data")
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# 文本化
# --------------------------------------------------------------------------
_PLACEHOLDER = {
    "image": "[图片]",
    "face": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "share": "[链接]",
    "music": "[音乐]",
    "forward": "[合并转发]",
    "markdown": "[Markdown]",
    "poke": "[戳一戳]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
}


def to_plain(segments: Iterable[Segment], *, at_as: str = "@{qq}") -> str:
    """把消息段转成适合喂给模型 / 打日志的纯文本。"""
    parts: list[str] = []
    for seg in segments:
        kind = seg_type(seg)
        data = seg_data(seg)
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "at":
            qq = str(data.get("qq", ""))
            parts.append(at_as.format(qq=qq) if qq else "@某人")
        elif kind == "image":
            summary = data.get("summary")
            parts.append(f"[图片:{summary}]" if summary else _PLACEHOLDER["image"])
        elif kind == "reply":
            continue  # 引用内容由上层补充
        else:
            parts.append(_PLACEHOLDER.get(kind, f"[{kind}]"))
    return "".join(parts).strip()


def at_targets(segments: Iterable[Segment]) -> list[int]:
    out: list[int] = []
    for seg in segments:
        if seg_type(seg) == "at":
            try:
                out.append(int(seg_data(seg).get("qq", 0)))
            except (TypeError, ValueError):
                continue
    return out


def reply_target(segments: Iterable[Segment]) -> int | None:
    for seg in segments:
        if seg_type(seg) == "reply":
            try:
                return int(seg_data(seg).get("id", 0))
            except (TypeError, ValueError):
                return None
    return None


def image_files(segments: Iterable[Segment]) -> list[str]:
    out: list[str] = []
    for seg in segments:
        if seg_type(seg) == "image":
            value = seg_data(seg).get("file") or seg_data(seg).get("url")
            if value:
                out.append(str(value))
    return out


def mentions(segments: Iterable[Segment], qq: int) -> bool:
    return any(target == qq for target in at_targets(segments))


def plain_has_alias(plain: str, aliases: Iterable[str]) -> bool:
    return any(alias and alias in plain for alias in aliases)
