"""工具调用（function calling）：让模型自己决定去查天气、看时间、搜网页。

模型不再需要"假装上不了网"——它可以直接调用这里注册的工具，
把真实结果拿到手再组织成人话。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Config
from ..util.text import now_local
from .tools import Tools

log = logging.getLogger("ricefish.toolbox")

#: 暴露给模型的工具描述（OpenAI / DeepSeek 通用格式）
_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某个城市当前的天气和今天的气温范围",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名，例如 北京、上海、杭州；不确定就留空",
                    }
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期、时间和星期几",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "联网搜索实时信息（新闻、价格、赛事、活动等），返回前三条结果摘要",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "打开一个网页，读出正文（用于查资料、照着教程回答）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http/https 开头的完整网址"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_closer",
            "description": "对方刚发过图时，对图再看一眼/放大某个区域，回答一个具体问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "你想确认的细节，例如「图上的字写的是什么」",
                    },
                    "region": {
                        "type": "string",
                        "enum": ["full", "left", "right", "top", "bottom", "center"],
                        "description": "看整张还是放大某个区域，默认 full",
                    },
                },
                "required": ["question"],
            },
        },
    },
]

MAX_RESULT_CHARS = 600


class ToolBox:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.tools = Tools(config)
        self.vision_lookup = None
        self.current_scope = ""

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config.tools, "enable_calls", True))

    def specs(self) -> list[dict[str, Any]]:
        """按配置过滤：天气关掉就不给 get_weather。

        搜索现在有免费的网页兜底，不再依赖 search_key。
        """
        if not self.enabled:
            return []
        specs: list[dict[str, Any]] = []
        for spec in _SPECS:
            name = spec["function"]["name"]
            if name == "get_weather" and not self.config.tools.weather:
                continue
            if name == "look_closer" and not callable(self.vision_lookup):
                continue
            specs.append(spec)
        return specs

    async def run(self, name: str, arguments: str | dict[str, Any] | None) -> str:
        args = self._parse_args(arguments)
        try:
            if name == "get_weather":
                city = str(args.get("city") or "").strip() or "北京"
                return await self.tools.weather(city)
            if name == "get_current_time":
                now = now_local()
                weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                return f"{now.strftime('%Y-%m-%d %H:%M')} {weekdays[now.weekday()]}"
            if name == "web_search":
                query = str(args.get("query") or "").strip()
                if not query:
                    return "搜索关键词为空"
                return await self.tools.search(query)
            if name == "read_url":
                url = str(args.get("url") or "").strip()
                return await self.tools.read_url(url)
            if name == "look_closer":
                if not callable(self.vision_lookup):
                    return "现在没有可看的图。"
                return await self.vision_lookup(
                    str(args.get("question") or ""),
                    str(args.get("region") or "full"),
                )
        except Exception as exc:  # 工具失败不能把聊天带崩
            log.warning("工具 %s 执行失败：%s", name, exc)
            return f"工具执行失败：{exc}"
        return f"未知工具：{name}"

    @staticmethod
    def _parse_args(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not arguments:
            return {}
        try:
            data = json.loads(arguments)
            return data if isinstance(data, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def clip(text: str) -> str:
        text = (text or "").strip()
        return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "…"
