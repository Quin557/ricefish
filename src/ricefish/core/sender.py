"""发送器：把人话拆条、加打字延迟、限流、dry-run。

所有"像真人"的发送节奏都在这里，别在业务代码里 sleep。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Protocol

from ..config import Config
from ..onebot import segments as seg
from ..onebot.actions import OneBotError
from ..util.text import reply_delay_ms, split_segments

log = logging.getLogger("ricefish.sender")


class _SendClient(Protocol):
    async def send_group_msg(self, group_id: int, message: list, **kwargs) -> object: ...
    async def send_private_msg(self, user_id: int, message: list, **kwargs) -> object: ...


class Sender:
    def __init__(
        self,
        client: _SendClient,
        config: Config,
        rng: random.Random | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.rng = rng or random.Random()
        self._sleep = sleeper or asyncio.sleep
        self.sent_count = 0
        self.failed_count = 0

    # -- 对外 --------------------------------------------------------
    async def send_text(
        self,
        scope_type: str,
        scope_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        quote: bool | None = None,
        reason: str = "",
    ) -> list[int]:
        chunks = split_segments(
            text,
            max_chars=self.config.send.segment_max_chars,
            max_segments=self.config.send.max_segments,
        )
        if not chunks:
            return []
        use_quote = self.config.send.quote_reply if quote is None else quote
        message_ids: list[int] = []
        for index, chunk in enumerate(chunks):
            segments: list[dict] = seg.text_segments(chunk)
            if index == 0 and reply_to and use_quote:
                segments = seg.with_reply(reply_to, segments)
            delay = reply_delay_ms(
                chunk,
                per_char=self.config.send.typing_ms_per_char,
                minimum=self.config.send.typing_min_ms,
                maximum=self.config.send.typing_max_ms,
                jitter=self.config.send.jitter,
                rng=self.rng,
            )
            await self._sleep(delay / 1000.0)
            message_id = await self._send_segments(scope_type, scope_id, segments, reason)
            if message_id:
                message_ids.append(message_id)
            if index < len(chunks) - 1:
                low, high = self.config.send.segment_gap_ms
                await self._sleep(self.rng.randint(low, high) / 1000.0)
        return message_ids

    async def send_chunk(
        self,
        scope_type: str,
        scope_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        reason: str = "stream",
    ) -> int | None:
        """流式专用：立刻发，不加打字延迟、不再拆条。"""
        text = (text or "").strip()
        if not text:
            return None
        segments: list[dict] = seg.text_segments(text)
        if reply_to:
            segments = seg.with_reply(reply_to, segments)
        return await self._send_segments(scope_type, scope_id, segments, reason)

    async def send_ack(
        self,
        scope_type: str,
        scope_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        reason: str = "ack",
    ) -> int | None:
        """慢响应时先发一句「在想」，随后可撤回；不加打字延迟。"""
        text = (text or "").strip()
        if not text:
            return None
        segments: list[dict] = seg.text_segments(text)
        if reply_to:
            segments = seg.with_reply(reply_to, segments)
        return await self._send_segments(scope_type, scope_id, segments, reason)

    async def recall(self, message_id: int | None) -> bool:
        """撤回自己发的一条消息；失败只记日志。"""
        if not message_id:
            return False
        if self.config.bot.dry_run:
            log.info("[DRY] recall %s", message_id)
            return True
        delete = getattr(self.client, "delete_msg", None)
        if not callable(delete):
            return False
        try:
            await delete(int(message_id))
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("撤回失败 %s: %s", message_id, exc)
            return False

    async def react(self, message_id: int, emoji_id: str, *, set_like: bool = True) -> bool:
        """给消息加/撤表情回应；NapCat 不支持时静默失败。"""
        if not message_id:
            return False
        if self.config.bot.dry_run:
            log.info("[DRY] react %s %s=%s", message_id, emoji_id, set_like)
            return True
        call = getattr(self.client, "set_msg_emoji_like", None)
        if not callable(call):
            return False
        try:
            await call(int(message_id), str(emoji_id), set=set_like)
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("表情回应失败 %s: %s", message_id, exc)
            return False

    async def send_images(
        self, scope_type: str, scope_id: int, files: list[str], *, reason: str = ""
    ) -> list[int]:
        out: list[int] = []
        for path in files[:2]:
            segments = [seg.image(path)]
            message_id = await self._send_segments(scope_type, scope_id, segments, reason)
            if message_id:
                out.append(message_id)
            await self._sleep(self.rng.uniform(0.3, 0.9))
        return out

    async def send_record(
        self, scope_type: str, scope_id: int, path: str, *, reason: str = ""
    ) -> list[int]:
        """发一条语音。file:// 前缀是 OneBot 本地文件的约定。"""
        normalized = str(path).replace("\\", "/")
        if not normalized.startswith("file://"):
            normalized = "file:///" + normalized.lstrip("/")
        segments = [{"type": "record", "data": {"file": normalized}}]
        message_id = await self._send_segments(scope_type, scope_id, segments, reason)
        return [message_id] if message_id else []

    async def poke(self, scope_type: str, scope_id: int, user_id: int) -> None:
        if self.config.bot.dry_run:
            log.info("[DRY] poke -> %s:%s %s", scope_type, scope_id, user_id)
            return
        try:
            if scope_type == "group":
                await self.client.group_poke(scope_id, user_id)  # type: ignore[attr-defined]
            else:
                await self.client.friend_poke(user_id)  # type: ignore[attr-defined]
        except (OneBotError, AttributeError) as exc:
            log.debug("戳一戳失败: %s", exc)

    # -- 内部 --------------------------------------------------------
    async def _send_segments(
        self,
        scope_type: str,
        scope_id: int,
        segments: list[dict],
        reason: str,
    ) -> int | None:
        preview = seg.to_plain(segments)[:120]
        if self.config.bot.dry_run:
            log.info("[DRY] %s:%s -> %s", scope_type, scope_id, preview)
            self.sent_count += 1
            return None
        try:
            if scope_type == "group":
                data = await self.client.send_group_msg(scope_id, segments)
            else:
                data = await self.client.send_private_msg(scope_id, segments)
        except OneBotError as exc:
            self.failed_count += 1
            log.warning("发送失败 %s:%s [%s] %s", scope_type, scope_id, reason, exc)
            raise
        self.sent_count += 1
        log.info("已发送 %s:%s -> %s", scope_type, scope_id, preview)
        if isinstance(data, dict):
            try:
                return int(data.get("message_id") or 0) or None
            except (TypeError, ValueError):
                return None
        return None
