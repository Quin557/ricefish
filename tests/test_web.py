"""Web 面板测试：起一个真 HTTP 服务，验证鉴权和各页面能渲染。"""

from __future__ import annotations

import http.client
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _temp import TempDir  # noqa: E402

from ricefish.config import Config  # noqa: E402
from ricefish.llm.prompt import PromptBuilder  # noqa: E402
from ricefish.memory.store import MemoryStore  # noqa: E402
from ricefish.web.server import PanelServer  # noqa: E402

TOKEN = "test-token"


class FakeApp:
    def __init__(self) -> None:
        self.store = MemoryStore(":memory:")
        self.config = Config.from_dict(
            {
                "bot": {"name": "小满", "mode": "active", "dry_run": True},
                "access": {"allowed_groups": [200], "allowed_friends": [300]},
            }
        )
        self.saved: dict = {}
        self._tmp = TempDir()
        self.persona_dir = Path(self._tmp.name)
        self.prompts = PromptBuilder(self.config, self.store, persona_dir=self.persona_dir)
        self.access_calls: list[tuple[list[int], list[int]]] = []

    def close(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def access_source(self) -> str:
        return "panel" if self.store.get_setting("access.groups") is not None else "config"

    def set_access(self, groups, friends, actor: str = "panel") -> None:
        self.config.apply_access(groups, friends)
        import json

        self.store.set_setting("access.groups", json.dumps(sorted(groups)))
        self.store.set_setting("access.friends", json.dumps(sorted(friends)))
        self.access_calls.append((sorted(groups), sorted(friends)))

    def known_groups(self) -> list[dict]:
        allowed = set(self.config.access.allowed_groups)
        return [
            {"id": 200, "name": "测试群", "allowed": 200 in allowed},
            {"id": 201, "name": "另一个群", "allowed": 201 in allowed},
        ]

    def known_friends(self) -> list[dict]:
        allowed = set(self.config.access.allowed_friends)
        return [
            {"id": 300, "name": "阿澈", "allowed": 300 in allowed},
            {"id": 301, "name": "小林", "allowed": 301 in allowed},
        ]

    def known_group_name(self, group_id: int) -> str:
        return "测试群" if int(group_id) == 200 else f"群 {group_id}"

    def known_friend_name(self, qq: int) -> str:
        return "阿澈" if int(qq) == 300 else f"好友 {qq}"

    def persona_path(self, scope_type: str, scope_id: int) -> Path:
        if scope_type == "group":
            return self.persona_dir / "groups" / f"{int(scope_id)}.md"
        if scope_type == "private":
            return self.persona_dir / "private" / f"{int(scope_id)}.md"
        return self.persona_dir / "global.md"

    def has_persona(self, scope_type: str, scope_id: int) -> bool:
        return self.persona_path(scope_type, scope_id).is_file()

    def status(self) -> dict:
        return {
            "bot_name": "小满",
            "self_id": 999,
            "nickname": "小满",
            "connected": True,
            "mode": "active",
            "dry_run": True,
            "readonly": False,
            "model": "deepseek-flash",
            "llm_ready": True,
            "uptime_sec": 125,
            "allowed_groups": 1,
            "allowed_friends": 1,
            "dispatcher": {"replies": 3, "proactive": 1},
            "curator": {"extracted": 2},
            "store": self.store.stats(),
            "groups_known": 1,
            "limits": {"groups": []},
            "concurrency": {
                "active": 1,
                "max_parallel": 6,
                "peak": 2,
                "dropped_busy": 0,
                "dropped_queue_full": 0,
                "timeouts": 0,
            },
            "plugins": [],
        }

    def save_runtime_settings(self, values: dict) -> None:
        self.saved.update(values)


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = FakeApp()
        cls.panel = PanelServer(cls.app, host="127.0.0.1", port=0, token=TOKEN)
        cls.panel.start()
        cls.port = cls.panel.port

    @classmethod
    def tearDownClass(cls):
        cls.panel.stop()
        cls.app.close()

    def setUp(self):
        self.app.set_access([200], [300])
        self.app.access_calls.clear()

    def request(self, method: str, path: str, body: dict | None = None,
                cookie: str = "", extra_headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = dict(extra_headers or {})
        payload = None
        if body is not None:
            payload = urlencode(body)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read().decode("utf-8", errors="replace")
        status = response.status
        headers_out = dict(response.getheaders())
        conn.close()
        return status, data, headers_out

    def test_login_page(self):
        status, body, _ = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("管理面板", body)

    def test_redirect_without_cookie(self):
        status, _, headers = self.request("GET", "/")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/login")

    def test_wrong_token(self):
        status, body, _ = self.request("POST", "/login", {"token": "nope"})
        self.assertEqual(status, 401)
        self.assertIn("Token 不对", body)

    def test_login_sets_cookie_and_pages_render(self):
        status, _, headers = self.request("POST", "/login", {"token": TOKEN})
        self.assertEqual(status, 303)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("ricefish_panel=", cookie)
        jar = f"ricefish_panel={TOKEN}"

        for path, expect in [
            ("/", "概览"),
            ("/groups", "会话"),
            ("/access", "白名单"),
            ("/profiles", "成员"),
            ("/memories", "长期记忆"),
            ("/personas", "人设"),
            ("/settings", "设置"),
            ("/logs", "日志"),
        ]:
            status, body, _ = self.request("GET", path, cookie=jar)
            self.assertEqual(status, 200, f"{path} 返回 {status}")
            self.assertIn(expect, body, f"{path} 缺少 {expect}")

    def test_login_via_token_url(self):
        """一键启动脚本用 /login?token=xxx 直接进面板。"""
        status, _, headers = self.request("GET", f"/login?token={TOKEN}")
        self.assertEqual(status, 303)
        self.assertIn("ricefish_panel=", headers.get("Set-Cookie", ""))
        bad, _, headers_bad = self.request("GET", "/login?token=wrong")
        self.assertEqual(bad, 200)
        self.assertNotIn("Set-Cookie", headers_bad)

    def test_status_api(self):
        status, body, _ = self.request("GET", "/api/status", cookie=f"ricefish_panel={TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("connected", body)

    def test_update_settings(self):
        status, _, _ = self.request(
            "POST",
            "/api/settings",
            {"mode": "passive", "dry_run": "1", "group_per_min": "2"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.saved.get("mode"), "passive")
        self.assertEqual(self.app.saved.get("group_per_min"), "2")

    def test_update_whitelist(self):
        status, _, _ = self.request(
            "POST",
            "/api/settings",
            {"allowed_groups": "200,300", "allowed_friends": "500"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.access_calls[-1], ([200, 300], [500]))

    def test_access_add_by_id(self):
        status, _, _ = self.request(
            "POST",
            "/api/access/add",
            {"kind": "group", "ids": "201"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.config.access.allowed_groups, [200, 201])
        _, body, _ = self.request("GET", "/access", cookie=f"ricefish_panel={TOKEN}")
        self.assertIn("另一个群", body)
        self.assertIn("面板（数据库）", body)

    def test_access_remove_last_allowed(self):
        status, _, _ = self.request(
            "POST",
            "/api/access/remove",
            {"kind": "group", "id": "200"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.config.access.allowed_groups, [])
        _, body, _ = self.request("GET", "/access", cookie=f"ricefish_panel={TOKEN}")
        self.assertIn("还没有加入任何群", body)

    def test_access_clear_and_bad_id(self):
        status, _, _ = self.request(
            "POST",
            "/api/access/add",
            {"kind": "friend", "ids": "301,302"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.config.access.allowed_friends, [300, 301, 302])
        status, _, _ = self.request(
            "POST",
            "/api/access/add",
            {"kind": "group", "ids": "abc"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.config.access.allowed_groups, [200])
        status, _, _ = self.request(
            "POST",
            "/api/access/clear",
            {"kind": "friend", "confirm": "1"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.config.access.allowed_friends, [])

    def test_profile_quick_edit_and_unlock(self):
        self.app.store.ensure_profile("group", 200, 300, "澈哥")
        self.app.store.update_profile("group", 200, 300, affinity=50, manual=True)
        status, body, _ = self.request(
            "POST",
            "/api/profile/quick",
            {
                "scope_type": "group",
                "scope_id": "200",
                "qq": "300",
                "notes": "喜欢猫",
                "affinity": "77",
                "manual_present": "1",
                "manual": "1",
            },
            cookie=f"ricefish_panel={TOKEN}",
            extra_headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        profile = self.app.store.get_profile("group", 200, 300)
        self.assertEqual(profile["notes"], "喜欢猫")
        self.assertEqual(profile["affinity"], 77)
        self.assertEqual(profile["is_override"], 1)
        # 不传 affinity 不会把它重置成 50；manual=0 能解锁
        status, body, _ = self.request(
            "POST",
            "/api/profile/quick",
            {
                "scope_type": "group",
                "scope_id": "200",
                "qq": "300",
                "notes": "改备注",
                "manual_present": "1",
                "manual": "0",
            },
            cookie=f"ricefish_panel={TOKEN}",
            extra_headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(status, 200)
        profile = self.app.store.get_profile("group", 200, 300)
        self.assertEqual(profile["affinity"], 77)
        self.assertEqual(profile["is_override"], 0)

    def test_profiles_filter_and_profile_page(self):
        self.app.store.ensure_profile("group", 201, 900, "另一个人")
        self.app.store.update_profile("group", 201, 900, notes="只属于 201")
        _, body, _ = self.request(
            "GET", "/profiles?t=group&id=200", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertNotIn("另一个人", body)
        _, body, _ = self.request(
            "GET",
            "/profile?t=group&id=201&qq=900",
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertIn("只属于 201", body)
        self.assertIn("她眼中的你", body)

    def test_sessions_hide_unauthorized(self):
        self.app.store.upsert_chat("group", 200, title="测试群")
        self.app.store.upsert_chat("group", 201, title="不该出现的群")
        _, body, _ = self.request("GET", "/groups", cookie=f"ricefish_panel={TOKEN}")
        self.assertIn("测试群", body)
        self.assertNotIn("不该出现的群", body)
        _, body_all, _ = self.request(
            "GET", "/groups?all=1", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertIn("不该出现的群", body_all)

    def test_private_persona_roundtrip(self):
        status, _, _ = self.request(
            "POST",
            "/api/persona",
            {
                "scope_type": "private",
                "scope_id": "300",
                "text": "只对阿澈：叫他澈哥，别太客气。",
            },
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        target = self.app.persona_dir / "private" / "300.md"
        self.assertTrue(target.is_file())
        self.assertIn("澈哥", target.read_text(encoding="utf-8"))
        _, body, _ = self.request(
            "GET", "/personas?t=private&id=300", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertIn("澈哥", body)

    def test_settings_has_no_raw_whitelist_inputs(self):
        _, body, _ = self.request("GET", "/settings", cookie=f"ricefish_panel={TOKEN}")
        self.assertNotIn('name="allowed_groups"', body)
        self.assertNotIn('name="allowed_friends"', body)
        self.assertIn("管理白名单", body)
        self.assertIn('name="companion_enabled"', body)

    def test_import_chat(self):
        payload = {
            "scope_type": "private",
            "scope_id": 300,
            "profiles": [{"qq": 300, "display_name": "阿澈"}],
            "memories": [{"content": "喜欢猫", "subject_qq": 300}],
            "open_loops": [{"content": "下周体检", "subject_qq": 300}],
        }
        status, _, _ = self.request(
            "POST",
            "/api/import",
            {
                "scope_type": "private",
                "scope_id": "300",
                "data": json.dumps(payload, ensure_ascii=False),
            },
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertTrue(
            any(
                "喜欢猫" in item.get("content", "")
                for item in self.app.store.list_memories("private", 300)
            )
        )

    def test_export_chat(self):
        self.app.store.upsert_chat("private", 300, title="阿澈")
        self.app.store.add_message(
            "private", 300, sender_qq=300, sender_name="阿澈", text="在吗", ts=1
        )
        status, body, headers = self.request(
            "GET", "/export?t=private&id=300", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        payload = json.loads(body)
        self.assertEqual(payload["scope_id"], 300)
        self.assertTrue(payload["messages"])

    def test_healthz_and_metrics(self):
        status, body, _ = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertIn('"ok": true', body)
        status, body, headers = self.request("GET", f"/metrics?token={TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn("ricefish_up 1", body)
        self.assertIn("text/plain", headers.get("Content-Type", ""))
        status, _, _ = self.request("GET", "/metrics")
        self.assertEqual(status, 401)

    def test_stickers_page_and_media(self):
        path = Path(self.app.persona_dir) / "sticker.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        sticker_id = self.app.store.add_sticker(
            "sticker-hash", str(path), emotion="开心", tags="猫"
        )
        status, body, _ = self.request(
            "GET", "/stickers", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertEqual(status, 200)
        self.assertIn("表情库", body)
        status, body, headers = self.request(
            "GET", f"/sticker/{sticker_id}", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertEqual(status, 200)
        self.assertIn("image/png", headers.get("Content-Type", ""))

    def test_update_companion_switch(self):
        status, _, _ = self.request(
            "POST",
            "/api/settings",
            {"companion_enabled": "0", "companion_idle_minutes": "90"},
            cookie=f"ricefish_panel={TOKEN}",
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.app.saved.get("companion_enabled"), "0")
        self.assertEqual(self.app.saved.get("companion_idle_minutes"), "90")

    def test_unauthorized_post(self):
        status, _, _ = self.request("POST", "/api/settings", {"mode": "passive"})
        self.assertEqual(status, 401)

    def test_group_detail(self):
        self.app.store.upsert_chat("group", 200, title="测试群")
        self.app.store.ensure_profile("group", 200, 300, "澈哥")
        self.app.store.add_memory("group", 200, "喜欢猫", subject_qq=300)
        status, body, _ = self.request(
            "GET", "/group?t=group&id=200", cookie=f"ricefish_panel={TOKEN}"
        )
        self.assertEqual(status, 200)
        self.assertIn("测试群", body)
        self.assertIn("澈哥", body)
        self.assertIn("喜欢猫", body)

    def test_static_css(self):
        status, body, _ = self.request("GET", "/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("--bg", body)

    def test_pwa_assets(self):
        for path, needle in [
            ("/manifest.json", "standalone"),
            ("/sw.js", "addEventListener"),
            ("/static/offline.html", "连不上"),
        ]:
            status, body, _ = self.request("GET", path)
            self.assertEqual(status, 200, f"{path} 返回 {status}")
            self.assertIn(needle, body, f"{path} 内容不对")
        _, _, headers = self.request("GET", "/sw.js")
        self.assertEqual(headers.get("Service-Worker-Allowed"), "/")

    def test_pwa_png_icons(self):
        for path in (
            "/static/icon-192.png",
            "/static/icon-512.png",
            "/static/icon-maskable-512.png",
            "/static/apple-touch-icon.png",
        ):
            status, _, headers = self.request("GET", path)
            self.assertEqual(status, 200, f"{path} 返回 {status}")
            self.assertIn("image/png", headers.get("Content-Type", ""))
        status, _, headers = self.request("GET", "/static/favicon.ico")
        self.assertEqual(status, 200)
        self.assertIn("image/x-icon", headers.get("Content-Type", ""))

    def test_manifest_lists_png_and_maskable(self):
        status, body, _ = self.request("GET", "/manifest.json")
        self.assertEqual(status, 200)
        self.assertIn("icon-192.png", body)
        self.assertIn("icon-512.png", body)
        self.assertIn("maskable", body)
        self.assertNotIn("icon.svg", body)

    def test_static_traversal_blocked(self):
        status, body, _ = self.request("GET", "/static/../server.py")
        self.assertNotEqual(status, 200)
        self.assertNotIn("class PanelServer", body)

    def test_layout_has_pwa_meta(self):
        status, body, _ = self.request("GET", "/", cookie=f"ricefish_panel={TOKEN}")
        self.assertEqual(status, 200)
        self.assertIn('rel="manifest"', body)
        self.assertIn("theme-color", body)
        self.assertIn("serviceWorker.register", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
