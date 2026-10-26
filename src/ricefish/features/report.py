"""周报：每周一给主人发一份"这周群里发生了啥"。

不调模型（省钱），纯统计 + 模板。想看的话也可以 `/周报` 手动触发。
"""

from __future__ import annotations

import logging
import time

from ..config import Config
from ..core.sender import Sender
from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.report")


class WeeklyReport:
    def __init__(self, config: Config, store: MemoryStore, sender: Sender) -> None:
        self.config = config
        self.store = store
        self.sender = sender

    # ------------------------------------------------------------------
    def build(self, days: int = 7) -> tuple[str, str]:
        """返回 (标题, 正文)。正文是纯文本，适合直接发 QQ。"""
        since = int(time.time()) - days * 86400
        chats = [c for c in self.store.list_chats("group") if c.get("scope_type") == "group"]
        rows: list[str] = []
        total = 0
        for chat in chats:
            group_id = int(chat.get("scope_id") or 0)
            count = self.store.count_messages_since("group", group_id, since)
            if count <= 0:
                continue
            total += count
            title = chat.get("title") or str(group_id)
            top = self.store.top_senders("group", group_id, since, limit=3)
            who = "、".join(f"{name}({n})" for name, n in top) if top else "没人说话"
            rows.append(f"· {title}：{count} 条，最活跃 {who}")

        new_memories = self.store.count_memories_since(since)
        memes = self.store.list_memories(kind="meme", limit=5)

        lines: list[str] = [f"这 {days} 天，群里一共 {total} 条消息。"]
        if rows:
            lines.append("")
            lines.extend(rows[:8])
        lines.append("")
        lines.append(f"我新记住了 {new_memories} 件事。")
        if memes:
            lines.append("新攒的梗：")
            lines.extend(f"· {str(m['content'])[:40]}" for m in memes[:3])
        lines.append("")
        lines.append("下周也要好好吃饭。")
        return f"ricefish 周报（{days} 天）", "\n".join(lines)

    # ------------------------------------------------------------------
    async def run(self, days: int = 7) -> str:
        title, body = self.build(days)
        owner = self.config.bot.owner_qq
        if not owner:
            log.warning("没有设置 owner_qq，周报只写日志")
            log.info("%s\n%s", title, body)
            return body
        try:
            await self.sender.send_text("private", owner, body, quote=False, reason="report")
        except Exception as exc:
            log.warning("周报发送失败: %s", exc)
        return body
