"""插件：往 `plugins/` 扔一个 .py 就能给机器人加功能，不用改主程序。

插件约定（全部可选）：
    NAME = "插件名"
    COMMANDS = {"指令": "说明"}        # 会出现在 /help 里
    async def on_message(event, api)   # 返回 True/字符串 表示已处理

api 提供：
    api.reply(text)      直接回一句
    api.config           只读配置
    api.store            SQLite 存储，想干嘛干嘛
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import Config
from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.plugins")


@dataclass
class PluginInfo:
    name: str
    path: str
    description: str = ""
    commands: dict[str, str] = field(default_factory=dict)
    handler: Callable[["PluginApi"], Awaitable[Any]] | None = None
    error: str = ""


class PluginApi:
    """插件能碰到的东西，故意收窄，避免插件乱来。"""

    def __init__(self, event, config: Config, store: MemoryStore, sender) -> None:
        self.event = event
        self.config = config
        self.store = store
        self._sender = sender
        self.replies: list[str] = []

    async def reply(self, text: str, *, quote: bool = True) -> None:
        if not text:
            return
        self.replies.append(text)
        await self._sender.send_text(
            self.event.scope_type,
            self.event.scope_id,
            text,
            reply_to=self.event.message_id if quote else None,
            reason="plugin",
        )

    @property
    def text(self) -> str:
        return self.event.text

    @property
    def scope(self) -> tuple[str, int]:
        return self.event.scope_type, self.event.scope_id


class PluginManager:
    def __init__(self, config: Config, store: MemoryStore, sender=None) -> None:
        self.config = config
        self.store = store
        self.sender = sender
        self.plugins: list[PluginInfo] = []
        self.stats = {"calls": 0, "handled": 0, "errors": 0}

    # ------------------------------------------------------------------
    def load(self, directory: str | None = None) -> int:
        if not self.config.plugins.enabled:
            return 0
        path = Path(directory or self.config.plugins.dir)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_dir():
            return 0
        self.plugins.clear()
        for file in sorted(path.glob("*.py")):
            if file.name.startswith("_"):
                continue
            info = PluginInfo(name=file.stem, path=str(file))
            try:
                spec = importlib.util.spec_from_file_location(
                    f"ricefish_plugin_{file.stem}", file
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                info.name = str(getattr(module, "NAME", file.stem))
                info.description = str(getattr(module, "DESCRIPTION", ""))
                commands = getattr(module, "COMMANDS", {})
                if isinstance(commands, dict):
                    info.commands = {str(k): str(v) for k, v in commands.items()}
                handler = getattr(module, "on_message", None)
                if callable(handler):
                    info.handler = handler
                self.plugins.append(info)
                log.info("插件已加载：%s（%s）", info.name, file.name)
            except Exception as exc:
                info.error = str(exc)
                self.plugins.append(info)
                log.warning("插件加载失败 %s: %s", file.name, exc)
        return len([p for p in self.plugins if p.handler])

    # ------------------------------------------------------------------
    async def dispatch(self, event) -> bool:
        if not self.sender or not self.plugins:
            return False
        api = PluginApi(event, self.config, self.store, self.sender)
        for plugin in self.plugins:
            if plugin.handler is None:
                continue
            self.stats["calls"] += 1
            try:
                result = await asyncio.wait_for(plugin.handler(api), timeout=10)
            except asyncio.TimeoutError:
                self.stats["errors"] += 1
                log.warning("插件超时：%s", plugin.name)
                continue
            except Exception:
                self.stats["errors"] += 1
                log.exception("插件异常：%s", plugin.name)
                continue
            if result:
                if isinstance(result, str):
                    await api.reply(result)
                self.stats["handled"] += 1
                return True
        return False

    # ------------------------------------------------------------------
    def describe(self) -> list[dict]:
        return [
            {
                "name": plugin.name,
                "file": plugin.path,
                "description": plugin.description,
                "commands": plugin.commands,
                "error": plugin.error,
            }
            for plugin in self.plugins
        ]
