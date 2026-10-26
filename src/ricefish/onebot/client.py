"""OneBot 11 连接管理：接受 NapCat 的反向 WS、分发事件、匹配 API 响应。"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from . import events as events_mod
from .actions import ActionsMixin, NotConnected, OneBotError
from .wsserver import WebSocketConnection, WebSocketServer

log = logging.getLogger("ricefish.onebot")

EventCallback = Callable[[events_mod.Event], Awaitable[None]]
SimpleCallback = Callable[[], Awaitable[None]]


class OneBotClient(ActionsMixin):
    """一个协议端连接。属性 `self_id` 在登录后可用。"""

    def __init__(
        self,
        token: str = "",
        on_event: EventCallback | None = None,
        on_connect: SimpleCallback | None = None,
        on_disconnect: SimpleCallback | None = None,
        idle_timeout: float = 120.0,
    ) -> None:
        self.token = token
        self.on_event = on_event
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.idle_timeout = idle_timeout

        self.self_id: int = 0
        self.nickname: str = ""
        self._conn: WebSocketConnection | None = None
        self._server = WebSocketServer(self._handle_conn, token=token)
        self._pending: dict[str, asyncio.Future] = {}
        self._seq = itertools.count(1)
        self._connected = asyncio.Event()
        self._closing = False
        self._last_rx = 0.0
        self._callback_tasks: set[asyncio.Task] = set()
        self._watchdog: asyncio.Task | None = None

    # -- 生命周期 ----------------------------------------------------
    async def start(self, host: str, port: int) -> None:
        await self._server.start(host, port)
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch(), name="onebot-watchdog")

    async def stop(self) -> None:
        self._closing = True
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
        if self._conn:
            await self._conn.close()
        await self._server.stop()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(NotConnected())
        self._pending.clear()

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    @property
    def port(self) -> int:
        """实际监听端口（配置里填 0 时用于测试）。"""
        return self._server.port

    async def wait_connected(self, timeout: float = 60) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # -- API 调用 ----------------------------------------------------
    async def call(
        self, action: str, params: dict[str, Any], timeout: float = 15
    ) -> Any:
        if not self.connected or self._conn is None:
            raise NotConnected()
        echo = f"c{next(self._seq)}-{uuid.uuid4().hex[:6]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future
        try:
            await self._conn.send_json({"action": action, "params": params, "echo": echo})
            response = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            raise OneBotError(-2, f"等待 {action} 响应超时", action) from exc
        finally:
            self._pending.pop(echo, None)

        if not isinstance(response, dict):
            raise OneBotError(-3, "响应格式异常", action)
        retcode = int(response.get("retcode", 0) or 0)
        if retcode != 0 or response.get("status") == "failed":
            message = str(response.get("message") or response.get("wording") or "")
            raise OneBotError(retcode, message, action)
        return response.get("data")

    # -- 连接处理 ----------------------------------------------------
    async def _handle_conn(self, conn: WebSocketConnection) -> None:
        old = self._conn
        self._conn = conn
        self._last_rx = time.monotonic()
        if old is not None and old is not conn:
            log.info("协议端重新连接，关闭旧连接 %s", old.peer)
            await old.close()
        log.info("协议端已连接：%s", conn.peer or "unknown")
        self._connected.set()
        # 登录信息查询放后台，避免协议端不响应时阻塞事件流
        await self._spawn(self._refresh_login_info())
        if self.on_connect:
            await self._spawn(self.on_connect())

        try:
            while True:
                raw = await conn.recv_text()
                if raw is None:
                    break
                self._last_rx = time.monotonic()
                await self._dispatch_raw(raw)
        finally:
            if self._conn is conn:
                self._conn = None
                self._connected.clear()
                log.warning("协议端连接断开：%s", conn.peer or "unknown")
                for future in list(self._pending.values()):
                    if not future.done():
                        future.set_exception(NotConnected())
                self._pending.clear()
                if self.on_disconnect and not self._closing:
                    await self._spawn(self.on_disconnect())

    async def _refresh_login_info(self) -> None:
        try:
            info = await self.get_login_info()
        except OneBotError:
            return
        if isinstance(info, dict):
            self.self_id = int(info.get("user_id", 0) or 0)
            self.nickname = str(info.get("nickname", "") or "")
            log.info("已登录账号：%s(%s)", self.nickname, self.self_id)

    async def _dispatch_raw(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.debug("收到非 JSON 数据：%s", raw[:200])
            return
        if not isinstance(payload, dict):
            return

        echo = payload.get("echo")
        if echo is not None:
            future = self._pending.get(str(echo))
            if future and not future.done():
                future.set_result(payload)
                return

        self_id = int(payload.get("self_id", 0) or self.self_id)
        if self_id:
            self.self_id = self_id
        event = events_mod.parse(payload, self_id)
        if event is None:
            return
        if isinstance(event, events_mod.MetaEvent):
            await self._handle_meta(event)
            return
        if self.on_event:
            await self._spawn(self.on_event(event))

    async def _handle_meta(self, event: events_mod.MetaEvent) -> None:
        if event.kind == "lifecycle" and event.data.get("sub_type") == "connect":
            log.info("协议端生命周期 connect")
        elif event.kind == "heartbeat":
            status = event.data.get("status") or {}
            if isinstance(status, dict) and status.get("online") is False:
                log.warning("协议端心跳报告 offline")

    async def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.create_task(self._safe(coro))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)

    @staticmethod
    async def _safe(coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("事件处理异常")

    async def _watch(self) -> None:
        """长时间没有任何帧（含心跳）就主动断链，等 NapCat 重连。"""
        while not self._closing:
            await asyncio.sleep(5)
            if not self.connected:
                continue
            silence = time.monotonic() - self._last_rx
            if silence > self.idle_timeout:
                log.warning("协议端 %.0fs 无数据，主动断开等待重连", silence)
                conn = self._conn
                if conn:
                    await conn.close()
