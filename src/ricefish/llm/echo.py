"""回声模型：没配 API Key 也能把整条链路跑通（--echo 模式）。

它不调用任何网络接口，只把用户最后一句回显回去，用于验证
NapCat → OneBot → 触发 → 发送 这条链路是否打通。
"""

from __future__ import annotations

import re
from typing import Any

from .client import ChatResult


class EchoLLM:
    available = True

    def __init__(self, prefix: str = "【echo】") -> None:
        self.prefix = prefix

    def _last_user_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                return ""
            # 用户消息第一行是「昵称: 内容」，后面几行是给模型的行为指令
            first = lines[0]
            if re.search(r"[:：]", first):
                first = re.split(r"[:：]", first, maxsplit=1)[1]
            return first.strip()
        return ""

    @staticmethod
    def _shorten(text: str, limit: int = 60) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[:limit] + "…"

    async def chat(self, messages, **kwargs) -> ChatResult:
        text = self._shorten(self._last_user_text(messages))
        return ChatResult(
            text=f"{self.prefix}收到：{text}",
            model="echo",
            prompt_tokens=0,
            completion_tokens=0,
        )

    async def complete(self, system, user, **kwargs) -> str:
        return "0.0"

    async def complete_json(self, system, user, **kwargs):
        return None

    async def describe_image(self, image_url, prompt="", **kwargs) -> str:
        return "（echo 模式看不了图）"
