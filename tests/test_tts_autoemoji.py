"""TTS 路径与自动表情的测试。"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _temp import TempDir  # noqa: E402

from test_dispatcher import FakeClient, FakeLLM, group_event, make_config  # noqa: E402

from ricefish.core.dispatcher import Dispatcher  # noqa: E402
from ricefish.core.ratelimit import RateLimiter  # noqa: E402
from ricefish.core.sender import Sender  # noqa: E402
from ricefish.features import tts  # noqa: E402
from ricefish.features.commands import CommandHandler  # noqa: E402
from ricefish.features.emoji import EmojiBook  # noqa: E402
from ricefish.llm.extractor import Extractor  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.llm.tags import ParsedReply  # noqa: E402
from ricefish.memory.curator import Curator  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402


class TtsPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthesize_returns_absolute_path(self):
        captured = {}

        class FakeCommunicate:
            def __init__(self, text, voice, **kwargs):
                captured.update({"voice": voice, **kwargs})

            async def save(self, path):
                Path(path).write_bytes(b"ID3fake")

        fake_module = types.SimpleNamespace(Communicate=FakeCommunicate)
        with TempDir() as tmp:
            with mock.patch.dict(sys.modules, {"edge_tts": fake_module}), mock.patch.object(
                tts, "CACHE_DIR", Path(tmp) / "tts"
            ):
                result = await tts.synthesize("你好呀", pitch="+12Hz")
            self.assertIsNotNone(result)
            self.assertTrue(Path(result).is_absolute())
            self.assertTrue(Path(result).exists())
        self.assertEqual(captured.get("pitch"), "+12Hz")
        self.assertEqual(captured.get("voice"), "zh-CN-XiaoyiNeural")


class EmojiPathTests(unittest.TestCase):
    def test_add_writes_absolute_path(self):
        with TempDir() as tmp:
            root = Path(tmp)
            src = root / "x.png"
            src.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("ricefish.features.emoji.EMOJI_DIR", root / "emoji"), mock.patch(
                "ricefish.features.emoji.MAPPING_PATH", root / "emoji.yaml"
            ):
                book = EmojiBook()
                out = book.add("无语", src)
                self.assertTrue(Path(out).is_absolute())
                self.assertTrue(Path(book.get("无语")).is_absolute())
                # 同名再收一次不能重复写映射
                book.add("无语", src)
                lines = (root / "emoji.yaml").read_text(encoding="utf-8").splitlines()
                self.assertEqual(sum(1 for line in lines if line.startswith("无语:")), 1)


class AutoEmojiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = make_config(bot={"owner_qq": 10000, "owner_name": "小满"})
        self.config.send.auto_emoji_probability = 1.0
        self.config.send.auto_emoji_cooldown_sec = 300
        self.store = MemoryStore(":memory:")
        self.client = FakeClient()
        self.llm = FakeLLM("嗯嗯")
        prompts = PromptBuilder(self.config, self.store)
        async def no_sleep(_):
            return None

        self.dispatcher = Dispatcher(
            config=self.config,
            store=self.store,
            client=self.client,
            sender=Sender(self.client, self.config, sleeper=no_sleep),
            llm=self.llm,
            prompts=prompts,
            limiter=RateLimiter(self.config.rate_limit),
            curator=Curator(self.config, self.store, Extractor(self.config, self.store, self.llm, prompts)),
            emojis=EmojiBook({}),
            commands=CommandHandler(),
        )
        await self.dispatcher.on_connect()

    async def asyncTearDown(self):
        self.store.close()

    async def test_random_tag_picks_inbox_image(self):
        with TempDir() as tmp:
            image = Path(tmp) / "r.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.latest", return_value=[{"id": "r.png"}]
            ), mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.get", return_value=image
            ):
                parsed = ParsedReply(text="", tags=[("表情", "随机表情")])
                self.dispatcher._apply_random_tags(parsed)
                self.assertEqual(parsed.images, [str(image)])

    async def test_auto_emoji_respects_cooldown(self):
        with TempDir() as tmp:
            image = Path(tmp) / "a.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.latest", return_value=[{"id": "a.png"}]
            ), mock.patch(
                "ricefish.core.dispatcher.emoji_inbox.get", return_value=image
            ):
                event = group_event(message_id=9)
                await self.dispatcher._maybe_auto_emoji(event, False)
                first = len(self.client.sent)
                await self.dispatcher._maybe_auto_emoji(event, False)
                self.assertEqual(len(self.client.sent), first)

    async def test_auto_emoji_skipped_when_image_already_sent(self):
        with mock.patch(
            "ricefish.core.dispatcher.emoji_inbox.latest", return_value=[{"id": "a.png"}]
        ), mock.patch(
            "ricefish.core.dispatcher.emoji_inbox.get", return_value=Path("a.png")
        ):
            await self.dispatcher._maybe_auto_emoji(group_event(message_id=10), True)
        self.assertEqual(self.client.sent, [])


class VoiceAliasTests(unittest.IsolatedAsyncioTestCase):
    def test_alias_map(self):
        self.assertEqual(Dispatcher.VOICE_KIND_ALIASES["撒娇"], "coquetry")
        self.assertEqual(Dispatcher.VOICE_KIND_ALIASES["摸头"], "pat")
        self.assertEqual(Dispatcher.VOICE_KIND_LABELS["coquetry"], "撒娇")

    async def test_chinese_kind_uses_pack(self):
        from ricefish.features.voice import Voice, VoiceLine, VoicePack

        with TempDir() as tmp:
            audio = Path(tmp) / "a.wav"
            audio.write_bytes(b"RIFFfake")
            pack = VoicePack([VoiceLine(id=1, kind="coquetry", text="撒娇", path=str(audio))])
            config = make_config()
            store = MemoryStore(":memory:")
            client = FakeClient()
            llm = FakeLLM("嗯")
            prompts = PromptBuilder(config, store)
            dispatcher = Dispatcher(
                config=config,
                store=store,
                client=client,
                sender=Sender(client, config, sleeper=lambda _s: asyncio.sleep(0)),
                llm=llm,
                prompts=prompts,
                limiter=RateLimiter(config.rate_limit),
                curator=Curator(config, store, Extractor(config, store, llm, prompts)),
                emojis=EmojiBook({}),
                commands=CommandHandler(),
            )
            dispatcher.voice = Voice(config, pack)
            await dispatcher._send_voice_value(group_event(message_id=11), "撒娇")
            kinds = [seg.get("type") for _, _, msg in client.sent for seg in msg]
            self.assertIn("record", kinds)
            store.close()

    async def test_prompt_shows_chinese_voice_labels(self):
        from ricefish.features.voice import Voice, VoiceLine, VoicePack

        config = make_config()
        store = MemoryStore(":memory:")
        client = FakeClient()
        llm = FakeLLM("嗯")
        prompts = PromptBuilder(config, store)
        dispatcher = Dispatcher(
            config=config,
            store=store,
            client=client,
            sender=Sender(client, config, sleeper=lambda _s: asyncio.sleep(0)),
            llm=llm,
            prompts=prompts,
            limiter=RateLimiter(config.rate_limit),
            curator=Curator(config, store, Extractor(config, store, llm, prompts)),
            emojis=EmojiBook({}),
            commands=CommandHandler(),
        )
        dispatcher.voice = Voice(
            config, VoicePack([VoiceLine(id=1, kind="coquetry", text="x", path="x.wav")])
        )
        ctx = dispatcher._build_context(group_event(message_id=12), False, "", [])
        self.assertIn("撒娇", ctx.voice_kinds)
        store.close()


class TtsProsodyTests(unittest.IsolatedAsyncioTestCase):
    def test_prosody_varies_by_sentence_type(self):
        # 疑问：抬高音调、放慢（短句再慢一点）
        rate, pitch = tts._prosody("你好吗？", 6, 10, 1.0)
        self.assertEqual(rate, "-2%")
        self.assertEqual(pitch, "+19Hz")
        # 感叹：加快、抬高
        rate, pitch = tts._prosody("太棒了！", 6, 10, 1.0)
        self.assertEqual(rate, "+12%")
        self.assertEqual(pitch, "+17Hz")
        # 省略：放慢、压低
        rate, pitch = tts._prosody("算了…", 6, 10, 1.0)
        self.assertEqual(rate, "-6%")
        self.assertEqual(pitch, "+5Hz")

    def test_split_sentences(self):
        parts = tts._split_sentences("你好呀！今天过得怎么样？还行……")
        self.assertEqual(len(parts), 3)

    async def test_multi_sentence_uses_different_pitch(self):
        calls = []

        class FakeCommunicate:
            def __init__(self, text, voice, **kwargs):
                calls.append((text, kwargs))

            async def save(self, path):
                Path(path).write_bytes(b"X" * 10)

        fake_module = types.SimpleNamespace(Communicate=FakeCommunicate)
        with TempDir() as tmp:
            with mock.patch.dict(sys.modules, {"edge_tts": fake_module}), mock.patch.object(
                tts, "CACHE_DIR", Path(tmp) / "tts"
            ):
                result = await tts.synthesize(
                    "你好呀！今天过得怎么样？", rate="+6%", pitch="+10Hz", expressiveness=1.0
                )
                self.assertIsNotNone(result)
                self.assertEqual(len(calls), 2)
                self.assertNotEqual(calls[0][1]["pitch"], calls[1][1]["pitch"])
                self.assertEqual(Path(result).stat().st_size, 20)

    async def test_zero_expressiveness_uses_single_call(self):
        calls = []

        class FakeCommunicate:
            def __init__(self, text, voice, **kwargs):
                calls.append(text)

            async def save(self, path):
                Path(path).write_bytes(b"Y")

        fake_module = types.SimpleNamespace(Communicate=FakeCommunicate)
        with TempDir() as tmp:
            with mock.patch.dict(sys.modules, {"edge_tts": fake_module}), mock.patch.object(
                tts, "CACHE_DIR", Path(tmp) / "tts"
            ):
                await tts.synthesize("你好！再见？", expressiveness=0)
        self.assertEqual(calls, ["你好！再见？"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
