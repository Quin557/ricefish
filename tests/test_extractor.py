"""抽取/摘要：截断重试，绝不落半句。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ricefish.config import Config  # noqa: E402
from ricefish.llm.client import ChatResult  # noqa: E402
from ricefish.llm.extractor import Extractor  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402

MESSAGES = [
    {
        "id": 1,
        "message_id": 1,
        "sender_qq": 300,
        "sender_name": "阿澈",
        "text": "我最近在准备考研，数学不太好",
        "ts": 100,
    },
    {
        "id": 2,
        "message_id": 2,
        "sender_qq": 999,
        "sender_name": "肥鱼",
        "text": "考研很累吧",
        "ts": 101,
        "is_bot": True,
    },
]


class FakeLLM:
    available = True

    def __init__(self, results: list[ChatResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return ChatResult(text="", finish_reason="stop")


def make_config() -> Config:
    return Config.from_dict({"bot": {"name": "小满"}, "memory": {"extractor": {"enabled": True}}})


class SummaryGuardTests(unittest.IsolatedAsyncioTestCase):
    def build(self, results):
        self.store = MemoryStore(":memory:")
        self.config = make_config()
        self.llm = FakeLLM(results)
        self.prompts = PromptBuilder(self.config, self.store)
        self.extractor = Extractor(
            self.config, self.store, self.llm, self.prompts
        )

    async def asyncTearDown(self):
        self.store.close()

    async def test_truncated_summary_retries_then_stores_complete(self):
        self.build(
            [
                ChatResult(text="半句话", finish_reason="length", reasoning_chars=500),
                ChatResult(text="完整的一段摘要，讲考研和数学。", finish_reason="stop"),
            ]
        )
        text = await self.extractor.summarize("private", 300, MESSAGES)
        self.assertIn("完整", text)
        rows = self.store.latest_summaries("private", 300, limit=5)
        self.assertEqual(len(rows), 1)
        self.assertIn("完整", rows[0]["content"])
        # 两次调用都关思考
        for call in self.llm.calls:
            self.assertEqual(call.get("thinking"), "disabled")

    async def test_two_truncations_store_nothing(self):
        self.build(
            [
                ChatResult(text="半句", finish_reason="length"),
                ChatResult(text="还是半句", finish_reason="length"),
            ]
        )
        text = await self.extractor.summarize("private", 300, MESSAGES)
        self.assertEqual(text, "")
        self.assertEqual(self.store.latest_summaries("private", 300, limit=5), [])

    async def test_extract_retries_on_truncated_json(self):
        good = (
            '{"facts":[{"subject_qq":300,"kind":"event","content":"准备考研",'
            '"importance":4,"confidence":0.8}],"profile_patch":{},'
            '"open_loops":[{"subject_qq":300,"kind":"plan","content":"下周去沙县打工"}],'
            '"episode":{"summary":"聊了考研和打工","tone":"认真","topics":["考研","打工"]},'
            '"relationship_events":[{"kind":"promise","content":"考完一起吃饭"}],'
            '"summary":"","emotion":{}}'
        )
        self.build(
            [
                ChatResult(text='{"facts":[', finish_reason="length"),
                ChatResult(text=good, finish_reason="stop"),
            ]
        )
        added = await self.extractor.extract(
            "private", 300, MESSAGES, subject_qq=300
        )
        self.assertEqual(added, 1)
        self.assertTrue(
            any("准备考研" in item.get("content", "") for item in self.store.list_memories("private", 300))
        )
        loops = self.store.list_open_loops("private", 300, subject_qq=300)
        self.assertEqual(len(loops), 1)
        self.assertIn("沙县", loops[0]["content"])
        episodes = self.store.list_episodes("private", 300)
        self.assertEqual(len(episodes), 1)
        self.assertIn("考研", episodes[0]["summary"])
        events = self.store.list_relationship_events("private", 300, subject_qq=300)
        self.assertEqual(len(events), 1)
        self.assertIn("吃饭", events[0]["content"])
        self.assertEqual(self.llm.calls[-1].get("thinking"), "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
