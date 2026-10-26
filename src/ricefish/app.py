"""应用装配：把各模块接起来，管生命周期。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

from .config import Config
from .core.concurrency import ConcurrencyGate
from .core.dispatcher import Dispatcher
from .core.ratelimit import RateLimiter
from .core.sender import Sender
from .features.commands import CommandHandler
from .features.companion import CompanionPinger
from .features.emoji import EmojiBook
from .features import emoji_inbox
from .features.games import GameManager
from .features.memes import MemeBook
from .features.plugins import PluginManager
from .features.proactive import CareTaker
from .features.report import WeeklyReport
from .features.scheduler import Scheduler
from .llm.client import DeepSeekClient
from .llm.extractor import Extractor
from .llm.prompt import PromptBuilder, ensure_persona_files
from .memory.curator import Curator
from .memory.embedding import EmbeddingProvider, SemanticIndex
from .memory.store import MemoryStore
from .onebot.client import OneBotClient

log = logging.getLogger("ricefish.app")


class BotApp:
    def __init__(self, config: Config, llm=None) -> None:
        self.config = config
        self.started_at = time.time()
        self._tasks: list[asyncio.Task] = []
        self._closed = False

        import os

        db_path = os.environ.get("RICEFISH_DB") or "data/bot.db"
        self.store = MemoryStore(db_path)
        self.persona_dir = Path("config/personas")
        self._load_runtime_settings()

        ensure_persona_files(config.bot.name, self.persona_dir)
        self.echo_mode = llm is not None
        self.llm = llm or DeepSeekClient(config.llm, self.store)
        self.prompts = PromptBuilder(config, self.store, persona_dir=self.persona_dir)
        self.emojis = EmojiBook.load()
        self.memes = MemeBook.load(config.tools.meme_pack)
        self._seed_stickers()
        if self.memes.enabled:
            log.info("已载入 %d 张梗图", len(self.memes))

        self.limiter = RateLimiter(config.rate_limit)
        self.limiter.set_proactive_limits(
            config.trigger.proactive.min_interval_sec,
            config.trigger.proactive.daily_quota,
        )

        self.client = OneBotClient(
            token=config.onebot.token,
            on_event=self._on_event,
            on_connect=self._on_connect,
        )
        self.sender = Sender(self.client, config)

        self.extractor = Extractor(config, self.store, self.llm, self.prompts)
        self.semantic = SemanticIndex(
            config.memory,
            self.store,
            EmbeddingProvider(config.memory.embedding_url, config.memory.embedding_model)
            if config.memory.embedding == "ollama"
            else None,
        )
        self.curator = Curator(config, self.store, self.extractor, semantic=self.semantic)
        self.commands = CommandHandler()
        self.games = GameManager(config, self.store, self.llm)
        self.plugins = PluginManager(config, self.store, self.sender)
        loaded = self.plugins.load()
        if loaded:
            log.info("已加载 %d 个插件", loaded)
        self.gate = ConcurrencyGate(
            config.concurrency.clamped(),
            config.concurrency.max_queue,
            config.concurrency.acquire_timeout_sec,
        )
        self.dispatcher = Dispatcher(
            config=config,
            store=self.store,
            client=self.client,
            sender=self.sender,
            llm=self.llm,
            prompts=self.prompts,
            limiter=self.limiter,
            curator=self.curator,
            emojis=self.emojis,
            commands=self.commands,
            games=self.games,
            plugins=self.plugins,
            gate=self.gate,
            semantic=self.semantic,
        )
        self.caretaker = CareTaker(
            config, self.store, self.llm, self.prompts, self.sender, self.limiter
        )
        self.companion = CompanionPinger(
            config,
            self.store,
            self.llm,
            self.prompts,
            self.sender,
            self.limiter,
            mood=self.dispatcher.mood,
        )
        self.report = WeeklyReport(config, self.store, self.sender)
        self.scheduler = Scheduler(
            config, self.store, self.caretaker, self.report, companion=self.companion
        )
        self.sync_chats_for_access()

        self.panel = None  # 由 web.server 注入

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(self) -> None:
        cfg = self.config
        log.info("启动 %s（mode=%s, dry_run=%s）", cfg.bot.name, cfg.bot.mode, cfg.bot.dry_run)
        if not cfg.access.allowed_groups and not cfg.access.allowed_friends:
            log.warning(
                "白名单为空：现在不会回复任何人。打开管理面板「白名单」添加群/好友，"
                "或改 config/bot.yaml 的 access 后重启。"
            )
        if not cfg.llm.api_key and not self.echo_mode:
            log.warning("没有检测到 DEEPSEEK_API_KEY，回复会全部失败。")

        await self.client.start(cfg.onebot.host, cfg.onebot.port)
        self._tasks.append(asyncio.create_task(self.curator.run_forever(30), name="curator"))
        self._tasks.append(asyncio.create_task(self.scheduler.run_forever(60), name="scheduler"))
        log.info("等待协议端连接……")

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("正在关闭……")
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.client.stop()
        try:
            self.store.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 回调
    # ------------------------------------------------------------------
    async def _on_event(self, event) -> None:
        await self.dispatcher.on_event(event)

    async def _on_connect(self) -> None:
        await self.dispatcher.on_connect()

    # ------------------------------------------------------------------
    # 设置热加载
    # ------------------------------------------------------------------
    def _load_runtime_settings(self) -> None:
        try:
            raw = self.store.all_settings()
        except Exception:
            return
        has_access_json = "access.groups" in raw or "access.friends" in raw
        values = {}
        for key in (
            "dry_run",
            "mode",
            "proactive_enabled",
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
            if key in raw and raw[key] != "":
                values[key] = raw[key]
        # 旧版白名单只在没有新 JSON 键时生效，避免两套值打架。
        if not has_access_json:
            for key in ("allowed_groups", "allowed_friends"):
                if key in raw and raw[key] != "":
                    values[key] = raw[key]
        if values:
            self.config.apply_runtime(values)
            log.info("已载入面板设置：%s", ", ".join(values))
        if has_access_json:
            groups = _json_ids(raw.get("access.groups"), self.config.access.allowed_groups)
            friends = _json_ids(raw.get("access.friends"), self.config.access.allowed_friends)
            self.config.apply_access(groups, friends)
            log.info(
                "已载入面板白名单：%d 群 / %d 好友",
                len(groups),
                len(friends),
            )

    def save_runtime_settings(self, values: dict) -> None:
        for key, value in values.items():
            self.store.set_setting(key, value)
        self.config.apply_runtime(values)
        self.limiter.set_proactive_limits(
            self.config.trigger.proactive.min_interval_sec,
            self.config.trigger.proactive.daily_quota,
        )
        log.info("面板设置已更新：%s", ", ".join(values))

    # ------------------------------------------------------------------
    # 白名单（面板唯一入口）
    # ------------------------------------------------------------------
    def access_source(self) -> str:
        """panel = 面板（数据库）为准；config = .env / bot.yaml。"""
        try:
            if self.store.get_setting("access.groups") is not None:
                return "panel"
            if self.store.get_setting("access.friends") is not None:
                return "panel"
            if self.store.get_setting("allowed_groups") is not None:
                return "panel"
            if self.store.get_setting("allowed_friends") is not None:
                return "panel"
        except Exception:
            pass
        return "config"

    def set_access(self, groups: list[int], friends: list[int], *, actor: str = "panel") -> None:
        groups = sorted({int(item) for item in groups if int(item) > 0})
        friends = sorted({int(item) for item in friends if int(item) > 0})
        self.config.apply_access(groups, friends)
        self.store.set_setting("access.groups", json.dumps(groups))
        self.store.set_setting("access.friends", json.dumps(friends))
        # 双写旧键：回滚到旧版本时仍能读到大致的名单
        self.store.set_setting("allowed_groups", ",".join(str(x) for x in groups))
        self.store.set_setting("allowed_friends", ",".join(str(x) for x in friends))
        self.sync_chats_for_access()
        try:
            self.store.audit(
                actor,
                "access",
                f"groups={','.join(map(str, groups))};friends={','.join(map(str, friends))}",
            )
        except Exception:
            log.debug("审计白名单失败", exc_info=True)

    def known_group_name(self, group_id: int) -> str:
        dispatcher = getattr(self, "dispatcher", None)
        if dispatcher is not None:
            name = dispatcher.group_titles.get(group_id)
            if name:
                return str(name)
        chat = self.store.get_chat("group", group_id)
        if chat and chat.get("title"):
            return str(chat["title"])
        return f"群 {group_id}"

    def known_friend_name(self, qq: int) -> str:
        dispatcher = getattr(self, "dispatcher", None)
        if dispatcher is not None:
            name = dispatcher.friend_names.get(qq)
            if name:
                return str(name)
        person = self.store.get_person(qq)
        if person and person.get("nickname"):
            return str(person["nickname"])
        return f"好友 {qq}"

    def known_groups(self) -> list[dict]:
        """给面板选择器用：NapCat 已知群 + 历史会话 + 白名单里的孤儿 ID。"""
        names: dict[int, str] = {}
        for chat in self.store.list_chats("group"):
            try:
                names[int(chat["scope_id"])] = str(chat.get("title") or "")
            except (TypeError, ValueError):
                continue
        dispatcher = getattr(self, "dispatcher", None)
        if dispatcher is not None:
            names.update({int(k): str(v) for k, v in dispatcher.group_titles.items()})
        for group_id in self.config.access.allowed_groups:
            names.setdefault(int(group_id), "")
        allowed = set(self.config.access.allowed_groups)
        items = [
            {
                "id": group_id,
                "name": name or f"群 {group_id}",
                "allowed": group_id in allowed,
            }
            for group_id, name in names.items()
        ]
        items.sort(key=lambda item: (not item["allowed"], item["name"]))
        return items

    def known_friends(self) -> list[dict]:
        names: dict[int, str] = {}
        for person in self.store.list_persons(500):
            try:
                names[int(person["qq"])] = str(person.get("nickname") or "")
            except (TypeError, ValueError):
                continue
        dispatcher = getattr(self, "dispatcher", None)
        if dispatcher is not None:
            names.update({int(k): str(v) for k, v in dispatcher.friend_names.items()})
        for qq in self.config.access.allowed_friends:
            names.setdefault(int(qq), "")
        allowed = set(self.config.access.allowed_friends)
        items = [
            {
                "id": qq,
                "name": name or f"好友 {qq}",
                "allowed": qq in allowed,
            }
            for qq, name in names.items()
        ]
        items.sort(key=lambda item: (not item["allowed"], item["name"]))
        return items

    def sync_chats_for_access(self) -> None:
        """让授权会话立即出现在面板（私聊不必等第一条消息）。"""
        for group_id in self.config.access.allowed_groups:
            self.store.upsert_chat(
                "group", int(group_id), title=self.known_group_name(int(group_id))
            )
        for qq in self.config.access.allowed_friends:
            self.store.upsert_chat(
                "private", int(qq), title=self.known_friend_name(int(qq))
            )

    def persona_path(self, scope_type: str, scope_id: int) -> Path:
        if scope_type == "group":
            return self.persona_dir / "groups" / f"{int(scope_id)}.md"
        if scope_type == "private":
            return self.persona_dir / "private" / f"{int(scope_id)}.md"
        return self.persona_dir / "global.md"

    def has_persona(self, scope_type: str, scope_id: int) -> bool:
        try:
            return self.persona_path(scope_type, scope_id).is_file()
        except (TypeError, ValueError, OSError):
            return False

    def _seed_stickers(self) -> None:
        """把内置表情和群友收件箱导入表情库；已存在按 hash 跳过。"""
        seeded = 0
        manifest: dict[str, dict] = {}
        try:
            raw = json.loads(Path("assets/emoji/manifest.json").read_text(encoding="utf-8"))
            for item in raw.get("items") or []:
                if isinstance(item, dict) and item.get("file"):
                    manifest[str(Path(str(item["file"])).resolve())] = item
        except Exception:
            log.debug("读取表情 manifest 失败", exc_info=True)
        try:
            for name, path in self.emojis.mapping.items():
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    continue
                digest = hashlib.sha1(data).hexdigest()[:20]
                meta = manifest.get(str(Path(path).resolve()), {})
                self.store.add_sticker(
                    digest,
                    str(path),
                    source=str(meta.get("origin") or "builtin"),
                    license=str(meta.get("license") or ""),
                    kind="sticker",
                    emotion=name,
                    tags=name,
                )
                seeded += 1
        except Exception:
            log.debug("导入内置表情失败", exc_info=True)
        try:
            for item in emoji_inbox.latest(300):
                path = emoji_inbox.get(str(item.get("id") or ""))
                if path is None:
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                digest = hashlib.sha1(data).hexdigest()[:20]
                scope = str(item.get("scope") or "")
                scope_type, scope_id = "", 0
                if ":" in scope:
                    kind, _, raw = scope.partition(":")
                    if kind in ("group", "private") and raw.isdigit():
                        scope_type, scope_id = kind, int(raw)
                self.store.add_sticker(
                    digest,
                    str(path),
                    scope_type=scope_type,
                    scope_id=scope_id,
                    sender_qq=int(item.get("qq") or 0),
                    source="inbox",
                    license="用户群聊图片，本地保存，默认不随项目分发",
                    kind="sticker",
                )
                seeded += 1
        except Exception:
            log.debug("导入收件箱表情失败", exc_info=True)
        if seeded:
            log.info("表情库已同步 %d 张（去重后以实际库为准）", seeded)

    # ------------------------------------------------------------------
    # 面板数据
    # ------------------------------------------------------------------
    def status(self) -> dict:
        store_stats = self.store.stats()
        return {
            "bot_name": self.config.bot.name,
            "self_id": self.client.self_id,
            "nickname": self.client.nickname,
            "connected": self.client.connected,
            "mode": self.config.bot.mode,
            "dry_run": self.config.bot.dry_run,
            "readonly": self.config.readonly,
            "model": self.config.llm.model,
            "llm_ready": self.llm.available,
            "uptime_sec": int(time.time() - self.started_at),
            "allowed_groups": len(self.config.access.allowed_groups),
            "allowed_friends": len(self.config.access.allowed_friends),
            "access_source": self.access_source(),
            "dispatcher": dict(self.dispatcher.stats),
            "curator": dict(self.curator.stats),
            "store": store_stats,
            "groups_known": len(self.dispatcher.group_titles),
            "limits": self.limiter.snapshot(),
            "mood": self.dispatcher.mood.snapshot(),
            "companion": self.companion.snapshot(),
            "concurrency": self.gate.snapshot(),
            "plugins": self.plugins.describe(),
            "games": dict(self.games.stats),
            "assets": {
                "emoji": len(self.emojis),
                "memes": len(self.memes),
                "voices": len(self.dispatcher.voice.pack.lines),
                "voice_kinds": self.dispatcher.voice.pack.kinds(),
            },
        }


def _json_ids(raw: str | None, default: list[int]) -> list[int]:
    """解析 settings 里的 JSON 列表；解析失败回退默认值。"""
    if raw is None:
        return list(default)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return list(default)
    if not isinstance(data, list):
        return list(default)
    out: list[int] = []
    for item in data:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in out:
            out.append(value)
    return out
