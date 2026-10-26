"""应用层白名单：JSON 来源、空列表、会话同步。"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _temp import TempDir  # noqa: E402

from ricefish.app import BotApp  # noqa: E402
from ricefish.config import Config  # noqa: E402


class AppAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.old_db = os.environ.get("RICEFISH_DB")
        os.environ["RICEFISH_DB"] = os.path.join(self.tmp.name, "bot.db")
        self.config = Config.from_dict(
            {
                "bot": {"name": "小满", "owner_qq": 1},
                "access": {"allowed_groups": [200], "allowed_friends": [300]},
                "web": {"enabled": False},
            }
        )
        self.app = BotApp(self.config)

    def tearDown(self):
        self.app.store.close()
        if self.old_db is None:
            os.environ.pop("RICEFISH_DB", None)
        else:
            os.environ["RICEFISH_DB"] = self.old_db
        self.tmp.cleanup()

    def test_panel_json_can_clear_whitelist(self):
        self.app.store.set_setting("access.groups", "[]")
        self.app.store.set_setting("access.friends", "[300]")
        self.app._load_runtime_settings()
        self.assertEqual(self.config.access.allowed_groups, [])
        self.assertEqual(self.config.access.allowed_friends, [300])
        self.assertEqual(self.app.access_source(), "panel")

    def test_set_access_writes_json_and_syncs_chats(self):
        self.app.set_access([201], [300, 301])
        self.assertEqual(self.config.access.allowed_groups, [201])
        self.assertEqual(self.config.access.allowed_friends, [300, 301])
        self.assertEqual(
            json.loads(self.app.store.get_setting("access.groups") or "[]"), [201]
        )
        self.assertIsNotNone(self.app.store.get_chat("group", 201))
        self.assertIsNotNone(self.app.store.get_chat("private", 301))
        self.assertEqual(self.app.access_source(), "panel")

    def test_known_lists_include_allowed_orphans(self):
        self.app.set_access([999], [888])
        groups = {item["id"]: item for item in self.app.known_groups()}
        friends = {item["id"]: item for item in self.app.known_friends()}
        self.assertTrue(groups[999]["allowed"])
        self.assertTrue(friends[888]["allowed"])
        self.assertIn("999", groups[999]["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
