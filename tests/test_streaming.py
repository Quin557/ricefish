"""流式回复的测试：边生成边分句发送。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_dispatcher import FakeClient, group_event, make_config  # noqa: E402

from ricefish.core.dispatcher import Dispatcher  # noqa: E402
from ricefish.core.ratelimit import RateLimiter  # noqa: E402
from ricefish.core.sender import Sender  # noqa: E402
from ricefish.features.commands import CommandHandler  # noqa: E402
from ricefish.features.emoji import EmojiBook  # noqa: E402
from ricefish.llm.client import ChatResult, LLMError  # noqa: E402
from ricefish.llm.extractor import Extractor  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.memory.curator import Curator  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402


class StreamLLM:
    """假模型：按给定的分片吐字。"""

    available = True

    def __init__(self, pieces, fail_at: int | None = None, delay: float = 0.0) -> None:
        self.pieces = list(pieces)
        self.fail_at = fail_at
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def stream_chat(self, messages, **kwargs):
        self.calls.append(messages)
        for index, piece in enumerate(self.pieces):
            if self.fail_at is not None and index >= self.fail_at:
                raise LLMError("stream boom")
            if self.delay:
                await asyncio.sleep(self.delay)
            yield {"type": "delta", "text": piece}
            await asyncio.sleep(0)
        yield {"type": "done", "result": ChatResult(text="".join(self.pieces))}

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResult(text="非流式兜底", model="fake")

    async def complete(self, system, user, **kwargs):
        return "0.5"

    async def complete_json(self, system, user, **kwargs):
        return None


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def build(self, llm):
        self.config = make_config()
        self.config.llm.stream = True
        self.config.llm.stream_min_chars = 2
        self.config.llm.stream_min_visible_chars = 2
        self.config.llm.stream_ack_mode = "off"
        self.store = MemoryStore(":memory:")
        self.client = FakeClient()
        self.llm = llm
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

    async def test_splits_into_multiple_messages(self):
        await self.build(StreamLLM(["在的呀。", "今天怎么样？", "我挺好的，你呢。"]))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        sent = [msg for _, _, msg in self.client.sent]
        self.assertGreaterEqual(len(sent), 2)
        joined = "".join(seg["data"].get("text", "") for msg in sent for seg in msg)
        self.assertIn("在的呀", joined)
        self.assertIn("我挺好的", joined)

    async def test_records_full_text_once(self):
        await self.build(StreamLLM(["第一句。", "第二句。"]))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        rows = self.store.recent_messages("group", 200, limit=10)
        bot_rows = [row for row in rows if row["is_bot"]]
        self.assertEqual(len(bot_rows), 1)
        self.assertIn("第一句", bot_rows[0]["text"])

    async def test_falls_back_when_stream_fails_before_send(self):
        await self.build(StreamLLM(["还没来得及发"], fail_at=0))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        joined = "".join(
            seg["data"].get("text", "")
            for _, _, msg in self.client.sent
            for seg in msg
        )
        self.assertIn("非流式兜底", joined)

    async def test_partial_failure_keeps_sent_text(self):
        await self.build(StreamLLM(["已经说出去的一句。", "还没说完"], fail_at=1))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        joined = "".join(
            seg["data"].get("text", "")
            for _, _, msg in self.client.sent
            for seg in msg
        )
        self.assertIn("已经说出去的一句", joined)

    async def test_no_standalone_ellipsis(self):
        await self.build(StreamLLM(["……", "啊……", "行吧，那就这样。"]))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        sent = [msg for _, _, msg in self.client.sent]
        texts = [
            "".join(seg["data"].get("text", "") for seg in msg if seg["type"] == "text")
            for msg in sent
        ]
        for text in texts:
            self.assertGreater(
                Dispatcher._visible_len(text), 1, f"不许发纯标点碎片: {text!r}"
            )
        self.assertTrue(any("行吧" in text for text in texts))

    async def test_max_two_messages(self):
        await self.build(
            StreamLLM(
                [
                    "第一句完整的话。",
                    "第二句也完整。",
                    "第三句继续。",
                    "第四句收尾。",
                ]
            )
        )
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        self.assertLessEqual(len(self.client.sent), 2)

    async def test_single_short_reply_still_sent(self):
        await self.build(StreamLLM(["嗯。"]))
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        joined = "".join(
            seg["data"].get("text", "")
            for _, _, msg in self.client.sent
            for seg in msg
        )
        self.assertIn("嗯", joined)

    async def test_text_ack_is_recalled(self):
        await self.build(StreamLLM(["这个我想了一下，还是去吧。"], delay=0.7))
        self.config.llm.stream_ack_mode = "text"
        self.config.llm.stream_ack_after_sec = 0.5
        self.config.llm.stream_first_token_timeout_sec = 3
        self.config.llm.stream_ack_texts = ["等下，我想想"]
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        texts = [
            "".join(seg["data"].get("text", "") for seg in msg if seg["type"] == "text")
            for _, _, msg in self.client.sent
        ]
        self.assertIn("等下，我想想", texts)
        self.assertTrue(self.client.deleted, "慢响应提示应该被撤回")

    async def test_reaction_ack_toggles(self):
        await self.build(StreamLLM(["我在的。"], delay=0.7))
        self.config.llm.stream_ack_mode = "reaction"
        self.config.llm.stream_ack_after_sec = 0.5
        self.config.llm.stream_first_token_timeout_sec = 3
        self.config.llm.stream_ack_emoji_id = "128531"
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        self.assertTrue(any(item[2] for item in self.client.reactions))
        self.assertTrue(any(not item[2] for item in self.client.reactions))

    async def test_hundred_turns_no_standalone_fragments(self):
        await self.build(
            StreamLLM(["……", "啊……", "好吧，那今天先这样。", "明天再聊。"])
        )
        for turn in range(100):
            self.client.sent.clear()
            await self.dispatcher._respond(
                group_event(message_id=1000 + turn), proactive=False, reason="at_me"
            )
            self.assertLessEqual(len(self.client.sent), 2, f"turn {turn}")
            for _, _, msg in self.client.sent:
                text = "".join(
                    seg.get("data", {}).get("text", "")
                    for seg in msg
                    if seg.get("type") == "text"
                )
                self.assertGreater(
                    Dispatcher._visible_len(text), 1, f"turn {turn}: {text!r}"
                )

    async def test_optional_slow_note(self):
        await self.build(StreamLLM(["我在的，刚在忙。"], delay=0.7))
        self.config.llm.stream_slow_note_sec = 0.1
        self.config.llm.stream_slow_note_template = "想了 {sec} 秒…"
        await self.dispatcher._respond(group_event(), proactive=False, reason="at_me")
        texts = [
            "".join(seg["data"].get("text", "") for seg in msg if seg["type"] == "text")
            for _, _, msg in self.client.sent
        ]
        self.assertTrue(any(text.startswith("想了") for text in texts))
        self.assertTrue(any("我在的" in text for text in texts))

    def test_find_cut_prefers_first_sentence(self):
        self.assertEqual(Dispatcher._find_cut("你好。今天怎么样？", 2, 60), 3)

    def test_find_cut_waits_when_too_short(self):
        self.assertEqual(Dispatcher._find_cut("你.", 3, 60), 0)

    def test_find_cut_forced_at_max_buffer(self):
        text = "啊" * 70
        self.assertEqual(Dispatcher._find_cut(text, 2, 60), 60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
