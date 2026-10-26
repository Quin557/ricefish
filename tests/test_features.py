"""功能模块测试：小游戏、插件、周报、语义检索、工具。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _temp import TempDir  # noqa: E402

from ricefish.config import Config
from ricefish.features.commands import CommandContext
from ricefish.features.emoji import EmojiBook
from ricefish.features.games import GameManager
from ricefish.features.memes import MemeBook
from ricefish.features.plugins import PluginManager
from ricefish.features.report import WeeklyReport
from ricefish.features.tools import Tools
from ricefish.features.voice import VoicePack
from ricefish.llm.client import ChatResult
from ricefish.memory.embedding import SemanticIndex, cosine, pack, unpack
from ricefish.memory.store import MemoryStore


class FakeSender:
    def __init__(self):
        self.sent: list[str] = []
        self.records: list[str] = []
        self.images: list[str] = []

    async def send_text(self, scope_type, scope_id, text, **kwargs):
        self.sent.append(text)
        return [1]

    async def send_images(self, scope_type, scope_id, files, **kwargs):
        self.images.extend(files)
        return [1]

    async def send_record(self, scope_type, scope_id, path, **kwargs):
        self.records.append(path)
        return [1]


class FakeLLM:
    available = True

    def __init__(self, soup=None):
        self.soup = soup or {"surface": "他每天给空椅子盛饭", "truth": "他在等死去的妻子"}
        self.fail = False

    async def chat(self, messages, **kwargs) -> ChatResult:
        if self.fail:
            from ricefish.llm.client import LLMError

            raise LLMError("boom")
        return ChatResult(text="嗯", model="fake")

    async def complete(self, system, user, **kwargs) -> str:
        if "海龟汤" in system:
            return "无关"
        return "0.9"

    async def complete_json(self, system, user, **kwargs):
        return self.soup


class FakeEvent:
    def __init__(self, text, scope_type="group", scope_id=200, user_id=300, message_id=9):
        self.text = text
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.user_id = user_id
        self.message_id = message_id
        self.sender_name = "阿澈"


def make_config(**extra) -> Config:
    raw = {
        "bot": {"name": "肥鱼", "aliases": ["肥鱼"], "owner_qq": 10000, "admin_qqs": [10000]},
        "access": {"allowed_groups": [200], "allowed_friends": [300]},
        "games": {"enabled": True, "daily_limit": 3},
    }
    for section, values in extra.items():
        if isinstance(values, dict) and isinstance(raw.get(section), dict):
            raw[section].update(values)
        else:
            raw[section] = values
    return Config.from_dict(raw)


class GameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = make_config()
        self.store = MemoryStore(":memory:")
        self.sender = FakeSender()
        self.llm = FakeLLM()
        self.games = GameManager(self.config, self.store, self.llm)
        self.ctx = CommandContext(
            config=self.config,
            store=self.store,
            sender=self.sender,
            llm=self.llm,
            prompts=None,
            limiter=None,
            scope_type="group",
            scope_id=200,
            user_qq=300,
            user_name="阿澈",
            args="",
            raw="",
        )

    async def asyncTearDown(self):
        self.store.close()

    async def test_word_game_flow(self):
        self.assertTrue(await self.games.handle("/猜词", self.ctx))
        self.assertIn("猜词开始", self.sender.sent[-1])
        game = self.games.active("group:200")
        self.assertIsNotNone(game)
        word = game.state["word"]
        reply = await self.games.intercept(FakeEvent(word), self.ctx)
        self.assertIn(word, reply)
        self.assertIsNone(self.games.active("group:200"))

    async def test_word_game_wrong_and_noise(self):
        await self.games.handle("/猜词", self.ctx)
        self.assertIsNone(await self.games.intercept(FakeEvent("哈哈"), self.ctx))
        self.assertIsNone(
            await self.games.intercept(FakeEvent("今天天气挺不错的"), self.ctx)
        )
        self.assertIsNone(await self.games.intercept(FakeEvent("是这样吗？"), self.ctx))
        reply = await self.games.intercept(FakeEvent("西瓜"), self.ctx)
        self.assertIsNotNone(reply)

    async def test_word_game_strips_at_mention(self):
        """群里 @ 机器人后跟答案，要能识别成猜测而不是普通聊天。"""
        await self.games.handle("/猜词", self.ctx)
        game = self.games.active("group:200")
        reply = await self.games.intercept(
            FakeEvent("@999 西瓜"), self.ctx, "西瓜"
        )
        self.assertIsNotNone(reply)
        self.assertNotIn("@999", reply)
        self.assertIsNotNone(game)

    async def test_only_one_game_at_a_time(self):
        await self.games.handle("/猜词", self.ctx)
        await self.games.handle("/21点", self.ctx)
        self.assertIn("已经在玩", self.sender.sent[-1])

    async def test_blackjack(self):
        await self.games.handle("/21点", self.ctx)
        self.assertIn("21 点开始", self.sender.sent[-1])
        await self.games.handle("/要", self.ctx)
        self.assertTrue(any("抽到" in text or "爆了" in text or "21 点" in text for text in self.sender.sent))

    async def test_soup_and_low_effort_answer(self):
        await self.games.handle("/汤", self.ctx)
        self.assertIn("海龟汤", self.sender.sent[-1])
        answer = await self.games.intercept(FakeEvent("他是等人吗？"), self.ctx)
        self.assertEqual(answer, "无关")
        reveal = await self.games.handle("/揭晓", self.ctx)
        self.assertTrue(reveal)
        self.assertIn("真相", self.sender.sent[-1])

    async def test_daily_quota(self):
        for _ in range(3):
            self.games.stop("group:200")
            await self.games.handle("/猜词", self.ctx)
        self.games.stop("group:200")
        await self.games.handle("/猜词", self.ctx)
        self.assertIn("玩够", self.sender.sent[-1])


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TempDir()
        plugin = Path(self.tmp.name) / "demo.py"
        plugin.write_text(
            "NAME='demo'\n"
            "async def on_message(api):\n"
            "    if api.text == 'ping':\n"
            "        await api.reply('pong')\n"
            "        return True\n"
            "    return False\n",
            encoding="utf-8",
        )
        self.config = make_config(plugins={"enabled": True, "dir": self.tmp.name})
        self.store = MemoryStore(":memory:")
        self.sender = FakeSender()
        self.manager = PluginManager(self.config, self.store, self.sender)
        self.loaded = self.manager.load(self.tmp.name)

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_load_and_dispatch(self):
        self.assertEqual(self.loaded, 1)
        handled = await self.manager.dispatch(FakeEvent("ping"))
        self.assertTrue(handled)
        self.assertEqual(self.sender.sent, ["pong"])
        self.assertFalse(await self.manager.dispatch(FakeEvent("别的话")))
        self.assertEqual(self.sender.sent, ["pong"])

    async def test_describe(self):
        info = self.manager.describe()[0]
        self.assertEqual(info["name"], "demo")
        self.assertEqual(info["error"], "")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.store = MemoryStore(":memory:")
        self.store.upsert_chat("group", 200, title="测试群")
        for i in range(5):
            self.store.add_message(
                "group", 200, message_id=i + 1, sender_qq=300, sender_name="阿澈", text=f"消息{i}"
            )
        self.store.add_memory("group", 200, "喜欢猫", kind="meme")

    def tearDown(self):
        self.store.close()

    def test_build(self):
        title, body = WeeklyReport(self.config, self.store, FakeSender()).build(7)
        self.assertIn("周报", title)
        self.assertIn("测试群", body)
        self.assertIn("阿澈", body)
        self.assertIn("喜欢猫", body)


class FakeEmbedding:
    model = "fake"

    def __init__(self):
        self.available = True
        self.map = {
            "考研": [1.0, 0.0, 0.0],
            "考试": [0.95, 0.05, 0.0],
            "吃饭": [0.0, 1.0, 0.0],
        }

    async def embed(self, texts):
        out = []
        for text in texts:
            for key, vector in self.map.items():
                if key in text:
                    out.append(vector)
                    break
            else:
                out.append([0.0, 0.0, 1.0])
        return out


class SemanticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = make_config()
        self.store = MemoryStore(":memory:")
        self.provider = FakeEmbedding()
        self.index = SemanticIndex(self.config.memory, self.store, self.provider)
        self.store.add_memory("group", 200, "最近在准备考研", subject_qq=300)
        self.store.add_memory("group", 200, "每天中午吃食堂", subject_qq=300)

    async def asyncTearDown(self):
        self.store.close()

    async def test_index_and_search(self):
        await self.index.index_pending("group", 200)
        results = await self.index.search("group", 200, "考试压力", limit=3)
        self.assertTrue(results)
        self.assertIn("考研", results[0]["content"])
        self.assertGreater(results[0]["semantic_score"], 0.9)

    def test_vector_roundtrip(self):
        vector = [0.5, -0.25, 0.125]
        self.assertEqual(unpack(pack(vector)), vector)
        self.assertAlmostEqual(cosine(vector, vector), 1.0, places=5)
        self.assertAlmostEqual(cosine([1, 0], [0, 1]), 0.0, places=5)

    async def test_disabled_without_provider(self):
        index = SemanticIndex(self.config.memory, self.store, None)
        self.assertFalse(index.enabled)
        self.assertEqual(await index.search("group", 200, "考研"), [])


class AssetTests(unittest.TestCase):
    def test_repo_emoji_pack_loads(self):
        root = Path(__file__).resolve().parents[1]
        book = EmojiBook.load(str(root / "config" / "emoji.yaml"))
        # 用户可能自己往里收表情，所以只保证内置那份还在
        self.assertGreaterEqual(len(book), 18)
        self.assertIn("开心", book)
        self.assertIn("摸鱼", book)
        self.assertTrue(book.get("开心").endswith("happy.png"))

    def test_repo_voice_pack_loads(self):
        root = Path(__file__).resolve().parents[1]
        pack = VoicePack.load(str(root / "assets" / "voices"))
        self.assertEqual(len(pack.lines), 19)
        self.assertEqual(pack.kinds(), ["idle", "pat"])

    def test_repo_meme_pack_loads(self):
        root = Path(__file__).resolve().parents[1]
        book = MemeBook.load(str(root / "assets" / "memes"))
        self.assertEqual(len(book), 12)
        self.assertEqual(book.find("010").caption, "原来是高等模型")
        self.assertIsNotNone(book.pick("123"))
        self.assertIsNone(book.pick("999"))


class MemeTests(unittest.TestCase):
    def test_missing_pack_is_silent(self):
        with TempDir() as tmp:
            book = MemeBook.load(tmp)
            self.assertFalse(book.enabled)
            self.assertIsNone(book.pick())
            self.assertEqual(MemeBook.load("").items, [])


class VoicePackTests(unittest.TestCase):
    def test_load_and_pick(self):
        import json

        with TempDir() as tmp:
            base = Path(tmp)
            (base / "pat.wav").write_bytes(b"RIFF....")
            (base / "idle.mp3").write_bytes(b"ID3....")
            (base / "manifest.json").write_text(
                json.dumps(
                    {
                        "lines": [
                            {"id": 1, "kind": "pat", "text": "摸摸头", "file": "pat.wav"},
                            {"id": 2, "kind": "idle", "text": "在呢", "file": "idle.mp3"},
                            {"id": 3, "kind": "idle", "text": "丢了", "file": "missing.wav"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pack = VoicePack.load(str(base))
            self.assertTrue(pack.enabled)
            self.assertEqual(len(pack.lines), 2)
            self.assertEqual(pack.kinds(), ["idle", "pat"])
            self.assertEqual(pack.pick("pat").text, "摸摸头")
            self.assertIsNone(pack.pick("greet"))
            self.assertEqual(len(pack.find("idle")), 1)

    def test_missing_pack_is_silent(self):
        with TempDir() as tmp:
            pack = VoicePack.load(tmp)
            self.assertFalse(pack.enabled)
            self.assertIsNone(pack.pick())
            self.assertEqual(VoicePack.load("").lines, [])

    def test_load_multiple_directories(self):
        import json

        with TempDir() as tmp:
            root = Path(tmp)
            for name, words in (("a", "摸头"), ("b", "撒娇")):
                folder = root / name
                folder.mkdir()
                (folder / "x.wav").write_bytes(b"RIFF")
                (folder / "manifest.json").write_text(
                    json.dumps(
                        {
                            "lines": [
                                {"id": 0, "kind": name, "text": words, "file": "x.wav"}
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            pack = VoicePack.load(f"{root / 'a'}, {root / 'b'}, {root / 'missing'}")
            self.assertEqual(len(pack.lines), 2)
            self.assertEqual(pack.kinds(), ["a", "b"])
            self.assertEqual(pack.pick("b").text, "撒娇")


class VoiceCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_ctx(self, sender: FakeSender, config: Config) -> CommandContext:
        return CommandContext(
            config=config,
            store=MemoryStore(":memory:"),
            sender=sender,
            llm=FakeLLM(),
            prompts=None,
            limiter=None,
            scope_type="group",
            scope_id=200,
            user_qq=300,
            user_name="阿澈",
            args="",
            raw="",
        )

    async def test_voice_without_args_uses_pack(self):
        from ricefish.features.commands import CommandHandler

        root = Path(__file__).resolve().parents[1]
        config = make_config(tools={"voice_pack": str(root / "assets" / "voices")})
        sender = FakeSender()
        ctx = self.make_ctx(sender, config)
        try:
            reply = await CommandHandler().cmd_voice(ctx, "")
            self.assertIsNone(reply)
            self.assertEqual(len(sender.records), 1)
            self.assertTrue(sender.records[0].endswith(".wav"))
        finally:
            ctx.store.close()

    async def test_voice_list_and_kind(self):
        from ricefish.features.commands import CommandHandler

        root = Path(__file__).resolve().parents[1]
        config = make_config(tools={"voice_pack": str(root / "assets" / "voices")})
        sender = FakeSender()
        ctx = self.make_ctx(sender, config)
        try:
            listing = await CommandHandler().cmd_voice_list(ctx, "")
            self.assertIn("19 句", listing)
            self.assertIn("pat", listing)
            reply = await CommandHandler().cmd_voice(ctx, "摸头")
            self.assertIsNone(reply)
            self.assertEqual(len(sender.records), 1)
        finally:
            ctx.store.close()


class MemeCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_ctx(self, sender: FakeSender, config: Config) -> CommandContext:
        return CommandContext(
            config=config,
            store=MemoryStore(":memory:"),
            sender=sender,
            llm=FakeLLM(),
            prompts=None,
            limiter=None,
            scope_type="group",
            scope_id=200,
            user_qq=300,
            user_name="阿澈",
            args="",
            raw="",
        )

    async def test_meme_sends_and_cooldown(self):
        from ricefish.features.commands import CommandHandler

        root = Path(__file__).resolve().parents[1]
        config = make_config(
            tools={
                "meme_pack": str(root / "assets" / "memes"),
                "meme_cooldown_sec": 60,
            }
        )
        sender = FakeSender()
        ctx = self.make_ctx(sender, config)
        handler = CommandHandler()
        try:
            reply = await handler.cmd_meme(ctx, "010")
            self.assertIsNone(reply)
            self.assertEqual(len(sender.images), 1)
            self.assertTrue(sender.images[0].endswith("010.png"))

            blocked = await handler.cmd_meme(ctx, "")
            self.assertIn("等", blocked)
            self.assertEqual(len(sender.images), 1)
        finally:
            ctx.store.close()

    async def test_meme_list(self):
        from ricefish.features.commands import CommandHandler

        root = Path(__file__).resolve().parents[1]
        config = make_config(tools={"meme_pack": str(root / "assets" / "memes")})
        sender = FakeSender()
        ctx = self.make_ctx(sender, config)
        try:
            listing = await CommandHandler().cmd_meme_list(ctx, "")
            self.assertIn("010", listing)
            self.assertIn("原来是高等模型", listing)
            self.assertEqual(sender.images, [])
        finally:
            ctx.store.close()

    async def test_missing_meme_pack(self):
        from ricefish.features.commands import CommandHandler

        config = make_config(tools={"meme_pack": "assets/definitely-missing"})
        sender = FakeSender()
        ctx = self.make_ctx(sender, config)
        try:
            reply = await CommandHandler().cmd_meme(ctx, "")
            self.assertIn("没加载", reply)
        finally:
            ctx.store.close()


class ToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_weather_offline(self):
        config = make_config(tools={"weather": True})
        tools = Tools(config)
        original = tools._weather_sync
        tools._weather_sync = lambda city: f"{city}：晴，20℃（假数据）"
        try:
            text = await tools.weather("上海")
            self.assertIn("上海", text)
        finally:
            tools._weather_sync = original

    async def test_weather_disabled(self):
        config = make_config(tools={"weather": False})
        self.assertIn("关掉", await Tools(config).weather("上海"))

    async def test_search_without_key_uses_free_backend(self):
        tools = Tools(make_config(tools={"search_key": ""}))
        tools._free_search = lambda query: [("标题", "摘要内容")]
        text = await tools.search("天气")
        self.assertIn("标题", text)
        self.assertIn("摘要内容", text)

    async def test_search_no_results(self):
        tools = Tools(make_config(tools={"search_key": ""}))
        tools._free_search = lambda query: []
        self.assertIn("没搜到", await tools.search("不存在的东西"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
