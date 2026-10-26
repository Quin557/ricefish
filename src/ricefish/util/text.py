"""文本工具：token 估算、分条、清洗、时间格式化。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    CST = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:  # pragma: no cover - Windows/精简 Python 可能没有 tzdata
    # 中国自 1991 年起不再使用夏令时，固定 UTC+8 与 Asia/Shanghai 等价。
    CST = timezone(timedelta(hours=8))

_SENTENCE_END = "。！？!?…~～\n"
_SENTENCE_END_WITH_QUOTE = ("。”", "！”", "？”", "……")

_MD_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"__(.+?)__", re.S), r"\1"),
    (re.compile(r"`{1,3}([^`]*)`{1,3}", re.S), r"\1"),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),
]


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文 1 字≈1 token，英文 4 字符≈1 token。"""
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
            cjk += 1
        elif ch.isspace():
            other += 0.1
        else:
            other += 0.3
    return int(cjk + other) + 1


def strip_markdown(text: str) -> str:
    for pattern, repl in _MD_PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


def normalize(text: str) -> str:
    """去掉多余空白，便于去重比较。"""
    return re.sub(r"\s+", " ", text).strip()


def split_segments(text: str, max_chars: int = 120, max_segments: int = 3) -> list[str]:
    """把一段回复拆成若干条消息，模拟真人一句一句发。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # 先按句末标点切句（保留标点）
    sentences: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _SENTENCE_END:
            sentences.append("".join(buf))
            buf = []
    if buf:
        sentences.append("".join(buf))

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            head, sentence = sentence[:max_chars], sentence[max_chars:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    if len(chunks) <= max_segments:
        return chunks
    # 超出上限：多余内容并入最后一条
    head = chunks[: max_segments - 1]
    tail = "".join(chunks[max_segments - 1 :])
    return head + [tail]


def reply_delay_ms(
    text: str,
    per_char: int = 80,
    minimum: int = 800,
    maximum: int = 6000,
    jitter: float = 0.25,
    rng: object | None = None,
) -> int:
    """按字数估算"打字"耗时，带随机抖动。"""
    import random

    randomizer = rng if rng is not None else random
    raw = len(text) * per_char
    clamped = max(minimum, min(maximum, raw))
    factor = 1.0 + randomizer.uniform(-jitter, jitter)  # type: ignore[attr-defined]
    return max(200, int(clamped * factor))


def now_local(tz: timezone | ZoneInfo | None = None) -> datetime:
    return datetime.now(tz or CST)


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_clock(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def humanize_age(dt: datetime, now: datetime | None = None, tz=CST) -> str:
    now = now or datetime.now(tz)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} 天前"
    return fmt_time(dt)


def parse_ts(value: str | int | float | None) -> datetime | None:
    """把时间戳（秒）转成本地时间。"""
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, CST)


def days_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 86400


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def initials(text: str, size: int = 2) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")
    return cleaned[:size] or "群友"


def time_ago_seconds(ts: float) -> float:
    return datetime.now().timestamp() - ts


def day_key(dt: datetime | None = None) -> str:
    return (dt or now_local()).strftime("%Y-%m-%d")


def yesterday(dt: datetime | None = None) -> datetime:
    return (dt or now_local()) - timedelta(days=1)
