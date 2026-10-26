"""WebSocket 协议层与事件解析的测试（纯标准库 unittest）。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.onebot import events as ev
from ricefish.onebot import segments as seg
from ricefish.onebot.client import OneBotClient
from ricefish.onebot.wsclient import WsClient


class SegmentTests(unittest.TestCase):
    def test_parse_array(self):
        raw = [
            {"type": "reply", "data": {"id": "99"}},
            {"type": "at", "data": {"qq": "1"}},
            {"type": "text", "data": {"text": " 你好"}},
        ]
        segs = seg.parse(raw)
        self.assertEqual(seg.reply_target(segs), 99)
        self.assertEqual(seg.at_targets(segs), [1])
        self.assertEqual(seg.to_plain(segs), "@1 你好")

    def test_parse_cq_string(self):
        segs = seg.parse("你好[CQ:at,qq=123] 在吗")
        self.assertEqual(seg.to_plain(segs), "你好@123 在吗")
        self.assertEqual(seg.at_targets(segs), [123])

    def test_escape_entities(self):
        segs = seg.parse("[CQ:at,qq=1]&amp;[CQ:face,id=2]")
        self.assertEqual(seg.to_plain(segs), "@1&[表情]")

    def test_mentions_alias(self):
        self.assertTrue(seg.plain_has_alias("小满你在干啥", ["小满"]))
        self.assertFalse(seg.plain_has_alias("今天天气不错", ["小满"]))


class EventTests(unittest.TestCase):
    def test_group_message(self):
        raw = {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 10,
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
        event = ev.parse_message(raw, 999)
        assert event is not None
        self.assertEqual(event.scope_key, "group:200")
        self.assertEqual(event.sender_name, "澈哥")
        self.assertTrue(event.mentions_me())
        self.assertFalse(event.is_self)

    def test_private_message_and_self(self):
        raw = {
            "post_type": "message",
            "message_type": "private",
            "message_id": 11,
            "user_id": 300,
            "self_id": 300,
            "time": 1700000000,
            "message": "hi",
            "sender": {"nickname": "阿澈"},
        }
        event = ev.parse_message(raw, 300)
        assert event is not None
        self.assertEqual(event.scope_key, "private:300")
        self.assertTrue(event.is_self)

    def test_notice_poke(self):
        raw = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "self_id": 999,
            "group_id": 200,
            "user_id": 300,
            "target_id": 999,
        }
        notice = ev.parse_notice(raw, 999)
        assert notice is not None
        self.assertEqual(notice.kind, "poke")
        self.assertEqual(notice.scope_key, "group:200")

    def test_notice_recall(self):
        raw = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": 200,
            "user_id": 300,
            "operator_id": 300,
            "message_id": 55,
            "self_id": 999,
        }
        notice = ev.parse_notice(raw, 999)
        assert notice is not None
        self.assertEqual(notice.kind, "group_recall")
        self.assertEqual(notice.message_id, 55)


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received: list = []

        async def on_event(event):
            self.received.append(event)

        self.client = OneBotClient(token="secret", on_event=on_event)
        await self.client.start("127.0.0.1", 0)
        self.port = self.client.port

    async def asyncTearDown(self):
        await self.client.stop()

    async def test_handshake_and_event(self):
        ws = await WsClient.connect(port=self.port, token="secret")
        await ws.send_json(
            {
                "post_type": "message",
                "message_type": "private",
                "message_id": 1,
                "user_id": 300,
                "self_id": 999,
                "time": 1,
                "message": "在吗",
                "sender": {"nickname": "阿澈"},
            }
        )
        for _ in range(20):
            if self.received:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].text, "在吗")
        await ws.close()

    async def test_bad_token_rejected(self):
        with self.assertRaises(Exception):
            await WsClient.connect(port=self.port, token="wrong")

    async def test_action_roundtrip(self):
        ws = await WsClient.connect(port=self.port, token="secret")
        stop = asyncio.Event()

        async def responder():
            while not stop.is_set():
                try:
                    request = await ws.recv_json(timeout=1)
                except asyncio.TimeoutError:
                    continue
                if request is None:
                    return
                await ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"user_id": 999, "nickname": "小满"},
                        "echo": request["echo"],
                    }
                )

        task = asyncio.create_task(responder())
        for _ in range(40):
            if self.client.connected:
                break
            await asyncio.sleep(0.05)
        info = await self.client.get_login_info()
        self.assertEqual(info["nickname"], "小满")
        stop.set()
        await task
        await ws.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
