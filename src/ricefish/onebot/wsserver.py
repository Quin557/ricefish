"""极简 RFC6455 WebSocket 服务端（零依赖）。

只实现 OneBot 反向 WS 需要的那一小撮能力：
    - 握手（可校验 Bearer token / ?access_token=）
    - 文本帧收发、分片重组、ping/pong、close
    - 服务端发出的帧不加掩码（协议要求）

不实现：压缩扩展、二进制业务、子协议协商。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import struct
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

logger = logging.getLogger("ricefish.onebot.ws")

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_FRAME = 16 * 1024 * 1024  # 16MB，防御性上限


class HandshakeError(Exception):
    """握手失败。"""


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _unmask(payload: bytes, mask: bytes) -> bytes:
    if not payload:
        return payload
    length = len(payload)
    key = (mask * (length // 4 + 1))[:length]
    return bytes(b ^ k for b, k in zip(payload, key))


class WebSocketConnection:
    """一条已建立的 WebSocket 连接。"""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        path: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.path = path
        self.headers = headers or {}
        self.closed = False
        self.peer = ""
        try:
            peer = writer.get_extra_info("peername")
            if peer:
                self.peer = f"{peer[0]}:{peer[1]}"
        except Exception:
            pass

    # -- 握手 --------------------------------------------------------
    @classmethod
    async def accept(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        token: str = "",
    ) -> "WebSocketConnection":
        try:
            raw_head = await asyncio.wait_for(cls._read_headers(reader), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            writer.close()
            raise HandshakeError("握手超时") from exc

        request_line, headers = raw_head
        parts = request_line.split()
        if len(parts) < 3 or parts[0].upper() != "GET":
            await cls._reject(writer, 400, "Bad Request")
            raise HandshakeError(f"非法请求行: {request_line!r}")

        target = parts[1]
        split = urlsplit(target)
        path = split.path
        query = parse_qs(split.query)

        upgrade = headers.get("upgrade", "").lower()
        if upgrade != "websocket":
            await cls._reject(writer, 400, "Not a WebSocket upgrade")
            raise HandshakeError("缺少 Upgrade: websocket")

        key = headers.get("sec-websocket-key", "")
        if not key:
            await cls._reject(writer, 400, "Missing Sec-WebSocket-Key")
            raise HandshakeError("缺少 Sec-WebSocket-Key")

        if token:
            provided = ""
            auth = headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
            if not provided:
                provided = (query.get("access_token") or [""])[0]
            if provided != token:
                await cls._reject(writer, 401, "Unauthorized")
                raise HandshakeError("token 校验失败")

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_accept_key(key)}\r\n"
            "\r\n"
        )
        writer.write(response.encode("latin-1"))
        await writer.drain()
        return cls(reader, writer, path=path, headers=headers)

    @staticmethod
    async def _read_headers(
        reader: asyncio.StreamReader,
    ) -> tuple[str, dict[str, str]]:
        request_line = (await reader.readline()).decode("latin-1").strip()
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            text = line.decode("latin-1").strip()
            if not text:
                break
            if ":" in text:
                name, value = text.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        return request_line, headers

    @staticmethod
    async def _reject(writer: asyncio.StreamWriter, code: int, reason: str) -> None:
        body = reason.encode("utf-8")
        head = (
            f"HTTP/1.1 {code} {reason}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        try:
            writer.write(head + body)
            await writer.drain()
        except Exception:
            pass
        writer.close()

    # -- 帧 ----------------------------------------------------------
    async def _read_frame(self) -> tuple[bool, int, bytes]:
        head = await self.reader.readexactly(2)
        b0, b1 = head[0], head[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F

        if length == 126:
            length = struct.unpack(">H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self.reader.readexactly(8))[0]
        if length > MAX_FRAME:
            raise HandshakeError(f"帧过大: {length}")

        mask = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked:
            payload = _unmask(payload, mask)
        return fin, opcode, payload

    async def recv_text(self) -> str | None:
        """读取一条完整文本消息；连接关闭返回 None。"""
        buffer: list[bytes] = []
        while True:
            try:
                fin, opcode, payload = await self._read_frame()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                self.closed = True
                return None

            if opcode == OP_CLOSE:
                await self._send_frame(OP_CLOSE, payload[:2])
                self.closed = True
                return None
            if opcode == OP_PING:
                await self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BIN, OP_CONT):
                buffer.append(payload)
                if fin:
                    data = b"".join(buffer)
                    try:
                        return data.decode("utf-8")
                    except UnicodeDecodeError:
                        return data.decode("utf-8", errors="replace")
                continue
            # 未知 opcode，忽略

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed and opcode != OP_CLOSE:
            return
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        try:
            self.writer.write(bytes(header) + payload)
            await self.writer.drain()
        except (ConnectionError, OSError):
            self.closed = True

    async def send_text(self, data: str) -> None:
        await self._send_frame(OP_TEXT, data.encode("utf-8"))

    async def send_json(self, payload: dict) -> None:
        await self.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if not self.closed:
            await self._send_frame(OP_CLOSE, struct.pack(">H", code) + reason.encode())
        self.closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


Handler = Callable[[WebSocketConnection], Awaitable[None]]


class WebSocketServer:
    """接受连接并把每条连接交给 handler。"""

    def __init__(self, handler: Handler, token: str = "") -> None:
        self.handler = handler
        self.token = token
        self._server: asyncio.AbstractServer | None = None

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(self._handle, host, port, limit=MAX_FRAME)
        sockets = self._server.sockets or []
        if sockets:
            addr = sockets[0].getsockname()
            logger.info("WebSocket 服务已监听 ws://%s:%s", addr[0], addr[1])

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            conn = await WebSocketConnection.accept(reader, writer, token=self.token)
        except HandshakeError:
            writer.close()
            return
        except Exception:
            writer.close()
            return
        try:
            await self.handler(conn)
        except Exception:
            pass
        finally:
            await conn.close()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return 0
