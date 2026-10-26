"""学习/设定/收表情相关测试。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from _temp import TempDir  # noqa: E402

from test_dispatcher import make_config  # noqa: E402

from ricefish.features.commands import CommandContext, CommandHandler  # noqa: E402
from ricefish.features.emoji import EmojiBook  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402


class FakeLLM:
    async def complete(self, system, user, **kwargs):
        return "- 杭州今天晴\n- 气温 21 度"


def make_ctx(store, *, user_qq=300, image_files=None, emojis=None):
    return CommandContext(
        config=make_config(),
        store=store,
        sender=None,
        llm=FakeLLM(),
        prompts=None,
        limiter=None,
        scope_type="group",
        scope_id=200,
        user_qq=user_qq,
        user_name="阿澈",
        args="",
        raw="",
        image_files=image_files or [],
        emojis=emojis,
    )


class EmojiBookTests(unittest.TestCase):
    def test_add_copies_file_and_persists_mapping(self):
        with TempDir() as tmp:
            root = Path(tmp)
            source = root / "src.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("ricefish.features.emoji.EMOJI_DIR", root / "emoji"), mock.patch(
                "ricefish.features.emoji.MAPPING_PATH", root / "emoji.yaml"
            ):
                book = EmojiBook()
                out = book.add("无语", source)
                self.assertTrue(Path(out).exists())
                self.assertEqual(book.get("无语"), out)
                self.assertIn("无语", (root / "emoji.yaml").read_text(encoding="utf-8"))

    def test_safe_name_strips_path_tricks(self):
        self.assertEqual(EmojiBook.safe_name("../../etc/passwd"), "etcpasswd")
        self.assertEqual(EmojiBook.safe_name(""), "表情")


class CollectEmojiCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_from_local_image(self):
        with TempDir() as tmp:
            root = Path(tmp)
            source = root / "pic.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("ricefish.features.emoji.EMOJI_DIR", root / "emoji"), mock.patch(
                "ricefish.features.emoji.MAPPING_PATH", root / "emoji.yaml"
            ):
                book = EmojiBook()
                store = MemoryStore(":memory:")
                ctx = make_ctx(store, image_files=[str(source)], emojis=book)
                reply = await CommandHandler().cmd_collect_emoji(ctx, "无语")
                self.assertIn("收下", reply)
                self.assertIn("无语", book.names())
                store.close()

    async def test_collect_without_image(self):
        store = MemoryStore(":memory:")
        ctx = make_ctx(store, emojis=EmojiBook())
        with mock.patch("ricefish.features.commands.emoji_inbox.latest", return_value=[]):
            reply = await CommandHandler().cmd_collect_emoji(ctx, "无语")
        self.assertIn("发图", reply)
        store.close()


class SettingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_setting_appends_to_learned_persona(self):
        with TempDir() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                store = MemoryStore(":memory:")
                ctx = make_ctx(store)
                reply = await CommandHandler().cmd_setting(ctx, "被夸的时候先脸红一下")
                self.assertIn("记下了", reply)
                text = Path("config/personas/learned.md").read_text(encoding="utf-8")
                self.assertIn("被夸的时候先脸红一下", text)
                # 人设里真的会注入
                builder = PromptBuilder(make_config(), store)
                self.assertIn("后来学到的设定", builder.build(self._ctx(builder))[0]["content"])
                store.close()
            finally:
                os.chdir(old)

    @staticmethod
    def _ctx(builder):
        from ricefish.llm.prompt import PromptContext

        return PromptContext(
            scope_type="private", scope_id=300, sender_qq=300, current_text="在吗"
        )

    async def test_clear_requires_admin(self):
        store = MemoryStore(":memory:")
        ctx = make_ctx(store, user_qq=300)
        reply = await CommandHandler().cmd_setting(ctx, "清空")
        self.assertIn("只有主人", reply)
        store.close()


class LearnCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_learn_stores_memories(self):
        store = MemoryStore(":memory:")
        ctx = make_ctx(store)
        reply = await CommandHandler().cmd_learn(ctx, "杭州今天晴，气温 21 度")
        self.assertIn("记住了", reply)
        memories = store.list_memories("group", 200, limit=10)
        self.assertGreaterEqual(len(memories), 1)
        self.assertTrue(any("杭州" in item["content"] for item in memories))
        store.close()


class PersonaLearnTests(unittest.TestCase):
    def test_extract_directives(self):
        from ricefish.features.persona_learn import extract

        self.assertEqual(extract("你要记住：被夸的时候先脸红"), "被夸的时候先脸红")
        self.assertIn("别太黏人", extract("你以后别太黏人"))
        self.assertIn("多问一句", extract("希望你多问一句"))
        self.assertIsNone(extract("你要吃饭吗"))
        self.assertIsNone(extract("今天天气不错"))

    def test_learn_writes_and_dedupes(self):
        from ricefish.features.persona_learn import PersonaLearner

        with TempDir() as tmp:
            path = Path(tmp) / "learned.md"
            learner = PersonaLearner(path)
            self.assertIsNotNone(learner.learn("记住：说话别太官方", "阿澈"))
            self.assertIsNone(learner.learn("记住：说话别太官方", "阿澈"))
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("说话别太官方"), 1)


class InboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_local_image(self):
        from ricefish.features import emoji_inbox

        class ImageClient:
            def __init__(self, path):
                self.path = path

            async def get_image(self, file_id):
                return {"file": str(self.path)}

        with TempDir() as tmp:
            source = Path(tmp) / "a.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("ricefish.features.emoji_inbox.DIR", Path(tmp) / "inbox"), mock.patch(
                "ricefish.features.emoji_inbox.INDEX", Path(tmp) / "inbox" / "index.json"
            ):
                saved = await emoji_inbox.capture(
                    ImageClient(source), "abc", sender="阿澈", qq=300, scope="group:200"
                )
                self.assertTrue(saved and Path(saved).exists())
                self.assertEqual(len(emoji_inbox.latest(5)), 1)

    async def test_collect_from_inbox(self):
        from ricefish.features import emoji_inbox

        with TempDir() as tmp:
            root = Path(tmp)
            image = root / "b.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            with mock.patch("ricefish.features.emoji.EMOJI_DIR", root / "emoji"), mock.patch(
                "ricefish.features.emoji.MAPPING_PATH", root / "emoji.yaml"
            ), mock.patch(
                "ricefish.features.emoji_inbox.DIR", root / "inbox"
            ), mock.patch(
                "ricefish.features.emoji_inbox.INDEX", root / "inbox" / "index.json"
            ), mock.patch(
                "ricefish.features.commands.emoji_inbox.latest", return_value=[{"id": "b.png"}]
            ), mock.patch(
                "ricefish.features.commands.emoji_inbox.get", return_value=image
            ):
                store = MemoryStore(":memory:")
                book = EmojiBook()
                ctx = make_ctx(store, emojis=book)
                reply = await CommandHandler().cmd_collect_emoji(ctx, "无语")
                self.assertIn("收下", reply)
                self.assertIn("无语", book.names())
                store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
