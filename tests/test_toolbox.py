"""工具调用与语音标记的测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ricefish.config import Config
from ricefish.features.toolbox import ToolBox
from ricefish.llm.tags import parse_reply


def make_config(**tools) -> Config:
    raw = {
        "bot": {"name": "肥鱼", "aliases": ["肥鱼"]},
        "tools": {"weather": True, "enable_calls": True, "search_key": ""},
    }
    raw["tools"].update(tools)
    return Config.from_dict(raw)


class ToolBoxTests(unittest.TestCase):
    def test_specs_without_search_key(self):
        box = ToolBox(make_config())
        names = [spec["function"]["name"] for spec in box.specs()]
        self.assertIn("get_weather", names)
        self.assertIn("get_current_time", names)
        # 没配 Key 也能搜（走免费网页兜底）
        self.assertIn("web_search", names)
        self.assertIn("read_url", names)

    def test_specs_with_search_key(self):
        box = ToolBox(make_config(search_key="tvly-xxx"))
        names = [spec["function"]["name"] for spec in box.specs()]
        self.assertIn("web_search", names)

    def test_specs_disabled(self):
        box = ToolBox(make_config(enable_calls=False))
        self.assertEqual(box.specs(), [])

    def test_weather_can_be_disabled(self):
        box = ToolBox(make_config(weather=False))
        names = [spec["function"]["name"] for spec in box.specs()]
        self.assertNotIn("get_weather", names)

    def test_parse_arguments(self):
        self.assertEqual(ToolBox._parse_args('{"city": "杭州"}'), {"city": "杭州"})
        self.assertEqual(ToolBox._parse_args("not json"), {})
        self.assertEqual(ToolBox._parse_args(None), {})
        self.assertEqual(ToolBox._parse_args({"a": 1}), {"a": 1})

    def test_clip(self):
        self.assertEqual(ToolBox.clip("abc"), "abc")
        self.assertTrue(ToolBox.clip("x" * 900).endswith("…"))

    def test_run_unknown_tool(self):
        import asyncio

        box = ToolBox(make_config())
        out = asyncio.run(box.run("nope", "{}"))
        self.assertIn("未知工具", out)


class VoiceTagTests(unittest.TestCase):
    def test_voice_tag(self):
        parsed = parse_reply("[语音:greet]在的呀")
        self.assertEqual(parsed.text, "在的呀")
        self.assertEqual(parsed.voice, "greet")

    def test_voice_tag_chinese_brackets(self):
        parsed = parse_reply("【语音：sad】唉")
        self.assertEqual(parsed.text, "唉")
        self.assertEqual(parsed.voice, "sad")

    def test_no_voice_tag(self):
        parsed = parse_reply("普通一句话")
        self.assertEqual(parsed.voice, "")

    def test_voice_tag_alone_is_empty(self):
        parsed = parse_reply("[语音:greet]")
        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.voice, "greet")


if __name__ == "__main__":
    unittest.main(verbosity=2)
