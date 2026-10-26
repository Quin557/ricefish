"""调度链路测试：用假的协议端和假的模型，验证"收到消息 → 发出去"。

这里不真的连 QQ，也不花 token。
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _temp import TempDir  # noqa: E402

from ricefish.config import Config
from ricefish.core.dispatcher import Dispatcher
from ricefish.core.ratelimit import RateLimiter
from ricefish.core.sender import Sender
from ricefish.features.commands import CommandHandler
from ricefish.features.emoji import EmojiBook
from ricefish.features.voice import Voice, VoicePack
from ricefish.llm.client import ChatResult, LLMError
from ricefish.llm.extractor import Extractor
from ricefish.llm.prompt import PromptBuilder
from ricefish.memory.curator import Curator
from ricefish.memory.store import MemoryStore
from ricefish.onebot import events as ev


class FakeClient:
    self_id = 999

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, list]] = []
        self.deleted: list[int] = []
        self.reactions: list[tuple[int, str, bool]] = []
        self.image_path = ""
        self._seq = 100

    async def send_group_msg(self, group_id, message, **kwargs):
        self._seq += 1
        self.sent.append(("group", group_id, message))
        return {"message_id": self._seq}

    async def send_private_msg(self, user_id, message, **kwargs):
        self._seq += 1
        self.sent.append(("private", user_id, message))
        return {"message_id": self._seq}

    async def delete_msg(self, message_id, **kwargs):
        self.deleted.append(int(message_id))
        return {}

    async def set_msg_emoji_like(self, message_id, emoji_id, set=True, **kwargs):
        self.reactions.append((int(message_id), str(emoji_id), bool(set)))
        return {}

    async def get_image(self, file, **kwargs):
        return {"file": self.image_path} if self.image_path else {}

    async def group_poke(self, group_id, user_id, **kwargs):
        return {}

    async def friend_poke(self, user_id, **kwargs):
        return {}

    async def get_group_list(self):
        return [{"group_id": 200, "group_name": "测试群"}]

    async def get_friend_list(self):
        return [{"user_id": 300, "nickname": "阿澈"}]

    async def get_group_member_info(self, group_id, user_id, **kwargs):
        return {"user_id": user_id, "card": "澈哥", "nickname": "阿澈"}


class FakeLLM:
    available = True

    def __init__(self, reply: str = "在的，咋了") -> None:
        self.reply = reply
        self.fail = False
        self.calls: list[list[dict]] = []

    async def chat(self, messages, **kwargs) -> ChatResult:
        self.calls.append(messages)
        if self.fail:
            raise LLMError("boom")
        return ChatResult(text=self.reply, model="fake", prompt_tokens=10, completion_tokens=5)

    async def complete(self, system, user, **kwargs) -> str:
        return "0.9"

    async def complete_json(self, system, user, **kwargs):
        return None


def make_config(**overrides) -> Config:
    raw = {
        "bot": {
            "name": "小满",
            "aliases": ["小满"],
            "mode": "active",
            "dry_run": False,
            "owner_qq": 10000,
            "admin_qqs": [10000],
        },
        "access": {"allowed_groups": [200], "allowed_friends": [300]},
        "trigger": {
            "group": {"at_or_reply": True, "keywords": []},
            "private": {"always_reply": True},
            "proactive": {"enabled": True, "probability": 0.0},
        },
        "rate_limit": {"group_per_min": 10, "group_per_day": 100, "user_per_hour": 10},
        "memory": {"window_messages": 20, "window_tokens": 4000},
        "send": {
            "typing_min_ms": 0,
            "typing_max_ms": 0,
            "auto_emoji_probability": 0,
        },
    }
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(raw.get(section), dict):
            raw[section].update(values)
    return Config.from_dict(raw)


def group_event(text="小满在吗", at=True, message_id=1, user_id=300,
                group_id=200, self_id=999, reply_id=None):
    segments = []
    if reply_id:
        segments.append({"type": "reply", "data": {"id": str(reply_id)}})
    if at:
        segments.append({"type": "at", "data": {"qq": str(self_id)}})
    segments.append({"type": "text", "data": {"text": " " + text}})
    raw = {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": message_id,
        "group_id": group_id,
        "user_id": user_id,
        "self_id": self_id,
        "time": 1700000000,
        "message": segments,
        "sender": {"user_id": user_id, "nickname": "阿澈", "card": "澈哥"},
    }
    event = ev.parse_message(raw, self_id)
    assert event is not None
    return event


def private_event(text="在吗", message_id=2, user_id=300, self_id=999):
    raw = {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": user_id,
        "self_id": self_id,
        "time": 1700000000,
        "message": text,
        "sender": {"user_id": user_id, "nickname": "阿澈"},
    }
    event = ev.parse_message(raw, self_id)
    assert event is not None
    return event


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = make_config()
        self.store = MemoryStore(":memory:")
        self.client = FakeClient()
        self.llm = FakeLLM()
        self.prompts = PromptBuilder(self.config, self.store)
        self.limiter = RateLimiter(self.config.rate_limit)

        async def no_sleep(_seconds):
            return None

        self.sender = Sender(self.client, self.config, sleeper=no_sleep)
        extractor = Extractor(self.config, self.store, self.llm, self.prompts)
        self.curator = Curator(self.config, self.store, extractor)
        self.dispatcher = Dispatcher(
            config=self.config,
            store=self.store,
            client=self.client,
            sender=self.sender,
            llm=self.llm,
            prompts=self.prompts,
            limiter=self.limiter,
            curator=self.curator,
            emojis=EmojiBook({}),
            commands=CommandHandler(),
        )
        await self.dispatcher.on_connect()

    async def asyncTearDown(self):
        self.store.close()

    # -- 基本链路 ----------------------------------------------------
    async def test_group_at_trigger_replies(self):
        await self.dispatcher.handle_message(group_event())
        self.assertEqual(len(self.client.sent), 1)
        scope, scope_id, segments = self.client.sent[0]
        self.assertEqual((scope, scope_id), ("group", 200))
        types = [seg["type"] for seg in segments]
        self.assertIn("reply", types)  # 引用回复
        self.assertIn("在的", seg_text(segments))

    async def test_private_always_replies(self):
        await self.dispatcher.handle_message(private_event())
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], "private")

    async def test_group_plain_message_ignored(self):
        await self.dispatcher.handle_message(group_event(text="今天好热", at=False))
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.store.count_messages("group", 200), 1)  # 但仍然记录

    async def test_not_whitelisted_ignored(self):
        await self.dispatcher.handle_message(group_event(group_id=999))
        self.assertEqual(self.client.sent, [])

    async def test_duplicate_message_ignored(self):
        await self.dispatcher.handle_message(group_event())
        await self.dispatcher.handle_message(group_event())
        self.assertEqual(len(self.client.sent), 1)
        # 一条用户消息 + 一条机器人回复
        self.assertEqual(self.store.count_messages("group", 200), 2)

    async def test_self_message_ignored(self):
        event = group_event(self_id=999, user_id=999)
        await self.dispatcher.handle_message(event)
        self.assertEqual(self.client.sent, [])

    # -- 记忆 --------------------------------------------------------
    async def test_profile_created_and_history_saved(self):
        await self.dispatcher.handle_message(group_event())
        profile = self.store.get_profile("group", 200, 300)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["msg_count"], 1)
        rows = self.store.recent_messages("group", 200, limit=10)
        self.assertEqual(len(rows), 2)  # 用户 + 机器人
        self.assertTrue(rows[-1]["is_bot"])

    async def test_prompt_contains_profile_and_memory(self):
        self.store.ensure_profile("group", 200, 300, "澈哥")
        self.store.update_profile("group", 200, 300, tags="夜猫子", manual=True)
        self.store.add_memory("group", 200, "在准备考研", subject_qq=300)
        await self.dispatcher.handle_message(group_event(text="考研好难啊"))
        messages = self.llm.calls[0]
        joined = "\n".join(str(m["content"]) for m in messages)
        self.assertIn("澈哥", joined)
        self.assertIn("夜猫子", joined)
        self.assertIn("考研", joined)
        self.assertEqual(messages[0]["role"], "system")

    # -- 命令 --------------------------------------------------------
    async def test_command_me(self):
        self.store.ensure_profile("group", 200, 300, "澈哥")
        self.store.update_profile("group", 200, 300, tags="夜猫子")
        await self.dispatcher.handle_message(group_event(text="/我"))
        self.assertEqual(len(self.client.sent), 1)
        body = seg_text(self.client.sent[0][2])
        self.assertIn("印象", body)
        self.assertIn("好感度", body)

    async def test_admin_command_denied_for_normal_user(self):
        await self.dispatcher.handle_message(group_event(text="/开关"))
        self.assertIn("只有主人", seg_text(self.client.sent[0][2]))

    # -- 异常 --------------------------------------------------------
    async def test_llm_failure_sends_fallback(self):
        self.llm.fail = True
        await self.dispatcher.handle_message(group_event())
        self.assertEqual(len(self.client.sent), 1)

    async def test_empty_reply_not_sent(self):
        self.llm.reply = "   "
        await self.dispatcher.handle_message(group_event())
        self.assertEqual(self.client.sent, [])

    # -- 通知 --------------------------------------------------------
    async def test_recall_marks_message(self):
        event = group_event()
        await self.dispatcher.handle_message(event)
        notice = ev.parse_notice(
            {
                "post_type": "notice",
                "notice_type": "group_recall",
                "group_id": 200,
                "user_id": 300,
                "operator_id": 300,
                "message_id": event.message_id,
                "self_id": 999,
            },
            999,
        )
        assert notice is not None
        await self.dispatcher.handle_notice(notice)
        self.assertEqual(self.store.get_message_by_id(event.message_id)["recalled"], 1)

    async def test_poke_replies(self):
        notice = ev.parse_notice(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "group_id": 200,
                "user_id": 300,
                "target_id": 999,
                "self_id": 999,
            },
            999,
        )
        assert notice is not None
        await self.dispatcher.handle_notice(notice)
        self.assertEqual(len(self.client.sent), 1)

    async def test_poke_uses_voice_pack(self):
        root = Path(__file__).resolve().parents[1]
        pack = VoicePack.load(str(root / "assets" / "voices"))
        self.assertTrue(pack.enabled, "仓库里的语音包应该能加载")
        self.dispatcher.voice = Voice(self.config, pack)
        self.dispatcher.config.tools.poke_voice_probability = 1.0
        notice = ev.parse_notice(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "poke",
                "group_id": 200,
                "user_id": 300,
                "target_id": 999,
                "self_id": 999,
            },
            999,
        )
        assert notice is not None
        await self.dispatcher.handle_notice(notice)
        self.assertEqual(len(self.client.sent), 1)
        types = [seg["type"] for seg in self.client.sent[0][2]]
        self.assertIn("record", types)

    # -- 限流 --------------------------------------------------------
    async def test_group_rate_limit(self):
        self.dispatcher.limiter.cfg.group_per_min = 1
        await self.dispatcher.handle_message(group_event(message_id=1))
        await self.dispatcher.handle_message(group_event(message_id=2))
        self.assertEqual(len(self.client.sent), 1)

    # -- 主动插话 ----------------------------------------------------
    async def test_proactive_when_enabled(self):
        self.config.trigger.proactive.probability = 1.0
        self.config.trigger.proactive.enabled = True
        await self.dispatcher.handle_message(
            group_event(text="周末有人去漫展吗", at=False)
        )
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.dispatcher.stats["proactive"], 1)

    # -- dry-run -----------------------------------------------------
    async def test_dry_run_does_not_send(self):
        self.config.bot.dry_run = True
        await self.dispatcher.handle_message(group_event())
        self.assertEqual(self.client.sent, [])
        self.assertGreaterEqual(self.sender.sent_count, 1)

    # -- 图片卡 ------------------------------------------------------
    async def test_image_card_is_built(self):
        tmp = TempDir()
        try:
            path = Path(tmp.name) / "a.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            self.client.image_path = str(path)
            self.llm.reply = (
                '{"kind":"meme","summary":"一只蓝白色的胖鱼在吃饭","ocr":"",'
                '"entities":[],"meme":"","emotion":"开心","reply_angles":["好耶"],'
                '"safety":"ok"}'
            )
            event = group_event()
            event.segments.append({"type": "image", "data": {"file": "img"}})
            cards = await self.dispatcher._describe_images(event)
            self.assertTrue(cards)
            self.assertIn("画面", cards[0])
            self.assertIn("胖鱼", cards[0])
        finally:
            tmp.cleanup()

    async def test_natural_language_save_sticker(self):
        tmp = TempDir()
        try:
            path = Path(tmp.name) / "s.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            self.llm.reply = (
                '{"kind":"meme","summary":"一只猫","ocr":"",'
                '"entities":["猫"],"meme":"","emotion":"开心",'
                '"reply_angles":[],"safety":"ok"}'
            )
            with mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.latest_for_scope",
                return_value=[{"id": "s.png"}],
            ), mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.get", return_value=path
            ):
                handled = await self.dispatcher._maybe_save_sticker(
                    group_event(text="保存这个表情")
                )
            self.assertTrue(handled)
            stickers = self.store.list_stickers(limit=10)
            self.assertTrue(any("开心" == item.get("emotion") for item in stickers))
            self.assertTrue(
                any("存好了" in seg.get("data", {}).get("text", "")
                    for _, _, segs in self.client.sent for seg in segs)
            )
        finally:
            tmp.cleanup()


def seg_text(segments) -> str:
    return "".join(
        seg.get("data", {}).get("text", "") for seg in segments if seg.get("type") == "text"
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
