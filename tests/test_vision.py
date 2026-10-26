"""图片卡与视觉调用测试：不发网络请求。"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _temp import TempDir  # noqa: E402

from ricefish.config import Config  # noqa: E402
from ricefish.llm.client import ChatResult, DeepSeekClient  # noqa: E402
from ricefish.vision.cards import (  # noqa: E402
    ImageCard,
    analyze_image,
    ask_image,
    data_uri,
    guess_mime,
    load_image_bytes,
    parse_card,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class ParseCardTests(unittest.TestCase):
    def test_parse_json_with_code_fence(self):
        raw = """```json
        {"kind":"meme","summary":"初音端着饭碗","ocr":"震撼美味！",
         "entities":["初音未来"],"meme":"好吃到震撼","emotion":"开心",
         "reply_angles":["这碗饭看着比我还香"],"safety":"ok"}
        ```"""
        card = parse_card(raw)
        self.assertIsNotNone(card)
        self.assertEqual(card.kind, "meme")
        self.assertEqual(card.ocr, "震撼美味！")
        self.assertIn("这碗饭", card.to_prompt())
        self.assertIn("可以接", card.to_prompt())

    def test_parse_rejects_empty(self):
        self.assertIsNone(parse_card(""))
        self.assertIsNone(parse_card("{}"))

    def test_data_uri_and_mime(self):
        self.assertEqual(guess_mime(PNG), "image/png")
        self.assertTrue(data_uri(PNG).startswith("data:image/png;base64,"))

    def test_image_card_prompt_without_optional_fields(self):
        card = ImageCard(summary="一只猫", kind="photo")
        text = card.to_prompt()
        self.assertIn("画面：一只猫", text)
        self.assertNotIn("图上的字", text)


class _FakeVisionLLM:
    config = SimpleNamespace(vision_model="fake-vl", vision_max_tokens=700)

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.kwargs: dict = {}

    async def chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return ChatResult(text=self.text, finish_reason="stop")


class AnalyzeImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_image_passes_json_and_disables_thinking(self):
        llm = _FakeVisionLLM(
            '{"kind":"meme","summary":"一只鱼","ocr":"","entities":[],'
            '"meme":"","emotion":"开心","reply_angles":["好耶"],"safety":"ok"}'
        )
        card = await analyze_image(llm, PNG, "image/png", scope_key="group:1")
        self.assertIsNotNone(card)
        self.assertEqual(card.summary, "一只鱼")
        self.assertEqual(llm.kwargs.get("thinking"), "disabled")
        self.assertEqual(llm.kwargs.get("purpose"), "vision")
        self.assertEqual(llm.kwargs.get("model"), "fake-vl")

    async def test_analyze_image_falls_back_to_text(self):
        llm = _FakeVisionLLM("一只蓝白色的胖鱼在吃饭")
        card = await analyze_image(llm, PNG, "image/png")
        self.assertIsNotNone(card)
        self.assertIn("胖鱼", card.summary)

    async def test_ask_image_detail(self):
        llm = _FakeVisionLLM("图上的字是「震撼美味」")
        answer = await ask_image(
            llm, PNG, "image/png", "图上的字写的是什么", region="center"
        )
        self.assertIn("震撼美味", answer)
        self.assertEqual(llm.kwargs.get("thinking"), "disabled")
        self.assertEqual(llm.kwargs.get("purpose"), "vision")

    async def test_load_image_bytes_base64_and_local(self):
        raw = b"hello-image"
        encoded = base64.b64encode(raw).decode()
        loaded = await load_image_bytes(None, f"base64://{encoded}")
        self.assertEqual(loaded[0], raw)

        tmp = TempDir()
        try:
            path = Path(tmp.name) / "a.png"
            path.write_bytes(PNG)
            loaded = await load_image_bytes(None, str(path))
            self.assertEqual(loaded[0], PNG)
            self.assertEqual(loaded[1], "image/png")
        finally:
            tmp.cleanup()


class DescribeImageRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_length_retries_with_bigger_budget(self):
        config = Config.from_dict({"llm": {"vision_max_tokens": 200}})
        config.llm.api_key = "sk-test"
        client = DeepSeekClient(config.llm)
        empty = {
            "model": "m",
            "choices": [
                {"message": {"content": "", "reasoning_content": "想很久"}, "finish_reason": "length"}
            ],
        }
        good = {
            "model": "m",
            "choices": [{"message": {"content": "一张猫图"}, "finish_reason": "stop"}],
        }
        with mock.patch.object(client, "_post", side_effect=[empty, good]) as post:
            text = await client.describe_image("data:image/png;base64,xxx")
        self.assertEqual(text, "一张猫图")
        self.assertEqual(post.call_count, 2)
        first_payload = post.call_args_list[0].args[0]
        second_payload = post.call_args_list[1].args[0]
        self.assertEqual(first_payload["thinking"], {"type": "disabled"})
        self.assertGreater(second_payload["max_tokens"], first_payload["max_tokens"])


class ThinkingPayloadTests(unittest.TestCase):
    def test_thinking_for_purpose(self):
        config = Config.from_dict(
            {
                "llm": {
                    "thinking": "enabled",
                    "thinking_vision": "disabled",
                    "thinking_utility": "disabled",
                }
            }
        )
        self.assertEqual(config.llm.thinking_for("chat"), "enabled")
        self.assertEqual(config.llm.thinking_for("vision"), "disabled")
        self.assertEqual(config.llm.thinking_for("summary"), "disabled")
        client = DeepSeekClient(config.llm)
        payload = client._payload([], "m", 0.1, 100, None, False, thinking="disabled")
        self.assertEqual(payload["thinking"], {"type": "disabled"})


class ImageCardStoreTests(unittest.TestCase):
    def test_roundtrip_in_store(self):
        from ricefish.memory.store import MemoryStore

        store = MemoryStore(":memory:")
        try:
            card = ImageCard(
                kind="meme",
                summary="一只鱼",
                ocr="好耶",
                reply_angles=["好耶", "可爱"],
                emotion="开心",
            )
            store.upsert_image_card("abc", card.to_dict(), scope_type="group", scope_id=1)
            row = store.get_image_card("abc")
            self.assertIsNotNone(row)
            restored = ImageCard.from_dict(row)
            self.assertEqual(restored.summary, "一只鱼")
            self.assertEqual(restored.ocr, "好耶")
            self.assertEqual(restored.reply_angles, ["好耶", "可爱"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
