"""人设与主人关系块的测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_dispatcher import make_config  # noqa: E402

from ricefish.features.commands import CommandContext, CommandHandler  # noqa: E402
from ricefish.llm.prompt import PromptBuilder, PromptContext  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402

OWNER_QQ = 10001


def build_system(
    config,
    *,
    scope_type="private",
    sender_qq=OWNER_QQ,
    proactive=False,
    scope_id=None,
    mood="",
):
    builder = PromptBuilder(config, MemoryStore(":memory:"))
    ctx = PromptContext(
        scope_type=scope_type,
        scope_id=scope_id or sender_qq,
        sender_qq=sender_qq,
        sender_name="测试主人",
        current_text="在吗",
        is_proactive=proactive,
        mood=mood,
    )
    return builder.build(ctx)[0]["content"]


class OwnerRelationTests(unittest.TestCase):
    def test_owner_private_has_relation_block(self):
        config = make_config(bot={"owner_qq": OWNER_QQ, "owner_name": "阿明"})
        system = build_system(config)
        self.assertIn("你的主人 阿明", system)
        self.assertIn("傲娇", system)
        self.assertIn(str(OWNER_QQ), system)

    def test_owner_group_gets_group_note(self):
        config = make_config(bot={"owner_qq": OWNER_QQ, "owner_name": "阿明"})
        system = build_system(config, scope_type="group", scope_id=200)
        self.assertIn("你的主人 阿明", system)
        self.assertIn("别太腻", system)

    def test_stranger_has_no_relation_block(self):
        config = make_config(bot={"owner_qq": OWNER_QQ, "owner_name": "阿明"})
        system = build_system(config, sender_qq=10002)
        self.assertNotIn("你的主人", system)

    def test_proactive_has_no_relation_block(self):
        config = make_config(bot={"owner_qq": OWNER_QQ, "owner_name": "阿明"})
        system = build_system(config, proactive=True)
        self.assertNotIn("你的主人", system)

    def test_owner_name_falls_back(self):
        config = make_config(bot={"owner_qq": OWNER_QQ, "owner_name": ""})
        system = build_system(config)
        self.assertIn("你的主人 主人", system)

    def test_no_owner_configured(self):
        config = make_config(bot={"owner_qq": 0, "owner_name": "阿明"})
        system = build_system(config)
        self.assertNotIn("你的主人", system)


class PersonaFileTests(unittest.TestCase):
    def test_persona_has_tsundere_sections(self):
        text = (ROOT / "config" / "personas" / "global.md").read_text(encoding="utf-8")
        self.assertIn("## 傲娇的尺度", text)
        self.assertIn("傲娇", text)
        self.assertIn("主人", text)
        # 防跑偏的底线必须一直在
        self.assertIn("不骂人", text)
        self.assertIn("最后一句必须落到关心上", text)


class MoodPromptTests(unittest.TestCase):
    def test_depth_rules_present(self):
        system = build_system(make_config())
        self.assertIn("【聊天的深度】", system)
        self.assertIn("追问一句", system)

    def test_mood_block_injected(self):
        config = make_config()
        system = build_system(config, mood="心情不错")
        self.assertIn("【肥鱼此刻的心情】心情不错", system)
        self.assertIn("别把心情说出口", system)

    def test_no_mood_block_by_default(self):
        system = build_system(make_config())
        self.assertNotIn("【肥鱼此刻的心情】", system)


class GroupPersonaTests(unittest.TestCase):
    def test_group_persona_is_injected(self):
        from _temp import TempDir

        with TempDir() as tmp:
            groups = Path(tmp) / "groups"
            groups.mkdir(parents=True, exist_ok=True)
            (groups / "200.md").write_text("# 测试群\n这是测试群。", encoding="utf-8")
            builder = PromptBuilder(
                make_config(), MemoryStore(":memory:"), persona_dir=Path(tmp)
            )
            ctx = PromptContext(
                scope_type="group",
                scope_id=200,
                sender_qq=300,
                sender_name="阿澈",
                current_text="在吗",
            )
            system = builder.build(ctx)[0]["content"]
            self.assertIn("【在这个群里的设定】", system)
            self.assertIn("测试群", system)

    def test_missing_group_persona_is_silent(self):
        system = build_system(make_config(), scope_type="group", scope_id=999999)
        self.assertNotIn("【在这个群里的设定】", system)


class OpenLoopPromptTests(unittest.TestCase):
    def test_open_loops_are_injected(self):
        store = MemoryStore(":memory:")
        try:
            store.add_open_loop(
                "private", OWNER_QQ, "下周要去体检", subject_qq=OWNER_QQ, kind="plan"
            )
            builder = PromptBuilder(make_config(), store)
            ctx = PromptContext(
                scope_type="private",
                scope_id=OWNER_QQ,
                sender_qq=OWNER_QQ,
                sender_name="测试主人",
                current_text="在吗",
                open_loops=store.list_open_loops(
                    "private", OWNER_QQ, subject_qq=OWNER_QQ
                ),
            )
            messages = builder.build(ctx)
            joined = "\n".join(item["content"] for item in messages)
            self.assertIn("【你还记着的事】", joined)
            self.assertIn("下周要去体检", joined)
        finally:
            store.close()


class PrivatePersonaTests(unittest.TestCase):
    def test_private_persona_is_injected_only_for_that_friend(self):
        from _temp import TempDir

        tmp = TempDir()
        try:
            (Path(tmp.name) / "private").mkdir(parents=True, exist_ok=True)
            (Path(tmp.name) / "private" / "300.md").write_text(
                "只对阿澈：叫他澈哥，别太客气。", encoding="utf-8"
            )
            builder = PromptBuilder(
                make_config(), MemoryStore(":memory:"), persona_dir=Path(tmp.name)
            )
            ctx = PromptContext(
                scope_type="private",
                scope_id=300,
                sender_qq=300,
                sender_name="阿澈",
                current_text="在吗",
            )
            system = builder.build(ctx)[0]["content"]
            self.assertIn("【你和这个人的单独设定】", system)
            self.assertIn("澈哥", system)

            other = PromptContext(
                scope_type="private",
                scope_id=301,
                sender_qq=301,
                sender_name="别人",
                current_text="在吗",
            )
            system_other = builder.build(other)[0]["content"]
            self.assertNotIn("【你和这个人的单独设定】", system_other)
        finally:
            tmp.cleanup()


class CallMeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore(":memory:")
        self.config = make_config()
        self.ctx = CommandContext(
            config=self.config,
            store=self.store,
            sender=None,
            llm=None,
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

    async def test_set_nickname(self):
        reply = await CommandHandler().cmd_call_me(self.ctx, "阿澈")
        self.assertIn("阿澈", reply)
        profile = self.store.get_profile("group", 200, 300)
        self.assertEqual(profile["display_name"], "阿澈")
        self.assertEqual(profile["aliases"], "阿澈")

    async def test_nickname_accumulates_aliases(self):
        handler = CommandHandler()
        await handler.cmd_call_me(self.ctx, "阿澈")
        await handler.cmd_call_me(self.ctx, "澈哥")
        profile = self.store.get_profile("group", 200, 300)
        self.assertEqual(profile["display_name"], "澈哥")
        self.assertIn("阿澈", profile["aliases"])
        self.assertIn("澈哥", profile["aliases"])

    async def test_empty_nickname(self):
        reply = await CommandHandler().cmd_call_me(self.ctx, "   ")
        self.assertIn("用法", reply)


class WhitelistRuntimeTests(unittest.TestCase):
    def test_apply_allowed_lists(self):
        config = make_config()
        config.apply_runtime(
            {"allowed_groups": "200, 300；400", "allowed_friends": "500 600"}
        )
        self.assertEqual(config.access.allowed_groups, [200, 300, 400])
        self.assertEqual(config.access.allowed_friends, [500, 600])

    def test_empty_value_keeps_current(self):
        config = make_config()
        before_groups = list(config.access.allowed_groups)
        before_friends = list(config.access.allowed_friends)
        config.apply_runtime({"allowed_groups": "", "allowed_friends": "  "})
        self.assertEqual(config.access.allowed_groups, before_groups)
        self.assertEqual(config.access.allowed_friends, before_friends)

    def test_apply_access_allows_empty_and_dedupes(self):
        config = make_config()
        config.apply_access([300, 200, 200], [500])
        self.assertEqual(config.access.allowed_groups, [200, 300])
        self.assertEqual(config.access.allowed_friends, [500])
        config.apply_access([], [])
        self.assertEqual(config.access.allowed_groups, [])
        self.assertEqual(config.access.allowed_friends, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
