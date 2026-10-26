"""主动陪伴：开关、冷却、配额、最后一条是谁。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_dispatcher import FakeClient, make_config  # noqa: E402

from ricefish.core.ratelimit import RateLimiter  # noqa: E402
from ricefish.core.sender import Sender  # noqa: E402
from ricefish.features.commands import CommandContext, CommandHandler  # noqa: E402
from ricefish.features.companion import CompanionPinger  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402

NOW = 1_700_000_000


class FakeLLM:
    available = True

    def __init__(self, text: str = "晚饭吃了没？我刚扒了两口饭。") -> None:
        self.text = text
        self.calls: list[dict] = []

    async def complete(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return self.text


class CompanionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, *, companion_enabled=True, proactive_enabled=1, dry_run=False):
        self.config = make_config(bot={"dry_run": dry_run})
        self.config.companion.enabled = companion_enabled
        self.config.companion.idle_minutes = 5
        self.config.companion.follow_up_minutes = 5
        self.config.companion.cooldown_minutes = 5
        self.config.companion.daily_per_chat = 2
        self.config.companion.daily_total = 5
        self.config.companion.respect_quiet_hours = False
        self.store = MemoryStore(":memory:")
        self.client = FakeClient()
        self.llm = FakeLLM()
        self.prompts = PromptBuilder(self.config, self.store)
        self.limiter = RateLimiter(self.config.rate_limit, clock=lambda: NOW)

        async def no_sleep(_seconds):
            return None

        self.sender = Sender(
            self.client, self.config, sleeper=no_sleep, rng=__import__("random").Random(0)
        )
        self.store.upsert_chat(
            "private", 300, title="阿澈", enabled=1, proactive_enabled=proactive_enabled
        )
        self.pinger = CompanionPinger(
            self.config,
            self.store,
            self.llm,
            self.prompts,
            self.sender,
            self.limiter,
            clock=lambda: NOW,
        )

    async def asyncTearDown(self):
        self.store.close()

    async def test_pings_after_idle_and_then_cools_down(self):
        self.build()
        self.store.update_profile(
            "private", 300, 300, relation="朋友", affinity=75, preferences="猫",
            notes="考研中",
        )
        self.store.add_memory("private", 300, "养了一只橘猫", subject_qq=300)
        self.store.add_open_loop(
            "private", 300, "下周要去体检", subject_qq=300, kind="plan"
        )
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="我去吃饭", ts=NOW - 3600
        )
        sent = await self.pinger.run_once()
        self.assertEqual(sent, 1)
        self.assertTrue(self.client.sent)
        self.assertIn("晚饭", self.client.sent[0][2][0]["data"].get("text", ""))
        # 上一条是自己主动发的：等对方回，不再连发
        self.assertEqual(await self.pinger.run_once(), 0)
        # 上下文里带上了关系、记忆和还没做完的事
        system = self.llm.calls[0]["system"]
        user = self.llm.calls[0]["user"]
        self.assertIn("情绪", system)
        self.assertIn("亲近", user)
        self.assertIn("下周要去体检", user)
        self.assertIn("橘猫", user)

    async def test_per_chat_switch_off(self):
        self.build(proactive_enabled=0)
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="在吗", ts=NOW - 3600
        )
        self.assertEqual(await self.pinger.run_once(), 0)
        self.assertEqual(self.client.sent, [])

    async def test_global_switch_off(self):
        self.build(companion_enabled=False)
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="在吗", ts=NOW - 3600
        )
        self.assertEqual(await self.pinger.run_once(), 0)

    async def test_dry_run_does_not_send(self):
        self.build(dry_run=True)
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="在吗", ts=NOW - 3600
        )
        self.assertEqual(await self.pinger.run_once(), 0)
        self.assertEqual(self.client.sent, [])

    async def test_follow_up_after_bot_message(self):
        self.build()
        # 机器人最后说了一句，对方没回；允许在 follow_up 后补一句
        self.store.add_message(
            "private", 300, sender_qq=0, sender_name="肥鱼", text="行，去吧", ts=NOW - 3600,
            is_bot=True,
        )
        self.assertEqual(await self.pinger.run_once(), 1)
        # 补过之后，等对方回
        self.assertEqual(await self.pinger.run_once(), 0)

    async def test_daily_per_chat_quota(self):
        self.build()
        self.config.companion.daily_per_chat = 1
        self.config.companion.cooldown_minutes = 0
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="在吗", ts=NOW - 3600
        )
        self.assertEqual(await self.pinger.run_once(), 1)
        # 模拟对方回了，但每日配额已经用完
        self.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="刚看到", ts=NOW - 600
        )
        self.assertEqual(await self.pinger.run_once(), 0)


class ProactiveCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = make_config()
        self.config.bot.admin_qqs = [300]
        self.store = MemoryStore(":memory:")
        self.store.upsert_chat("private", 300, title="阿澈")
        self.handler = CommandHandler()
        self.ctx = CommandContext(
            config=self.config,
            store=self.store,
            sender=None,  # 只测命令结果，不发消息
            llm=None,
            prompts=None,
            limiter=None,
            scope_type="private",
            scope_id=300,
            user_qq=300,
            user_name="阿澈",
            args="",
            raw="",
        )

    def tearDown(self):
        self.store.close()

    async def test_toggle_current_chat(self):
        reply = await self.handler.cmd_proactive(self.ctx, "关")
        self.assertIn("关", reply)
        chat = self.store.get_chat("private", 300)
        self.assertEqual(chat["proactive_enabled"], 0)
        reply = await self.handler.cmd_proactive(self.ctx, "开")
        self.assertIn("开", reply)
        chat = self.store.get_chat("private", 300)
        self.assertEqual(chat["proactive_enabled"], 1)

    async def test_global_switch_persists(self):
        await self.handler.cmd_proactive(self.ctx, "全局 关")
        self.assertFalse(self.config.companion.enabled)
        self.assertEqual(self.store.get_setting("companion_enabled"), "0")
        await self.handler.cmd_proactive(self.ctx, "全局 开")
        self.assertTrue(self.config.companion.enabled)
        self.assertEqual(self.store.get_setting("companion_enabled"), "1")

    async def test_non_admin_rejected(self):
        self.ctx.user_qq = 301
        self.assertIn("只有主人", await self.handler.cmd_proactive(self.ctx, "关"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
