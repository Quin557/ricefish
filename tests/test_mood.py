"""心情持久化的测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.features.mood import HIGH, LOW, MoodTracker  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402


class MoodTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")
        self.now = 1_700_000_000.0
        self.mood = MoodTracker(self.store, clock=lambda: self.now)

    def test_bump_and_persist_across_instances(self):
        self.mood.bump(1.5, "被夸了")
        other = MoodTracker(self.store, clock=lambda: self.now)
        state = other.load()
        self.assertAlmostEqual(state.score, 1.5, places=2)
        self.assertEqual(state.reason, "被夸了")

    def test_clamp_bounds(self):
        for _ in range(20):
            self.mood.bump(1.0, "夸")
        self.assertLessEqual(self.mood.load().score, HIGH)
        for _ in range(40):
            self.mood.bump(-1.0, "骂")
        self.assertGreaterEqual(self.mood.load().score, LOW)

    def test_decay_over_time(self):
        self.mood.bump(4.0, "被夸了")
        later = MoodTracker(self.store, clock=lambda: self.now + 6 * 3600)
        self.assertLess(later.load().score, 2.0)

    def test_praise_raises_mood(self):
        self.mood.note_text("肥鱼好可爱啊")
        self.assertGreater(self.mood.load().score, 0)

    def test_insult_lowers_mood(self):
        self.mood.note_text("大肥鱼你也太胖了")
        self.assertLess(self.mood.load().score, 0)

    def test_cooldown_blocks_repeat(self):
        self.mood.note_text("好可爱")
        first = self.mood.load().score
        self.mood.note_text("好可爱")
        self.assertAlmostEqual(self.mood.load().score, first, places=2)

    def test_owner_weight_is_stronger(self):
        self.mood.note_text("好可爱", from_owner=True)
        owner_score = self.mood.load().score

        store2 = MemoryStore(":memory:")
        other = MoodTracker(store2, clock=lambda: self.now)
        other.note_text("好可爱", from_owner=False)
        self.assertGreater(owner_score, other.load().score)

    def test_label_and_snapshot(self):
        self.mood.bump(4.0, "被夸了")
        snap = self.mood.snapshot()
        self.assertEqual(set(snap), {"score", "label", "reason", "changes", "updated_at"})
        self.assertIn("心情", snap["label"])
        self.assertEqual(self.mood.label(), "心情很好，尾巴一直在拍地板")

    def test_no_signal_returns_none(self):
        self.assertIsNone(self.mood.note_text("今天天气不错"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
