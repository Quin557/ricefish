"""本地 Web 管理面板（标准库 http.server，无第三方依赖）。

只监听 127.0.0.1，用一次性 Token + Cookie 鉴权。
运行在独立线程里，通过 app 直接读写同一个 SQLite 连接（已加锁）。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from . import templates as tpl

if TYPE_CHECKING:  # pragma: no cover
    from ..app import BotApp

log = logging.getLogger("ricefish.web")

COOKIE_NAME = "ricefish_panel"
STATIC_DIR = Path(__file__).with_name("static")


class PanelServer:
    def __init__(self, app: "BotApp", host: str = "127.0.0.1", port: int = 8721, token: str = "") -> None:
        self.app = app
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(12)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        handler = _make_handler(self.app, self.token)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        # 端口填 0 时由系统分配，这里回读真实端口
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="panel", daemon=True
        )
        self._thread.start()
        log.info("面板已启动：http://%s:%s   Token: %s", self.host, self.port, self.token)
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def _make_handler(app: "BotApp", token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ricefish-panel"
        protocol_version = "HTTP/1.1"

        # -- 基础设施 --------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            log.debug("panel %s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _html(self, html: str, code: int = 200, extra: dict | None = None) -> None:
            self._send(code, html.encode("utf-8"), "text/html; charset=utf-8", extra)

        def _json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _wants_json(self) -> bool:
            if self.headers.get("X-Requested-With") == "fetch":
                return True
            return "application/json" in (self.headers.get("Accept") or "")

        def _redirect(self, location: str = "/") -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _cookies(self) -> dict[str, str]:
            raw = self.headers.get("Cookie") or ""
            out: dict[str, str] = {}
            for part in raw.split(";"):
                if "=" in part:
                    key, value = part.split("=", 1)
                    out[key.strip()] = value.strip()
            return out

        def _authorized(self) -> bool:
            return self._cookies().get(COOKIE_NAME) == token

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            parsed = parse_qs(text, keep_blank_values=True)
            return {key: values[-1] for key, values in parsed.items()}

        def _referer(self) -> str:
            ref = self.headers.get("Referer") or ""
            if ref.startswith("http://127.0.0.1") or ref.startswith("http://localhost"):
                split = urlsplit(ref)
                return split.path + (("?" + split.query) if split.query else "")
            return "/"

        def _serve_static(self, path: str) -> bool:
            """面板静态文件。只允许 STATIC_DIR 下的单层文件，不支持目录遍历。"""
            aliases = {
                "/manifest.json": "manifest.json",
                "/sw.js": "sw.js",
            }
            if path in aliases:
                name = aliases[path]
            elif path.startswith("/static/"):
                name = path[len("/static/") :]
            else:
                return False
            if (
                not name
                or "/" in name
                or "\\" in name
                or name.startswith(".")
                or Path(name).suffix.lower() not in {".svg", ".png", ".ico", ".json", ".js", ".html", ".css", ".webp"}
            ):
                return False
            file = STATIC_DIR / name
            if not file.is_file():
                self._html("<h1>404</h1>", code=404)
                return True
            mime = {
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
                ".webp": "image/webp",
                ".json": "application/json; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8",
            }.get(file.suffix, "application/octet-stream")
            extra = {
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache" if file.suffix == ".js" else "public, max-age=3600",
            }
            self._send(200, file.read_bytes(), mime, extra)
            return True

        # -- GET --------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            split = urlsplit(self.path)
            path = split.path
            query = parse_qs(split.query)

            if path == "/static/style.css":
                self._send(200, tpl.CSS.encode("utf-8"), "text/css; charset=utf-8")
                return
            if path.startswith("/static/") or path in ("/manifest.json", "/sw.js"):
                if self._serve_static(path):
                    return
            if path == "/login":
                # 一键启动脚本用 /login?token=xxx 直接登进去
                query_token = (query.get("token") or [""])[0]
                if query_token and query_token == token:
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header(
                        "Set-Cookie",
                        f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict",
                    )
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._html(tpl.page_login())
                return
            if path == "/healthz":
                status = app.status()
                self._json(
                    {
                        "ok": True,
                        "connected": bool(getattr(getattr(app, "client", None), "connected", False)),
                        "uptime_sec": int(status.get("uptime_sec", 0)),
                        "dry_run": bool(app.config.bot.dry_run),
                    }
                )
                return
            if path == "/metrics":
                query_token = (query.get("token") or [""])[0]
                if not self._authorized() and query_token != token:
                    self._json({"ok": False, "error": "unauthorized"}, code=401)
                    return
                status = app.status()
                store = status.get("store") or {}
                dispatcher = status.get("dispatcher") or {}
                lines = [
                    "# HELP ricefish_up Bot process is running",
                    "# TYPE ricefish_up gauge",
                    "ricefish_up 1",
                    "# HELP ricefish_connected OneBot websocket connected",
                    "# TYPE ricefish_connected gauge",
                    f"ricefish_connected {1 if status.get('connected') else 0}",
                    "# TYPE ricefish_messages_total counter",
                    f"ricefish_messages_total {int(store.get('messages', 0))}",
                    "# TYPE ricefish_replies_total counter",
                    f"ricefish_replies_total {int(dispatcher.get('replies', 0))}",
                    "# TYPE ricefish_memories gauge",
                    f"ricefish_memories {int(store.get('memories', 0))}",
                    "# TYPE ricefish_profiles gauge",
                    f"ricefish_profiles {int(store.get('profiles', 0))}",
                    "# TYPE ricefish_uptime_seconds gauge",
                    f"ricefish_uptime_seconds {int(status.get('uptime_sec', 0))}",
                ]
                body = ("\n".join(lines) + "\n").encode("utf-8")
                self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
                return
            if not self._authorized():
                self._redirect("/login")
                return

            try:
                if path == "/":
                    self._html(tpl.page_dashboard(app))
                elif path == "/groups":
                    self._html(
                        tpl.page_groups(
                            app,
                            show_all=(query.get("all") or ["0"])[0] == "1",
                            query=(query.get("q") or [""])[0],
                        )
                    )
                elif path == "/access":
                    self._html(
                        tpl.page_access(
                            app,
                            ok=(query.get("ok") or [""])[0],
                            err=(query.get("err") or [""])[0],
                        )
                    )
                elif path == "/stickers":
                    self._html(
                        tpl.page_stickers(
                            app,
                            query=(query.get("q") or [""])[0],
                            show_all=(query.get("all") or ["0"])[0] == "1",
                            tagged=_as_int((query.get("tagged") or ["0"])[0]),
                        )
                    )
                elif path.startswith("/sticker/"):
                    sticker_id = _as_int(path.rsplit("/", 1)[-1])
                    row = app.store.get_sticker(sticker_id)
                    if not row:
                        self._html("<h1>404</h1>", code=404)
                        return
                    file = Path(str(row.get("path") or ""))
                    if not file.is_file():
                        self._html("<h1>404</h1>", code=404)
                        return
                    mime = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".gif": "image/gif",
                        ".webp": "image/webp",
                        ".bmp": "image/bmp",
                    }.get(file.suffix.lower(), "application/octet-stream")
                    self._send(200, file.read_bytes(), mime)
                elif path == "/group":
                    scope_type = (query.get("t") or ["group"])[0]
                    scope_id = _as_int((query.get("id") or ["0"])[0])
                    self._html(tpl.page_group(app, scope_type, scope_id))
                elif path == "/profiles":
                    self._html(
                        tpl.page_profiles(
                            app,
                            scope_type=(query.get("t") or [""])[0],
                            scope_id=_as_int((query.get("id") or ["0"])[0]),
                            query=(query.get("q") or [""])[0],
                            show_all=(query.get("all") or ["0"])[0] == "1",
                        )
                    )
                elif path == "/profile":
                    self._html(
                        tpl.page_profile(
                            app,
                            scope_type=(query.get("t") or ["private"])[0],
                            scope_id=_as_int((query.get("id") or ["0"])[0]),
                            qq=_as_int((query.get("qq") or ["0"])[0]),
                        )
                    )
                elif path == "/export":
                    scope_type = (query.get("t") or ["private"])[0]
                    scope_id = _as_int((query.get("id") or ["0"])[0])
                    if scope_type not in ("group", "private") or scope_id <= 0:
                        self._html("<h1>参数不对</h1>", code=400)
                        return
                    data = app.store.export_chat(scope_type, scope_id)
                    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                    self._send(
                        200,
                        body,
                        "application/json; charset=utf-8",
                        {
                            "Content-Disposition": (
                                f'attachment; filename="ricefish-{scope_type}-{scope_id}.json"'
                            )
                        },
                    )
                elif path == "/memories":
                    self._html(tpl.page_memories(app, (query.get("q") or [""])[0]))
                elif path == "/personas":
                    kind = (
                        query.get("t")
                        or (["group"] if query.get("g") else ["global"])
                    )[0]
                    scope_id = _as_int(
                        (query.get("id") or query.get("g") or ["0"])[0]
                    )
                    self._html(tpl.page_personas(app, kind=kind, scope_id=scope_id))
                elif path == "/settings":
                    self._html(tpl.page_settings(app))
                elif path == "/logs":
                    self._html(tpl.page_logs(app))
                elif path == "/api/status":
                    self._json(app.status())
                else:
                    self._html("<h1>404</h1>", code=404)
            except Exception as exc:  # pragma: no cover
                log.exception("面板 GET 失败 %s", path)
                self._html(f"<pre>{tpl.esc(exc)}</pre>", code=500)

        # -- POST -------------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            form = self._form()

            if path == "/login":
                if form.get("token", "") == token:
                    cookie = f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict"
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", cookie)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._html(tpl.page_login("Token 不对"), code=401)
                return

            if path == "/logout":
                self.send_response(303)
                self.send_header("Location", "/login")
                self.send_header("Set-Cookie", f"{COOKIE_NAME}=; Path=/; Max-Age=0")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if not self._authorized():
                self._json({"ok": False, "error": "unauthorized"}, code=401)
                return

            try:
                self._handle_action(path, form)
            except Exception as exc:  # pragma: no cover
                log.exception("面板 POST 失败 %s", path)
                self._html(f"<pre>{tpl.esc(exc)}</pre>", code=500)

        def _handle_action(self, path: str, form: dict[str, str]) -> None:
            store = app.store
            actor = "panel"

            if path.startswith("/api/access/"):
                kind = form.get("kind", "group")
                if kind not in ("group", "friend"):
                    self._redirect("/access?err=bad_kind")
                    return
                groups = list(app.config.access.allowed_groups)
                friends = list(app.config.access.allowed_friends)
                target = groups if kind == "group" else friends

                if path.endswith("/add"):
                    raw_ids = form.get("ids", "")
                    if re.search(r"[^0-9,，、;；\s]", raw_ids):
                        self._redirect("/access?err=bad_id")
                        return
                    ids = _parse_ids(raw_ids)
                    if not ids:
                        self._redirect("/access?err=bad_id")
                        return
                    added = [item for item in ids if item not in target]
                    target.extend(added)
                    app.set_access(groups, friends, actor=actor)
                    self._redirect("/access?ok=added" if added else "/access?ok=exists")
                    return

                if path.endswith("/remove"):
                    target_id = _as_int(form.get("id", ""))
                    if target_id <= 0:
                        self._redirect("/access?err=bad_id")
                        return
                    if target_id in target:
                        target.remove(target_id)
                    app.set_access(groups, friends, actor=actor)
                    self._redirect("/access?ok=removed")
                    return

                if path.endswith("/clear"):
                    if form.get("confirm") != "1":
                        self._redirect("/access?err=confirm")
                        return
                    target.clear()
                    app.set_access(groups, friends, actor=actor)
                    self._redirect("/access?ok=cleared")
                    return

                self._redirect("/access?err=unknown")
                return

            if path == "/api/settings":
                values: dict[str, str] = {}
                if "mode" in form:
                    values["mode"] = form["mode"]
                if "dry_run" in form:
                    values["dry_run"] = "1" if form["dry_run"] == "1" else "0"
                if "proactive_enabled" in form:
                    values["proactive_enabled"] = (
                        "1" if form["proactive_enabled"] == "1" else "0"
                    )
                for key in (
                    "proactive_probability",
                    "proactive_min_interval",
                    "proactive_daily_quota",
                    "proactive_relevance_threshold",
                    "group_per_min",
                    "group_per_day",
                    "quiet_hours",
                    "bot_name",
                    "max_parallel",
                    "companion_enabled",
                    "companion_idle_minutes",
                    "companion_follow_up_minutes",
                    "companion_cooldown_minutes",
                    "companion_daily_per_chat",
                    "companion_daily_total",
                ):
                    if form.get(key) is not None:
                        values[key] = form[key]
                if values:
                    app.save_runtime_settings(values)
                    store.audit(actor, "settings", json.dumps(values, ensure_ascii=False))
                # 兼容旧脚本：非空白名单才替换，空值交给 /access 处理
                if form.get("allowed_groups") or form.get("allowed_friends"):
                    groups = _parse_ids(form.get("allowed_groups", "")) or list(
                        app.config.access.allowed_groups
                    )
                    friends = _parse_ids(form.get("allowed_friends", "")) or list(
                        app.config.access.allowed_friends
                    )
                    app.set_access(groups, friends, actor=actor)
                self._redirect("/settings")
                return

            if path == "/api/chat":
                scope_type = form.get("scope_type", "group")
                scope_id = _as_int(form.get("scope_id", ""))
                if scope_id <= 0:
                    self._redirect("/groups?err=bad_id")
                    return
                store.upsert_chat(
                    scope_type,
                    scope_id,
                    enabled=1 if form.get("enabled") else 0,
                    proactive_enabled=1 if form.get("proactive_enabled") else 0,
                )
                store.audit(actor, "chat", f"{scope_type}:{scope_id}")
                self._redirect(f"/group?t={scope_type}&id={scope_id}")
                return

            if path == "/api/profile/quick":
                scope_type = form.get("scope_type", "group")
                scope_id = _as_int(form.get("scope_id", ""))
                qq = _as_int(form.get("qq", ""))
                if scope_id <= 0 or qq <= 0:
                    self._json({"ok": False, "error": "参数不对"}, code=400)
                    return
                fields: dict[str, object] = {}
                if "notes" in form:
                    fields["notes"] = form.get("notes", "").strip()
                if "affinity" in form and form.get("affinity", "").strip() != "":
                    try:
                        fields["affinity"] = max(
                            0, min(100, int(form["affinity"].strip()))
                        )
                    except ValueError:
                        self._json({"ok": False, "error": "亲密度要是 0~100"}, code=400)
                        return
                manual = (
                    form.get("manual") == "1"
                    if (form.get("manual_present") == "1" or "manual" in form)
                    else None
                )
                try:
                    store.update_profile(
                        scope_type, scope_id, qq, manual=manual, **fields
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("快速保存档案失败", exc_info=True)
                    self._json({"ok": False, "error": str(exc)}, code=400)
                    return
                store.audit(actor, "profile.quick", f"{scope_type}:{scope_id}:{qq}")
                if self._wants_json():
                    profile = store.get_profile(scope_type, scope_id, qq) or {}
                    self._json({"ok": True, "profile": profile})
                else:
                    self._redirect(
                        form.get("next")
                        or f"/group?t={scope_type}&id={scope_id}"
                    )
                return

            if path == "/api/profile":
                scope_type = form.get("scope_type", "group")
                scope_id = _as_int(form.get("scope_id", ""))
                qq = _as_int(form.get("qq", ""))
                if scope_id <= 0 or qq <= 0:
                    self._redirect("/profiles?err=bad_id")
                    return
                fields = {}
                for key in (
                    "display_name",
                    "aliases",
                    "identity",
                    "relation",
                    "tags",
                    "preferences",
                    "taboos",
                    "notes",
                    "mood",
                ):
                    if key in form:
                        fields[key] = form[key].strip()
                affinity = form.get("affinity", "")
                if affinity.strip() != "":
                    try:
                        fields["affinity"] = max(0, min(100, int(affinity.strip())))
                    except ValueError:
                        self._redirect(f"/profile?t={scope_type}&id={scope_id}&qq={qq}&err=bad_affinity")
                        return
                manual = (
                    form.get("manual") == "1"
                    if (form.get("manual_present") == "1" or "manual" in form)
                    else None
                )
                store.update_profile(
                    scope_type, scope_id, qq, manual=manual, **fields
                )
                store.audit(actor, "profile", f"{scope_type}:{scope_id}:{qq}")
                self._redirect(
                    form.get("next")
                    or f"/profile?t={scope_type}&id={scope_id}&qq={qq}"
                )
                return

            if path in ("/api/memory/delete", "/api/memory/archive"):
                memory_id = _as_int(form.get("id", ""))
                if path.endswith("delete"):
                    store.delete_memory(memory_id)
                else:
                    store.archive_memory(memory_id, True)
                store.audit(actor, path.rsplit("/", 1)[-1], str(memory_id))
                self._redirect(self._referer())
                return

            if path in ("/api/loop/resolve", "/api/loop/delete"):
                loop_id = _as_int(form.get("id", ""))
                if loop_id <= 0:
                    self._redirect(self._referer())
                    return
                if path.endswith("delete"):
                    store.delete_open_loop(loop_id)
                else:
                    store.resolve_open_loop(loop_id)
                store.audit(actor, path.rsplit("/", 1)[-1], str(loop_id))
                self._redirect(self._referer())
                return

            if path in ("/api/sticker/update", "/api/sticker/delete"):
                sticker_id = _as_int(form.get("id", ""))
                if sticker_id <= 0:
                    self._redirect("/stickers?err=bad_id")
                    return
                if path.endswith("delete"):
                    store.delete_sticker(sticker_id)
                else:
                    store.update_sticker_tags(
                        sticker_id,
                        emotion=form.get("emotion", ""),
                        tags=form.get("tags", ""),
                    )
                    if "enabled" in form:
                        store.set_sticker_enabled(
                            sticker_id, form.get("enabled") == "1"
                        )
                    if "scope_type" in form:
                        store.set_sticker_scope(
                            sticker_id,
                            form.get("scope_type", ""),
                            _as_int(form.get("scope_id", "")),
                        )
                store.audit(actor, path.rsplit("/", 1)[-1], str(sticker_id))
                self._redirect(self._referer() or "/stickers")
                return

            if path == "/api/sticker/tag":
                import asyncio

                from ..features.sticker_tagger import tag_pending

                limit = max(1, min(20, _as_int(form.get("limit", "5")) or 5))
                try:
                    tagged = asyncio.run(tag_pending(app, limit))
                except Exception:
                    log.exception("表情打标失败")
                    tagged = 0
                store.audit(actor, "sticker.tag", str(tagged))
                self._redirect(f"/stickers?tagged={tagged}")
                return

            if path == "/api/persona":
                scope_type = form.get("scope_type", "global")
                scope_id = form.get("scope_id", "").strip()
                text = form.get("text", "")
                base = app.persona_dir
                base.mkdir(parents=True, exist_ok=True)
                if scope_type in ("group", "private") and scope_id.isdigit():
                    sub = "groups" if scope_type == "group" else "private"
                    target = base / sub / f"{scope_id}.md"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    back = f"/personas?t={scope_type}&id={scope_id}"
                else:
                    target = base / "global.md"
                    back = "/personas?t=global"
                target.write_text(text, encoding="utf-8")
                store.audit(actor, "persona", str(target))
                self._redirect(back)
                return

            if path == "/api/import":
                scope_type = form.get("scope_type", "private")
                scope_id = _as_int(form.get("scope_id", ""))
                try:
                    payload = json.loads(form.get("data", ""))
                    counts = store.import_chat(
                        payload, scope_type=scope_type, scope_id=scope_id
                    )
                except Exception as exc:
                    log.warning("导入失败: %s", exc)
                    self._redirect("/settings?err=import")
                    return
                store.audit(actor, "import", json.dumps(counts, ensure_ascii=False))
                self._redirect(f"/group?t={scope_type}&id={scope_id}")
                return

            if path == "/api/reset":
                self._redirect("/")
                return

            self._json({"ok": False, "error": "unknown action"}, code=404)

    return Handler


def _as_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _parse_ids(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in re.split(r"[,，、;；\s]+", raw or ""):
        chunk = chunk.strip()
        if chunk.isdigit():
            value = int(chunk)
            if value > 0 and value not in out:
                out.append(value)
    return out
