"""测试/调试用的极简 WebSocket 客户端（RFC6455 客户端侧，帧要加掩码）。

用途：
    - 单元测试里模拟 NapCat 连上来
    - `scripts/probe.py` 手动连一下看握手是否正常
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WsClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(
        cls,
        host: str = "127.0.0.1",
        port: int = 8080,
        path: str = "/onebot/v11/ws",
        token: str = "",
        timeout: float = 5.0,
    ) -> "WsClient":
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if token:
            lines.append(f"Authorization: Bearer {token}")
        request = "\r\n".join(lines) + "\r\n\r\n"
        writer.write(request.encode("latin-1"))
        await writer.drain()

        status_line = (await reader.readline()).decode("latin-1").strip()
        if "101" not in status_line:
            writer.close()
            raise ConnectionError(f"握手失败: {status_line}")
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        return cls(reader, writer)

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | opcode)
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.writer.write(bytes(header) + mask + masked)
        await self.writer.drain()

    async def send_json(self, payload: dict) -> None:
        await self._send_frame(0x1, json.dumps(payload, ensure_ascii=False).encode())

    async def recv_json(self, timeout: float = 5.0) -> dict:
        raw = await asyncio.wait_for(self.recv_text(), timeout)
        if raw is None:
            raise ConnectionError("连接已关闭")
        return json.loads(raw)

    async def recv_text(self, timeout: float = 5.0) -> str | None:
        data = await asyncio.wait_for(self._read_frame(), timeout)
        return data

    async def _read_frame(self) -> str | None:
        head = await self.reader.readexactly(2)
        b0, b1 = head[0], head[1]
        opcode = b0 & 0x0F
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self.reader.readexactly(8))[0]
        masked = bool(b1 & 0x80)
        mask = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked and payload:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            return None
        if opcode in (0x9, 0xA):
            return await self._read_frame()
        return payload.decode("utf-8", errors="replace")

    async def close(self) -> None:
        try:
            await self._send_frame(0x8, struct.pack(">H", 1000))
        except Exception:
            pass
        self.writer.close()


async def probe(url: str, token: str = "") -> str:
    """连一下看看握手是否通过，返回状态描述。"""
    split = urlsplit(url)
    client = await WsClient.connect(
        host=split.hostname or "127.0.0.1",
        port=split.port or 8080,
        path=split.path or "/",
        token=token,
    )
    await client.close()
    return "ok"
