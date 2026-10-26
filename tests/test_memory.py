"""记忆层测试：档案、事实检索、覆盖保护、摘要、清理。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.memory.store import MemoryStore, query_tokens


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_person_and_profile(self):
        self.store.ensure_person(300, "阿澈")
        self.store.ensure_profile("group", 200, 300, "澈哥")
        self.store.note_profile_message("group", 200, 300, "澈哥")
        profile = self.store.get_profile("group", 200, 300)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["display_name"], "澈哥")
        self.assertEqual(profile["msg_count"], 1)
        person = self.store.get_person(300)
        self.assertEqual(person["nickname"], "阿澈")

    def test_profile_patch_fills_empty_only(self):
        self.store.ensure_profile("group", 200, 300, "澈哥")
        self.store.update_profile("group", 200, 300, relation="网友")
        self.store.apply_profile_patch(
            "group", 200, 300, {"relation": "熟人", "tags": "夜猫子", "identity": "学生"}
        )
        profile = self.store.get_profile("group", 200, 300)
        self.assertEqual(profile["relation"], "熟人")  # notes/relation 允许更新
        self.assertEqual(profile["tags"], "夜猫子")
        self.assertEqual(profile["identity"], "学生")

    def test_override_blocks_extractor(self):
        self.store.ensure_profile("group", 200, 300)
        self.store.update_profile("group", 200, 300, display_name="澈哥", manual=True)
        changed = self.store.apply_profile_patch(
            "group", 200, 300, {"display_name": "小王", "tags": "话痨"}
        )
        self.assertEqual(changed, [])
        self.assertEqual(self.store.get_profile("group", 200, 300)["display_name"], "澈哥")

    def test_manual_tristate_keeps_untouched_fields(self):
        self.store.ensure_profile("group", 200, 300)
        self.store.update_profile("group", 200, 300, affinity=77, manual=True)
        # 只改备注：manual=None，不能把 is_override 或 affinity 改掉
        self.store.update_profile("group", 200, 300, notes="喜欢猫")
        profile = self.store.get_profile("group", 200, 300)
        self.assertEqual(profile["affinity"], 77)
        self.assertEqual(profile["is_override"], 1)
        # 显式解锁
        self.store.update_profile("group", 200, 300, manual=False)
        profile = self.store.get_profile("group", 200, 300)
        self.assertEqual(profile["is_override"], 0)
        self.assertEqual(profile["affinity"], 77)

    def test_open_loops_roundtrip_and_dedupe(self):
        first = self.store.add_open_loop(
            "private", 300, "下周要去体检", subject_qq=300, kind="plan"
        )
        again = self.store.add_open_loop(
            "private", 300, "下周要去体检", subject_qq=300, kind="plan"
        )
        self.assertEqual(first, again)
        loops = self.store.list_open_loops("private", 300, subject_qq=300)
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]["content"], "下周要去体检")
        self.store.resolve_open_loop(first)
        self.assertEqual(
            self.store.list_open_loops("private", 300, subject_qq=300), []
        )

    def test_stickers_dedupe_pick_and_disable(self):
        first = self.store.add_sticker(
            "hash1", "/tmp/a.png", emotion="开心", tags="猫,开心"
        )
        again = self.store.add_sticker("hash1", "/tmp/a.png")
        self.assertEqual(first, again)
        picked = self.store.pick_sticker(emotion="开心")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["path"], "/tmp/a.png")
        self.store.set_sticker_enabled(first, False)
        self.assertIsNone(self.store.pick_sticker(emotion="开心"))

    def test_sticker_vector_pick(self):
        self.store.add_sticker("h-app", "/tmp/happy.png", emotion="开心", tags="猫,好耶")
        self.store.add_sticker("h-sad", "/tmp/sad.png", emotion="难过", tags="哭")
        picked = self.store.pick_sticker(query="开心好耶")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["path"], "/tmp/happy.png")

    def test_episodes_and_relationship_events(self):
        self.store.add_episode(
            "private", 300, "聊了考研和猫", subject_qq=300, tone="放松",
            topics="考研,猫",
        )
        episodes = self.store.list_episodes("private", 300)
        self.assertEqual(len(episodes), 1)
        self.assertIn("考研", episodes[0]["summary"])
        self.store.add_relationship_event(
            "private", 300, "约好下周一起看鲸歌", subject_qq=300, kind="promise"
        )
        self.store.add_relationship_event(
            "private", 300, "约好下周一起看鲸歌", subject_qq=300, kind="promise"
        )
        events = self.store.list_relationship_events("private", 300, subject_qq=300)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "promise")

    def test_tags_merge_without_duplicates(self):
        self.store.ensure_profile("group", 200, 300)
        self.store.apply_profile_patch("group", 200, 300, {"tags": "二次元,夜猫子"})
        self.store.apply_profile_patch("group", 200, 300, {"tags": "夜猫子,爱吃辣"})
        tags = self.store.get_profile("group", 200, 300)["tags"]
        self.assertEqual(tags, "二次元,夜猫子,爱吃辣")

    def test_affinity_clamped(self):
        self.store.ensure_profile("group", 200, 300)
        self.assertEqual(self.store.bump_affinity("group", 200, 300, 60), 100)
        self.assertEqual(self.store.bump_affinity("group", 200, 300, -200), 0)

    def test_memory_dedup(self):
        first = self.store.add_memory("group", 200, "喜欢原神", subject_qq=300)
        second = self.store.add_memory("group", 200, "喜欢原神", subject_qq=300)
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_memories("group", 200)), 1)

    def test_search_prefers_subject_and_keyword(self):
        self.store.add_memory("group", 200, "最近在准备考研，压力大", subject_qq=300, importance=4)
        self.store.add_memory("group", 200, "群主讨厌别人发广告", subject_qq=0, importance=3)
        results = self.store.search_memories("group", 200, "考研压力", subject_qq=300)
        self.assertTrue(results)
        self.assertIn("考研", results[0]["content"])

    def test_search_scope_isolated(self):
        self.store.add_memory("group", 200, "喜欢猫", subject_qq=300)
        self.assertEqual(
            self.store.search_memories("group", 201, "喜欢猫", subject_qq=300), []
        )

    def test_forget_and_purge(self):
        self.store.add_memory("group", 200, "喜欢原神", subject_qq=300)
        self.store.add_memory("group", 200, "在考研", subject_qq=300)
        self.assertEqual(self.store.forget_by_keyword("group", 200, "原神"), 1)
        self.assertEqual(self.store.purge_memories("group", 200, subject_qq=300), 1)

    def test_messages_window_and_dedupe(self):
        self.store.add_message("group", 200, message_id=1, sender_qq=300, text="一")
        self.store.add_message("group", 200, message_id=2, sender_qq=300, text="二")
        self.assertTrue(self.store.message_exists(2))
        self.assertFalse(self.store.message_exists(99))
        recent = self.store.recent_messages("group", 200, limit=10)
        self.assertEqual([row["text"] for row in recent], ["一", "二"])
        self.store.mark_recalled(2)
        self.assertEqual(self.store.recent_messages("group", 200, limit=10)[-1]["recalled"], 1)

    def test_message_token_budget(self):
        for i in range(10):
            self.store.add_message("group", 200, message_id=i + 1, text="很长的消息" * 20)
        recent = self.store.recent_messages("group", 200, limit=10, max_tokens=50)
        self.assertLess(len(recent), 10)

    def test_summary(self):
        self.store.add_summary("group", 200, "聊了考试", from_ts=1, to_ts=2, msg_count=5)
        latest = self.store.latest_summaries("group", 200)
        self.assertEqual(latest[0]["content"], "聊了考试")

    def test_archive_stale(self):
        memory_id = self.store.add_memory("group", 200, "很久以前的小事", importance=1)
        self.assertEqual(self.store.archive_stale_memories(90), 1)
        self.assertEqual(self.store.list_memories("group", 200), [])
        self.assertEqual(len(self.store.list_memories("group", 200, include_archived=True)), 1)
        self.store.archive_memory(memory_id, False)
        self.assertEqual(len(self.store.list_memories("group", 200)), 1)

    def test_usage_stats(self):
        self.store.add_usage("deepseek-flash", 100, 50, purpose="chat")
        stats = self.store.usage_summary(0)
        self.assertEqual(stats["calls"], 1)
        self.assertEqual(stats["prompt_tokens"], 100)

    def test_query_tokens(self):
        tokens = query_tokens("他在准备考研，压力很大")
        self.assertIn("考研", tokens)
        self.assertIn("压力", tokens)
        self.assertLessEqual(len(query_tokens("字" * 200)), 12)

    def test_settings_roundtrip(self):
        self.store.set_setting("dry_run", "0")
        self.assertEqual(self.store.get_setting("dry_run"), "0")
        self.store.set_setting("dry_run", "1")
        self.assertEqual(self.store.get_setting("dry_run"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
