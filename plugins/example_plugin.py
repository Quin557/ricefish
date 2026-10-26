"""示例插件：把文件扔在 plugins/ 下会被自动加载。

它做了两件事：
    1. 群里有人说 "ping" 就回 "pong"
    2. 说 "/roll" 就摇一个骰子

写成模板了，改一改就是你的新功能。
"""

from __future__ import annotations

import random

NAME = "示例插件"
DESCRIPTION = "ping/pong 和摇骰子，用来演示插件怎么写"
COMMANDS = {
    "/roll": "摇一个 1~100 的骰子",
}


async def on_message(api):
    text = (api.text or "").strip()

    if text.lower() == "ping":
        await api.reply("pong")
        return True

    if text.startswith("/roll"):
        value = random.randint(1, 100)
        await api.reply(f"🎲 {value}")
        return True

    return False
