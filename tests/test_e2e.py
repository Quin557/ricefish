"""端到端测试：假装自己是 NapCat，走完整条 WebSocket 链路。

覆盖：握手 → 登录信息 → 群消息事件 → 触发 → 生成回复 → send_group_msg。
用 EchoLLM，不花钱、不联网。
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("RICEFISH_DB", ":memory:")

from ricefish.app import BotApp
from ricefish.config import Config
from ricefish.llm.echo import EchoLLM
from ricefish.onebot.wsclient import WsClient


class FakeNapCat:
    """一条最小可用的协议端连接：自动应答 API 请求，记录推送的动作。"""

    def __init__(self, client: WsClient) -> None:
        self.client = client
        self.actions: list[dict] = []
        self._seq = 500
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.group_message = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                payload = await self.client.recv_json(timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            action = payload.get("action")
            if not action:
                continue
            self.actions.append(payload)
            if action == "send_group_msg":
                self.group_message.set()
            await self.client.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": self._fake_data(action),
                    "echo": payload.get("echo"),
                }
            )

    def _fake_data(self, action: str):
        self._seq += 1
        if action == "get_login_info":
            return {"user_id": 999, "nickname": "小满"}
        if action == "get_group_list":
            return [{"group_id": 200, "group_name": "测试群"}]
        if action == "get_friend_list":
            return [{"user_id": 300, "nickname": "阿澈"}]
        if action == "get_group_member_info":
            return {"user_id": 300, "card": "澈哥", "nickname": "阿澈"}
        return {"message_id": self._seq}

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            await self.client.close()
        except Exception:
            pass


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        raw = {
            "bot": {
                "name": "小满",
                "aliases": ["小满"],
                "mode": "active",
                "dry_run": False,
                "owner_qq": 10000,
                "admin_qqs": [10000],
            },
            "onebot": {"host": "127.0.0.1", "port": 0, "path": "/onebot/v11/ws", "token": ""},
            "access": {"allowed_groups": [200], "allowed_friends": [300]},
            "trigger": {
                "group": {"at_or_reply": True},
                "private": {"always_reply": True},
                "proactive": {"enabled": False},
            },
            "send": {
                "typing_ms_per_char": 0,
                "typing_min_ms": 0,
                "typing_max_ms": 0,
                "segment_gap_ms": [0, 0],
            },
            "web": {"enabled": False},
        }
        self.config = Config.from_dict(raw)
        self.app = BotApp(self.config, llm=EchoLLM())
        await self.app.start()
        self.port = self.app.client.port

    async def asyncTearDown(self):
        await self.app.shutdown()

    async def _connect(self) -> FakeNapCat:
        ws = await WsClient.connect(port=self.port)
        napcat = FakeNapCat(ws)
        await napcat.start()
        for _ in range(60):
            if self.app.client.connected:
                break
            await asyncio.sleep(0.05)
        self.assertTrue(self.app.client.connected, "协议端没能连上")
        return napcat

    async def test_group_at_roundtrip(self):
        napcat = await self._connect()
        await napcat.client.send_json(
            {
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "message_id": 1001,
                "group_id": 200,
                "user_id": 300,
                "self_id": 999,
                "time": 1700000000,
                "message": [
                    {"type": "at", "data": {"qq": "999"}},
                    {"type": "text", "data": {"text": " 在吗"}},
                ],
                "sender": {"user_id": 300, "nickname": "阿澈", "card": "澈哥"},
            }
        )
        try:
            await asyncio.wait_for(napcat.group_message.wait(), timeout=8)
        except asyncio.TimeoutError:
            self.fail("没有收到 send_group_msg")

        sends = [a for a in napcat.actions if a["action"] == "send_group_msg"]
        self.assertTrue(sends)
        params = sends[0]["params"]
        self.assertEqual(params["group_id"], 200)
        kinds = [seg["type"] for seg in params["message"]]
        self.assertIn("reply", kinds)
        text = "".join(
            seg["data"].get("text", "")
            for seg in params["message"]
            if seg["type"] == "text"
        )
        self.assertIn("echo", text)

        # 消息两边都落库了（回复是异步写库的，轮询等一会儿）
        rows = []
        for _ in range(60):
            rows = self.app.store.recent_messages("group", 200, limit=10)
            if len(rows) >= 2:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[-1]["is_bot"])
        await napcat.stop()

    async def test_private_roundtrip(self):
        napcat = await self._connect()
        await napcat.client.send_json(
            {
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "message_id": 1002,
                "user_id": 300,
                "self_id": 999,
                "time": 1700000000,
                "message": "你好呀",
                "sender": {"user_id": 300, "nickname": "阿澈"},
            }
        )
        for _ in range(80):
            if any(a["action"] == "send_private_msg" for a in napcat.actions):
                break
            await asyncio.sleep(0.05)
        sends = [a for a in napcat.actions if a["action"] == "send_private_msg"]
        self.assertTrue(sends, "私聊没有回复")
        self.assertEqual(sends[0]["params"]["user_id"], 300)
        await napcat.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
