"""核心逻辑测试：触发决策、限流、文本处理、标记解析。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.config import Config
from ricefish.core import trigger as tr
from ricefish.core.ratelimit import RateLimiter
from ricefish.llm.tags import parse_reply
from ricefish.util.text import reply_delay_ms, split_segments, estimate_tokens


def make_config(**overrides) -> Config:
    raw = {
        "bot": {"name": "小满", "aliases": ["小满", "满满"], "mode": "active", "dry_run": True},
        "access": {"allowed_groups": [200], "allowed_friends": [300], "blocked_users": []},
        "trigger": {
            "group": {"at_or_reply": True, "keywords": ["小满小满"]},
            "private": {"always_reply": True},
            "proactive": {
                "enabled": True,
                "probability": 0.15,
                "min_interval_sec": 600,
                "daily_quota": 20,
                "relevance_threshold": 0.6,
            },
        },
        "rate_limit": {"quiet_hours": "00:30-07:30", "group_per_min": 3, "group_per_day": 5,
                       "user_per_hour": 2},
    }
    raw["bot"].update(overrides.pop("bot", {}))
    raw["access"].update(overrides.pop("access", {}))
    for section, values in overrides.items():
        if isinstance(values, dict) and isinstance(raw.get(section), dict):
            raw[section].update(values)
    return Config.from_dict(raw)


def group_input(**kwargs) -> tr.TriggerInput:
    base = dict(
        scope_type="group",
        scope_id=200,
        user_id=300,
        self_id=999,
        text="今天好热",
        at_ids=[],
        reply_id=None,
        is_self=False,
        quoted_is_me=False,
        allowed=True,
        is_quiet=False,
        proactive_ready=True,
        rng_value=0.99,
        has_content=True,
    )
    base.update(kwargs)
    return tr.TriggerInput(**base)


class TriggerTests(unittest.TestCase):
    def decide(self, cfg=None, **kwargs):
        return tr.decide(cfg or make_config(), group_input(**kwargs))

    def test_at_me(self):
        result = self.decide(at_ids=[999])
        self.assertEqual(result.action, tr.REPLY)
        self.assertEqual(result.reason, "at_me")

    def test_reply_to_me(self):
        result = self.decide(reply_id=1, quoted_is_me=True)
        self.assertEqual(result.action, tr.REPLY)
        self.assertEqual(result.reason, "reply_to_me")

    def test_reply_to_someone_else_is_not_trigger(self):
        result = self.decide(reply_id=1, quoted_is_me=False)
        self.assertEqual(result.action, tr.IGNORE)

    def test_keyword(self):
        result = self.decide(text="小满小满你快看")
        self.assertEqual(result.action, tr.REPLY)
        self.assertTrue(result.reason.startswith("keyword"))

    def test_alias(self):
        result = self.decide(text="小满在吗")
        self.assertEqual(result.action, tr.REPLY)
        self.assertTrue(result.reason.startswith("alias"))

    def test_proactive_roll_hit(self):
        result = self.decide(text="周末去哪玩", rng_value=0.05)
        self.assertEqual(result.action, tr.PROACTIVE)

    def test_proactive_roll_miss(self):
        result = self.decide(text="周末去哪玩", rng_value=0.9)
        self.assertEqual(result.action, tr.IGNORE)
        self.assertEqual(result.reason, "proactive_roll")

    def test_not_allowed(self):
        result = self.decide(allowed=False, at_ids=[999])
        self.assertEqual(result.reason, "not_allowed")

    def test_quiet_blocks_proactive_but_not_at(self):
        quiet = self.decide(text="随便说说", rng_value=0.01, is_quiet=True)
        self.assertEqual(quiet.reason, "quiet_hours")
        hard = self.decide(at_ids=[999], is_quiet=True)
        self.assertEqual(hard.action, tr.REPLY)

    def test_passive_mode_blocks_proactive(self):
        cfg = make_config(bot={"mode": "passive"})
        result = tr.decide(cfg, group_input(text="随便", rng_value=0.01))
        self.assertEqual(result.reason, "passive_mode")

    def test_readonly_blocks_everything(self):
        cfg = make_config(bot={"mode": "readonly"})
        result = tr.decide(cfg, group_input(at_ids=[999]))
        self.assertEqual(result.reason, "readonly")

    def test_self_message(self):
        result = self.decide(is_self=True, at_ids=[999])
        self.assertEqual(result.reason, "self")

    def test_blocked_user(self):
        cfg = make_config(access={"blocked_users": [300]})
        result = tr.decide(cfg, group_input(at_ids=[999]))
        self.assertEqual(result.reason, "blocked_user")

    def test_private_reply(self):
        result = self.decide(scope_type="private", scope_id=300)
        self.assertEqual(result.action, tr.REPLY)
        self.assertEqual(result.reason, "private")

    def test_private_quiet_only_if_configured(self):
        cfg = make_config(rate_limit={"quiet_private_passive": False})
        result = tr.decide(cfg, group_input(scope_type="private", scope_id=300, is_quiet=True))
        self.assertEqual(result.reason, "quiet_hours")

    def test_empty_content(self):
        result = self.decide(has_content=False)
        self.assertEqual(result.reason, "empty")

    def test_proactive_cooldown(self):
        result = self.decide(text="随便", rng_value=0.01, proactive_ready=False)
        self.assertEqual(result.reason, "proactive_cooldown")


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_700_000_000.0
        self.cfg = make_config().rate_limit
        self.limiter = RateLimiter(self.cfg, clock=lambda: self.now)
        self.limiter.set_proactive_limits(600, 2)

    def test_group_per_min(self):
        for _ in range(3):
            self.assertTrue(self.limiter.check_group_send(200))
            self.limiter.note_send("group", 200, 300, "hi")
        verdict = self.limiter.check_group_send(200)
        self.assertFalse(verdict)
        self.assertEqual(verdict.reason, "group_per_min")

    def test_window_slides(self):
        self.limiter.note_send("group", 200, 300, "hi")
        self.now += 61
        self.assertTrue(self.limiter.check_group_send(200))

    def test_group_per_day(self):
        for i in range(5):
            self.limiter.note_send("group", 200, 300, f"消息{i}")
            self.now += 61  # 避开每分钟限制，只看日配额
        verdict = self.limiter.check_group_send(200)
        self.assertFalse(verdict)
        self.assertEqual(verdict.reason, "group_per_day")
        self.now += 86400
        self.assertTrue(self.limiter.check_group_send(200))

    def test_user_hour(self):
        self.limiter.note_send("group", 200, 300, "a")
        self.limiter.note_send("group", 200, 300, "b")
        self.assertFalse(self.limiter.check_user(300))
        self.assertTrue(self.limiter.check_user(301))

    def test_duplicate(self):
        self.limiter.note_send("group", 200, 300, "在吗 在吗")
        self.assertTrue(self.limiter.is_duplicate("group:200", "在吗  在吗"))
        self.assertFalse(self.limiter.is_duplicate("group:200", "别的话"))

    def test_proactive_limits(self):
        self.assertTrue(self.limiter.check_proactive(200))
        self.limiter.note_proactive(200)
        self.assertFalse(self.limiter.check_proactive(200))
        self.now += 601
        self.assertTrue(self.limiter.check_proactive(200))
        self.limiter.note_proactive(200)
        self.now += 601
        self.assertFalse(self.limiter.check_proactive(200))  # 日配额 2

    def test_care_limits(self):
        self.assertTrue(self.limiter.check_care(300, 7, 5))
        self.limiter.note_care(300)
        verdict = self.limiter.check_care(300, 7, 5)
        self.assertFalse(verdict)
        self.assertEqual(verdict.reason, "care_cooldown")


class TextTests(unittest.TestCase):
    def test_tokens(self):
        self.assertGreater(estimate_tokens("你好世界"), 3)
        self.assertGreater(estimate_tokens("hello world"), 0)

    def test_split_short(self):
        self.assertEqual(split_segments("在吗"), ["在吗"])

    def test_split_long_respects_limit(self):
        text = "这是一句话。" * 30
        chunks = split_segments(text, max_chars=40, max_segments=3)
        self.assertLessEqual(len(chunks), 3)
        self.assertTrue(all(chunks))
        self.assertEqual("".join(chunks), text)

    def test_split_sentences(self):
        text = "今天好热啊。你吃饭了吗？我刚下班！"
        chunks = split_segments(text, max_chars=8, max_segments=5)
        self.assertGreaterEqual(len(chunks), 2)

    def test_delay_bounds(self):
        delay = reply_delay_ms("你好", per_char=80, minimum=800, maximum=6000, jitter=0.0)
        self.assertEqual(delay, 800)
        delay = reply_delay_ms("字" * 200, per_char=80, minimum=800, maximum=6000, jitter=0.0)
        self.assertEqual(delay, 6000)


class TagTests(unittest.TestCase):
    def test_action(self):
        parsed = parse_reply("好累啊[动作:趴下]")
        self.assertEqual(parsed.text, "好累啊（趴下）")
        self.assertEqual(parsed.images, [])

    def test_emoji_mapped(self):
        parsed = parse_reply("笑死[表情:开心]", {"开心": "/tmp/happy.png"})
        self.assertEqual(parsed.text, "笑死")
        self.assertEqual(parsed.images, ["/tmp/happy.png"])

    def test_emoji_missing(self):
        parsed = parse_reply("嗯[表情:不存在]", {})
        self.assertEqual(parsed.text, "嗯")
        self.assertEqual(parsed.images, [])

    def test_strip_markdown_and_quotes(self):
        parsed = parse_reply('**你好**`世界`')
        self.assertEqual(parsed.text, "你好世界")

    def test_quoted_reply(self):
        parsed = parse_reply("「在的」")
        self.assertEqual(parsed.text, "在的")

    def test_empty(self):
        self.assertTrue(parse_reply("[表情:开心]").empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
