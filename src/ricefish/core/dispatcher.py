"""调度中心：从事件到回复的完整链路。

    message -> 落库 -> 白名单/命令 -> 触发决策 -> 限流 -> 组装 prompt
            -> 调模型 -> 解析标记 -> 分条发送 -> 记账 -> 记忆策展
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..companion.session import ChatSession
from ..features.commands import CommandContext, CommandHandler, is_command
from ..features.emoji import EmojiBook
from ..features.games import GameManager
from ..features import emoji_inbox
from ..features.mood import MoodTracker
from ..features.persona_learn import PersonaLearner
from ..features.toolbox import ToolBox
from ..features.voice import Voice
from ..llm.client import DeepSeekClient, LLMError
from ..llm.prompt import PromptBuilder, PromptContext
from ..llm.tags import parse_reply
from ..memory.curator import Curator
from ..memory.store import MemoryStore
from ..onebot import events as evmod
from ..onebot import segments as seg
from ..onebot.client import OneBotClient
from ..util.text import now_local
from ..vision.cards import ImageCard, analyze_image, ask_image, guess_mime, load_image_bytes
from .concurrency import Busy, ConcurrencyGate
from . import streaming
from .ratelimit import RateLimiter
from .sender import Sender
from .trigger import IGNORE, PROACTIVE, REPLY, TriggerInput, decide

log = logging.getLogger("ricefish.dispatcher")

FALLBACKS = [
    "等下，脑子卡了一下",
    "啊…刚走神了，你再说一遍",
    "嗯？我刚刚没看清",
]

POKE_REPLIES = ["干嘛", "戳我干啥", "嗯？", "别戳了别戳了", "在的在的"]


class Dispatcher:
    def __init__(
        self,
        *,
        config: Config,
        store: MemoryStore,
        client: OneBotClient,
        sender: Sender,
        llm: DeepSeekClient,
        prompts: PromptBuilder,
        limiter: RateLimiter,
        curator: Curator,
        emojis: EmojiBook,
        commands: CommandHandler | None = None,
        games: GameManager | None = None,
        plugins=None,
        gate: ConcurrencyGate | None = None,
        semantic=None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.client = client
        self.sender = sender
        self.llm = llm
        self.prompts = prompts
        self.limiter = limiter
        self.curator = curator
        self.emojis = emojis
        self.commands = commands or CommandHandler()
        self.games = games or GameManager(config, store, llm, rng)
        self.plugins = plugins
        self.semantic = semantic
        self.voice = Voice(config)
        self.toolbox = ToolBox(config)
        self.mood = MoodTracker(store)
        self.persona_learner = PersonaLearner()
        self._image_tasks: set[asyncio.Task] = set()
        self.gate = gate or ConcurrencyGate(config.concurrency.clamped())
        self.rng = rng or random.Random()

        self.group_titles: dict[int, str] = {}
        self.friend_names: dict[int, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_probe: dict[str, float] = {}
        self._voice_tasks: set[asyncio.Task] = set()
        self._last_auto_emoji: dict[str, float] = {}
        self._last_sticker: dict[str, list[int]] = {}
        self._image_cache: dict[str, ImageCard] = {}
        self._last_image: dict[str, tuple[bytes, str]] = {}
        self.toolbox.vision_lookup = self._look_closer
        self.stats = {
            "messages": 0,
            "replies": 0,
            "proactive": 0,
            "ignored": 0,
            "dropped": 0,
            "llm_errors": 0,
            "last_event_ts": 0,
        }

    # ------------------------------------------------------------------
    # 事件入口
    # ------------------------------------------------------------------
    async def on_event(self, event: evmod.Event) -> None:
        self.stats["last_event_ts"] = int(time.time())
        if isinstance(event, evmod.MessageEvent):
            await self.handle_message(event)
        elif isinstance(event, evmod.NoticeEvent):
            await self.handle_notice(event)

    async def on_connect(self) -> None:
        await self.refresh_account()

    async def refresh_account(self) -> None:
        try:
            groups = await self.client.get_group_list()
            if isinstance(groups, list):
                for item in groups:
                    if not isinstance(item, dict):
                        continue
                    group_id = int(item.get("group_id") or 0)
                    title = str(item.get("group_name") or "").strip()
                    if group_id:
                        self.group_titles[group_id] = title
                        if self.config.access.group_ok(group_id):
                            self.store.upsert_chat("group", group_id, title=title)
        except Exception as exc:
            log.debug("拉取群列表失败（不影响使用）: %s", exc)
        try:
            friends = await self.client.get_friend_list()
            if isinstance(friends, list):
                for item in friends:
                    if isinstance(item, dict):
                        qq = int(item.get("user_id") or 0)
                        if qq:
                            nickname = str(item.get("nickname") or "")
                            self.friend_names[qq] = nickname
                            if self.config.access.friend_ok(qq):
                                self.store.upsert_chat("private", qq, title=nickname)
        except Exception as exc:
            log.debug("拉取好友列表失败（不影响使用）: %s", exc)
        log.info(
            "已加载 %d 个群、%d 个好友", len(self.group_titles), len(self.friend_names)
        )

    # ------------------------------------------------------------------
    # 消息
    # ------------------------------------------------------------------
    async def handle_message(self, event: evmod.MessageEvent) -> None:
        if event.message_id and self.store.message_exists(event.message_id):
            return
        self.stats["messages"] += 1

        # 语音消息先转文字（配了 asr_command 才走）
        if self.voice.asr_enabled and not (event.text or "").strip():
            transcript = await self._transcribe_voice(event)
            if transcript:
                event.text = transcript

        self.store.add_message(
            event.scope_type,
            event.scope_id,
            message_id=event.message_id,
            sender_qq=event.user_id,
            sender_name=event.sender_name,
            text=event.text,
            segments=event.segments,
            raw=event.raw,
            ts=event.time,
            is_bot=event.is_self,
        )
        if event.is_self:
            return
        log.info(
            "收到 %s %s：%s",
            event.scope_key,
            event.sender_name or event.user_id,
            (event.text or "[图片/其他]")[:80],
        )

        self._touch_profile(event)

        if not self._allowed(event):
            log.debug("未授权会话，忽略：%s", event.scope_key)
            return
        if not self.store.chat_enabled(event.scope_type, event.scope_id):
            log.debug("会话已关闭：%s", event.scope_key)
            return
        if self._muted(event.scope_key):
            log.debug("会话静音中：%s", event.scope_key)
            return

        self.mood.note_text(
            event.text, from_owner=bool(event.user_id == self.config.bot.owner_qq)
        )
        self._capture_images(event)

        if await self._maybe_tag_pending(event):
            return
        if await self._maybe_save_sticker(event):
            return

        cmd_ctx = CommandContext(
            config=self.config,
            store=self.store,
            sender=self.sender,
            llm=self.llm,
            prompts=self.prompts,
            limiter=self.limiter,
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            user_qq=event.user_id,
            user_name=event.sender_name,
            args="",
            raw=event.text,
            image_files=seg.image_files(event.segments),
            emojis=self.emojis,
            client=self.client,
        )

        if is_command(event.text) or is_command(command_text(event)):
            text = event.text if is_command(event.text) else command_text(event)
            if await self.games.handle(text, cmd_ctx):
                return
            if await self.commands.handle(text, cmd_ctx):
                return

        if self.plugins is not None and await self.plugins.dispatch(event):
            self.store.mark_replied(event.message_id)
            return

        # 进行中的小游戏先接话（猜词 / 海龟汤提问）
        bare_text = command_text(event) or event.text
        game_reply = await self.games.intercept(event, cmd_ctx, bare_text)
        if game_reply:
            await self._send_game_reply(event, game_reply)
            return

        decision = self._decide(event)
        if decision.action != IGNORE:
            self._maybe_learn_persona(event)
        if decision.action == IGNORE:
            self.stats["ignored"] += 1
            log.debug("不回复 %s（%s）", event.scope_key, decision.reason)
            return

        verdict = self.limiter.check_group_send(event.scope_id) if event.is_group else None
        if verdict is not None and not verdict:
            log.info("限流命中 %s: %s", event.scope_key, verdict.reason)
            return
        user_verdict = self.limiter.check_user(event.user_id)
        if not user_verdict:
            log.info("用户限流 %s: %s", event.user_id, user_verdict.reason)
            return

        # 并发闸门：最多 6 个会话同时生成，同一会话不并发
        try:
            async with self.gate.slot(event.scope_key):
                if decision.action == PROACTIVE:
                    if not await self._proactive_worthy(event):
                        self.stats["ignored"] += 1
                        return
                await self._respond(
                    event,
                    proactive=decision.action == PROACTIVE,
                    reason=decision.reason,
                )
        except Busy as exc:
            self.stats["dropped"] += 1
            log.info("并发已满，跳过 %s（%s）", event.scope_key, exc.reason)
            return

    # -- 决策辅助 ----------------------------------------------------
    async def _send_game_reply(self, event: evmod.MessageEvent, text: str) -> None:
        try:
            await self.sender.send_text(
                event.scope_type,
                event.scope_id,
                text,
                reply_to=event.message_id or None,
                reason="game",
            )
        except Exception:
            return
        self.limiter.note_send(event.scope_type, event.scope_id, event.user_id, text)
        self.store.add_message(
            event.scope_type,
            event.scope_id,
            sender_qq=self.client.self_id,
            sender_name=self.config.bot.name,
            text=text,
            is_bot=True,
        )
        self.store.mark_replied(event.message_id)

    def _touch_profile(self, event: evmod.MessageEvent) -> None:
        self.store.ensure_person(event.user_id, event.sender_name)
        self.store.note_profile_message(
            event.scope_type, event.scope_id, event.user_id, event.sender_name
        )
        if event.is_group:
            self.store.upsert_chat(
                "group", event.scope_id, title=self.group_titles.get(event.scope_id, "")
            )

    def _allowed(self, event: evmod.MessageEvent) -> bool:
        if event.is_group:
            return self.config.access.group_ok(event.scope_id)
        return self.config.access.friend_ok(event.user_id)

    RANDOM_EMOJI_NAMES = {"随机", "随机表情", "群友表情", "表情包", "来一张"}

    #: 模型说中文类别，映射到语音包里的英文 kind
    VOICE_KIND_ALIASES = {
        "撒娇": "coquetry",
        "卖萌": "coquetry",
        "摸头": "pat",
        "贴贴": "pat",
        "欢迎": "greet",
        "打招呼": "greet",
        "庆祝": "celebrate",
        "完成": "celebrate",
        "好耶": "celebrate",
        "难过": "sad",
        "委屈": "sad",
        "待机": "idle",
        "随便": "idle",
        "干活": "running",
        "提醒": "todo",
        "答应": "approval",
        "批准": "approval",
    }

    #: 提示词里展示给模型的中文名
    VOICE_KIND_LABELS = {
        "coquetry": "撒娇",
        "pat": "摸头",
        "greet": "欢迎",
        "celebrate": "庆祝",
        "sad": "委屈",
        "idle": "待机",
        "running": "干活",
        "todo": "提醒",
        "approval": "答应",
    }

    def _random_sticker(self, scope_type: str, scope_id: int, text: str = "") -> tuple[str, int]:
        """优先从表情库按会话/当前话题挑一张；没有就退回旧收件箱。"""
        scope_key = f"{scope_type}:{scope_id}"
        recent = self._last_sticker.get(scope_key, [])[-5:]
        try:
            row = self.store.pick_sticker(
                scope_type=scope_type,
                scope_id=scope_id,
                query=text[:80],
                exclude_ids=recent,
            )
        except Exception:
            row = None
        if row and row.get("path"):
            return str(row["path"]), int(row.get("id") or 0)
        items = emoji_inbox.latest(30)
        if not items:
            return "", 0
        for item in self.rng.sample(items, len(items)):
            path = emoji_inbox.get(str(item.get("id") or ""))
            if path and path.stat().st_size <= 2_000_000:
                return str(path), 0
        return "", 0

    def _apply_random_tags(self, parsed, event: evmod.MessageEvent | None = None):
        names = [name for kind, name in parsed.tags if kind == "表情"]
        if not any(name in self.RANDOM_EMOJI_NAMES for name in names):
            return parsed
        scope_type = event.scope_type if event is not None else ""
        scope_id = event.scope_id if event is not None else 0
        text = event.text if event is not None else ""
        path, sticker_id = self._random_sticker(scope_type, scope_id, text)
        if path and path not in parsed.images:
            parsed.images.append(path)
            if sticker_id:
                key = f"{scope_type}:{scope_id}"
                bucket = self._last_sticker.setdefault(key, [])
                bucket.append(sticker_id)
                del bucket[:-5]
                try:
                    self.store.note_sticker_used(sticker_id)
                except Exception:
                    pass
        return parsed

    async def _maybe_auto_emoji(self, event: evmod.MessageEvent, already_image: bool) -> None:
        """回复完偶尔补一张群友表情，让聊天更像真人。"""
        if already_image:
            return
        probability = float(getattr(self.config.send, "auto_emoji_probability", 0) or 0)
        if probability <= 0 or self.rng.random() >= probability:
            return
        now = time.time()
        cooldown = int(getattr(self.config.send, "auto_emoji_cooldown_sec", 0) or 0)
        if now - self._last_auto_emoji.get(event.scope_key, 0) < cooldown:
            return
        path, sticker_id = self._random_sticker(
            event.scope_type, event.scope_id, event.text
        )
        if not path:
            return
        self._last_auto_emoji[event.scope_key] = now
        if sticker_id:
            bucket = self._last_sticker.setdefault(event.scope_key, [])
            bucket.append(sticker_id)
            del bucket[:-5]
            try:
                self.store.note_sticker_used(sticker_id)
            except Exception:
                pass
        try:
            await self.sender.send_images(
                event.scope_type, event.scope_id, [path], reason="auto-emoji"
            )
        except Exception:
            log.debug("自动表情发送失败", exc_info=True)

    # -- 自动收图 / 自动学设定 ---------------------------------------
    def _capture_images(self, event: evmod.MessageEvent) -> None:
        files = seg.image_files(event.segments)
        if not files:
            return
        task = asyncio.create_task(self._capture_task(event, files[:2]))
        self._image_tasks.add(task)
        task.add_done_callback(self._image_tasks.discard)

    async def _capture_task(self, event: evmod.MessageEvent, files: list[str]) -> None:
        for file in files:
            try:
                path = await emoji_inbox.capture(
                    self.client,
                    file,
                    sender=event.sender_name,
                    qq=event.user_id,
                    scope=event.scope_key,
                )
            except Exception:
                log.debug("收图失败", exc_info=True)
                continue
            if path:
                log.info("收进表情收件箱：%s（来自 %s）", path, event.sender_name)
                sticker_id = 0
                try:
                    data = Path(path).read_bytes()
                    digest = hashlib.sha1(data).hexdigest()[:20]
                    sticker_id = self.store.add_sticker(
                        digest,
                        str(path),
                        scope_type=event.scope_type,
                        scope_id=event.scope_id,
                        sender_qq=event.user_id,
                        source="inbox",
                        license="用户群聊图片，本地保存，默认不随项目分发",
                        kind="sticker",
                    )
                except Exception:
                    log.debug("写表情库失败", exc_info=True)
                if sticker_id and getattr(self.config.tools, "sticker_autotag", True):
                    row = self.store.get_sticker(sticker_id) or {}
                    if not (row.get("emotion") or row.get("tags")):
                        await self._autotag_sticker(sticker_id, path, event.scope_key)

    async def _autotag_sticker(self, sticker_id: int, path: str, scope_key: str) -> None:
        """收图后顺手让视觉模型打个情绪/标签；失败不影响聊天。"""
        try:
            data = Path(path).read_bytes()
        except OSError:
            return
        try:
            card = await analyze_image(
                self.llm, data, guess_mime(data, path), scope_key=scope_key
            )
        except Exception:
            log.debug("表情自动打标失败", exc_info=True)
            return
        if card is None:
            return
        tags = ",".join([item for item in [card.kind, *card.entities] if item][:6])
        try:
            self.store.update_sticker_tags(
                sticker_id,
                emotion=card.emotion or "",
                tags=tags,
                nsfw=1 if card.safety == "nsfw" else 0,
            )
            log.info("表情已自动打标 #%s：%s %s", sticker_id, card.emotion, tags)
        except Exception:
            log.debug("写表情标签失败", exc_info=True)

    _SAVE_STICKER_RE = re.compile(
        r"(保存|收藏|收|存).{0,6}(这个|这张|一下|当前).{0,4}表情|"
        r"(把|将).{0,4}表情.{0,4}(存|收|保存)"
    )

    async def _maybe_save_sticker(self, event: evmod.MessageEvent) -> bool:
        text = (event.text or "").strip()
        if not text or not self._SAVE_STICKER_RE.search(text):
            return False
        source = self._find_recent_image(event)
        if not source:
            # 图片的下载/入库可能还在后台跑，给它一点时间
            await asyncio.sleep(0.8)
            source = self._find_recent_image(event)
        if not source:
            await self._send_notice(
                event, "先发一张图，或者引用那张图再说「保存这个表情」。", "sticker-save"
            )
            return True
        path = source
        if not Path(source).is_file():
            path = await emoji_inbox.capture(
                self.client,
                source,
                sender=event.sender_name,
                qq=event.user_id,
                scope=event.scope_key,
            )
        if not path:
            await self._send_notice(
                event, "图没拿下来，可能是太大了或 NapCat 没给文件。", "sticker-save"
            )
            return True
        try:
            data = Path(path).read_bytes()
        except OSError:
            await self._send_notice(event, "图读不出来，再发一次？", "sticker-save")
            return True
        if len(data) > 10_000_000:
            await self._send_notice(
                event, "这张图超过 10MB 了，先压缩一下再发？", "sticker-save"
            )
            return True
        digest = hashlib.sha1(data).hexdigest()[:20]
        sticker_id = self.store.add_sticker(
            digest,
            str(path),
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            sender_qq=event.user_id,
            source="inbox",
            license="用户群聊图片，本地保存，默认不随项目分发",
            kind="sticker",
        )
        existing = self.store.get_sticker(sticker_id) or {}
        if existing.get("emotion") or existing.get("tags"):
            desc = str(existing.get("emotion") or existing.get("tags") or "这个表情")[:40]
            await self._send_notice(
                event, f"存好了，我记成「{desc}」。", "sticker-save"
            )
            return True
        card = None
        if getattr(self.config.tools, "sticker_autotag", True):
            card = await analyze_image(
                self.llm, data, guess_mime(data, str(path)), scope_key=event.scope_key
            )
        if card is not None:
            tags = ",".join([item for item in [card.kind, *card.entities] if item][:6])
            self.store.update_sticker_tags(
                sticker_id,
                emotion=card.emotion or "",
                tags=tags,
                nsfw=1 if card.safety == "nsfw" else 0,
            )
            desc = f"{card.emotion or '这个表情'}" + (f"（{tags}）" if tags else "")
            await self._send_notice(
                event, f"存好了，我记成「{desc}」。", "sticker-save"
            )
        else:
            self.store.set_state(f"sticker_pending:{event.scope_key}", sticker_id)
            await self._send_notice(
                event,
                "存下来了，但我没看清它是什么意思。你回一句「这个表情是……」我就记下来。",
                "sticker-save",
            )
        return True

    async def _maybe_tag_pending(self, event: evmod.MessageEvent) -> bool:
        key = f"sticker_pending:{event.scope_key}"
        raw = self.store.get_state(key)
        if not raw:
            return False
        text = (event.text or "").strip()
        if is_command(text) or len(text) < 2 or len(text) > 60:
            return False
        try:
            sticker_id = int(raw)
        except ValueError:
            self.store.set_state(key, "")
            return False
        self.store.update_sticker_tags(
            sticker_id, emotion=text[:40], tags=text[:200]
        )
        self.store.set_state(key, "")
        await self._send_notice(
            event, f"记下了：这个表情是「{text[:40]}」。", "sticker-tag"
        )
        return True

    def _find_recent_image(self, event: evmod.MessageEvent) -> str:
        """优先引用消息里的图，其次当前消息，最后本会话最近一张。"""
        if event.reply_id:
            row = self.store.get_message_by_id(event.reply_id)
            if row:
                try:
                    segments = json.loads(row.get("segments_json") or "[]")
                except ValueError:
                    segments = []
                files = seg.image_files(segments) if isinstance(segments, list) else []
                if files:
                    return str(files[0])
        current = seg.image_files(event.segments)
        if current:
            return str(current[0])
        items = emoji_inbox.latest_for_scope(event.scope_key, 1)
        if items:
            path = emoji_inbox.get(str(items[0].get("id") or ""))
            if path:
                return str(path)
        return ""

    async def _send_notice(
        self, event: evmod.MessageEvent, text: str, reason: str = "notice"
    ) -> None:
        try:
            await self.sender.send_text(
                event.scope_type,
                event.scope_id,
                text,
                quote=False,
                reason=reason,
            )
            self.store.add_message(
                event.scope_type,
                event.scope_id,
                sender_qq=self.client.self_id,
                sender_name=self.config.bot.name,
                text=text,
                is_bot=True,
            )
            if event.message_id:
                self.store.mark_replied(event.message_id)
        except Exception:
            log.debug("发送提示失败", exc_info=True)

    def _maybe_learn_persona(self, event: evmod.MessageEvent) -> None:
        text = (event.text or "").strip()
        if not text:
            return
        if not (event.is_group is False or event.at_ids or event.reply_id):
            return
        learned = self.persona_learner.learn(
            text, event.sender_name or str(event.user_id), scope=event.scope_key
        )
        if learned:
            log.info("学到设定：%s", learned)

    def _muted(self, scope_key: str) -> bool:
        raw = self.store.get_state(f"mute:{scope_key}")
        if not raw:
            return False
        try:
            until = int(raw)
        except ValueError:
            return False
        if until <= time.time():
            self.store.set_state(f"mute:{scope_key}", 0)
            return False
        return True

    def _quoted_is_me(self, event: evmod.MessageEvent) -> bool:
        if not event.reply_id:
            return False
        row = self.store.get_message_by_id(event.reply_id)
        return bool(row and row.get("is_bot"))

    def _decide(self, event: evmod.MessageEvent):
        now = now_local()
        is_quiet = self.config.rate_limit.is_quiet(now.hour, now.minute)
        proactive_ready = True
        if event.is_group:
            chat = self.store.get_chat(event.scope_type, event.scope_id) or {}
            proactive_ready = bool(chat.get("proactive_enabled", 1)) and bool(
                self.limiter.check_proactive(event.scope_id)
            )
        has_content = bool(event.text.strip()) or any(
            seg.seg_type(item) not in ("reply", "at") for item in event.segments
        )
        probe = TriggerInput(
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            user_id=event.user_id,
            self_id=event.self_id or self.client.self_id,
            text=event.text,
            at_ids=event.at_ids,
            reply_id=event.reply_id,
            is_self=event.is_self,
            quoted_is_me=self._quoted_is_me(event),
            allowed=True,
            is_quiet=is_quiet,
            proactive_ready=proactive_ready,
            rng_value=self.rng.random(),
            has_content=has_content,
        )
        return decide(self.config, probe)

    async def _proactive_worthy(self, event: evmod.MessageEvent) -> bool:
        """主动插话前先花一次便宜调用判断"接话自不自然"。"""
        now = time.time()
        last = self._last_probe.get(event.scope_key, 0.0)
        if now - last < 90:
            return False
        self._last_probe[event.scope_key] = now
        recent_rows = self.store.recent_messages(
            event.scope_type, event.scope_id, limit=8, max_tokens=1200
        )
        recent = "\n".join(
            f"{'你' if row.get('is_bot') else row.get('sender_name')}: {row.get('text')}"
            for row in recent_rows
        )
        system, user = self.prompts.relevance_prompt(event.text, recent)
        try:
            answer = await self.llm.complete(
                system,
                user,
                temperature=0.0,
                max_tokens=8,
                purpose="relevance",
                scope_key=event.scope_key,
            )
        except LLMError:
            return False
        score = _parse_score(answer)
        threshold = self.config.trigger.proactive.relevance_threshold
        log.debug("主动插话相关度 %.2f（阈值 %.2f）", score, threshold)
        return score >= threshold

    # -- 生成回复 ----------------------------------------------------
    async def _resolve_tools(self, result, messages, event: evmod.MessageEvent):
        """模型要调工具就把结果喂回去，最多两轮，避免死循环。"""
        self.toolbox.current_scope = event.scope_key
        rounds = 0
        while getattr(result, "tool_calls", None) and rounds < 3:
            rounds += 1
            messages.append(
                {
                    "role": "assistant",
                    "content": result.text or "",
                    "tool_calls": result.tool_calls,
                }
            )
            for call in result.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                output = await self.toolbox.run(name, function.get("arguments"))
                log.info("工具 %s -> %s", name, output[:80])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "content": self.toolbox.clip(output),
                    }
                )
            result = await self.llm.chat(
                messages,
                purpose="chat",
                scope_key=event.scope_key,
                tools=self.toolbox.specs(),
            )
        return result

    # -- 流式回复 ----------------------------------------------------
    _FILLER_CHARS = streaming.FILLER_CHARS

    @classmethod
    def _visible_len(cls, text: str) -> int:
        return streaming.visible_len(text)

    @classmethod
    def _is_filler(cls, text: str) -> bool:
        return streaming.is_filler(text)

    @staticmethod
    def _strip_leading_filler(text: str) -> str:
        return streaming.strip_leading_filler(text)

    @staticmethod
    def _find_cut(buffer: str, min_chars: int, max_buffer: int) -> int:
        return streaming.find_cut(buffer, min_chars, max_buffer)

    async def _emit_piece(
        self,
        event: evmod.MessageEvent,
        piece: str,
        reply_to: int | None,
        reason: str,
    ) -> tuple[str, bool]:
        parsed = self._apply_random_tags(
            parse_reply(piece, self.emojis.mapping), event
        )
        if parsed.text:
            await self.sender.send_chunk(
                event.scope_type,
                event.scope_id,
                parsed.text,
                reply_to=reply_to,
                reason=reason,
            )
        if parsed.images:
            await self.sender.send_images(
                event.scope_type, event.scope_id, parsed.images, reason=reason
            )
        if parsed.voice:
            await self._send_voice_value(event, parsed.voice)
        return parsed.text, bool(parsed.images)

    async def _send_voice_value(self, event: evmod.MessageEvent, value: str) -> None:
        """语音包里有这个类别就发预录的，没有就把这段文字实时合成。"""
        value = (value or "").strip()
        if not value:
            return
        if self.voice.pack.enabled:
            kind = self.VOICE_KIND_ALIASES.get(value, value)
            if kind in self.voice.pack.kinds():
                line = self.voice.pack.pick(kind, self.rng)
                if line and line.path:
                    try:
                        await self.sender.send_record(
                            event.scope_type, event.scope_id, line.path, reason="voice-tag"
                        )
                    except Exception:
                        log.debug("语音发送失败", exc_info=True)
                    return
        # 实时合成可能要几秒，别卡着文字回复，后台生成完再发
        task = asyncio.create_task(self._tts_and_send(event, value))
        self._voice_tasks.add(task)
        task.add_done_callback(self._voice_tasks.discard)

    async def _tts_and_send(self, event: evmod.MessageEvent, text: str) -> None:
        try:
            path = await self.voice.synthesize(text[:80])
            if not path:
                return
            await self.sender.send_record(
                event.scope_type, event.scope_id, path, reason="tts"
            )
        except Exception:
            log.debug("TTS 发送失败", exc_info=True)

    async def _stream_reply(
        self,
        event: evmod.MessageEvent,
        messages: list[dict],
        *,
        proactive: bool,
        reason: str,
    ) -> bool:
        """流式回复：过滤纯标点碎片、最多两条、慢响应先给信号。

        返回 True 表示已处理完；False 表示没发出任何东西，可以退回非流式。
        """
        settings = self.config.llm
        min_visible = max(1, int(getattr(settings, "stream_min_visible_chars", 10)))
        max_messages = max(1, int(getattr(settings, "stream_max_messages", 2)))
        ack_mode = str(getattr(settings, "stream_ack_mode", "reaction") or "off").lower()
        first_token_timeout = float(
            getattr(settings, "stream_first_token_timeout_sec", 15.0) or 15.0
        )
        ack_after = max(0.5, float(getattr(settings, "stream_ack_after_sec", 7.0) or 7.0))
        reply_to = None if proactive else (event.message_id or None)

        buffer = ""
        collected: list[str] = []
        first = True
        sent_any = False
        sent_image = False
        ack_sent = False
        ack_id: int | None = None
        reacted = False
        first_delta_at = 0.0
        started_at = time.monotonic()

        queue: asyncio.Queue = asyncio.Queue()

        async def pump() -> None:
            try:
                async for item in self.llm.stream_chat(
                    messages,
                    purpose="proactive" if proactive else "chat",
                    scope_key=event.scope_key,
                    tools=self.toolbox.specs(),
                ):
                    await queue.put(item)
            except LLMError as exc:
                await queue.put({"type": "error", "error": str(exc)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await queue.put({"type": "error", "error": f"流式异常: {exc}"})
            finally:
                await queue.put({"type": "end"})

        pump_task = asyncio.create_task(pump())

        async def first_item() -> dict:
            nonlocal ack_sent, ack_id, reacted
            started = time.monotonic()
            try:
                return await asyncio.wait_for(queue.get(), timeout=ack_after)
            except asyncio.TimeoutError:
                pass
            if ack_mode == "text" and not ack_sent:
                try:
                    ack_id = await self.sender.send_ack(
                        event.scope_type,
                        event.scope_id,
                        self.rng.choice(
                            list(getattr(settings, "stream_ack_texts", []) or ["等下，我想想"])
                        ),
                        reply_to=reply_to,
                    )
                    ack_sent = True
                except Exception:
                    log.debug("发送思考提示失败", exc_info=True)
            elif (
                ack_mode == "reaction"
                and event.message_id
                and hasattr(self.sender, "react")
            ):
                try:
                    reacted = bool(
                        await self.sender.react(
                            event.message_id,
                            str(getattr(settings, "stream_ack_emoji_id", "128531")),
                        )
                    )
                except Exception:
                    log.debug("表情回应失败", exc_info=True)
            remaining = max(0.2, first_token_timeout - (time.monotonic() - started))
            try:
                return await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutError("first token timeout") from exc

        try:
            try:
                pending: list[dict] = [await first_item()]
            except TimeoutError:
                log.warning(
                    "首 token 超时 %.0fs，走兜底 %s",
                    first_token_timeout,
                    event.scope_key,
                )
                if not proactive and reason in ("at_me", "reply_to_me", "private"):
                    await self._send_fallback(event)
                return True
            except LLMError as exc:
                log.warning("流式请求失败，退回非流式 %s: %s", event.scope_key, exc)
                return False

            while True:
                item = pending.pop(0) if pending else await queue.get()
                kind = item.get("type")
                if kind == "end":
                    break
                if kind == "error":
                    raise LLMError(str(item.get("error") or "流式请求失败"))
                if kind != "delta":
                    if kind == "done" and item.get("result") is not None:
                        result = item["result"]
                        if result.tool_calls and not sent_any:
                            log.info(
                                "流式阶段请求工具，退回非流式处理 %s", event.scope_key
                            )
                            return False
                        if result.finish_reason == "length" and not result.text.strip():
                            log.warning("流式输出被截断且为空 %s", event.scope_key)
                    continue

                if not first_delta_at:
                    first_delta_at = time.monotonic()
                    elapsed = first_delta_at - started_at
                    log.info("首 token %.2fs %s", elapsed, event.scope_key)
                    slow_note = float(
                        getattr(settings, "stream_slow_note_sec", 0) or 0
                    )
                    if slow_note > 0 and elapsed >= slow_note:
                        if ack_sent and ack_id:
                            try:
                                await self.sender.recall(ack_id)
                            except Exception:
                                pass
                            ack_sent, ack_id = False, None
                        template = str(
                            getattr(settings, "stream_slow_note_template", "想了 {sec} 秒…")
                            or "想了 {sec} 秒…"
                        )
                        note = template.replace("{sec}", str(int(elapsed)))
                        try:
                            await self.sender.send_ack(
                                event.scope_type,
                                event.scope_id,
                                note,
                                reason="slow-note",
                            )
                        except Exception:
                            log.debug("慢响应提示发送失败", exc_info=True)
                buffer += str(item.get("text") or "")
                while True:
                    cut = self._find_cut(
                        buffer, settings.stream_min_chars, settings.stream_max_buffer
                    )
                    if cut <= 0:
                        break
                    head = buffer[:cut]
                    if self._is_filler(head) and len(buffer) < settings.stream_max_buffer * 2:
                        break
                    if self._visible_len(head) < min_visible:
                        break
                    if len(collected) >= max_messages - 1:
                        break
                    piece, buffer = head, buffer[cut:]
                    if ack_sent and ack_id:
                        try:
                            await self.sender.recall(ack_id)
                        except Exception:
                            log.debug("撤回思考提示失败", exc_info=True)
                        ack_sent, ack_id = False, None
                    text, had_image = await self._emit_piece(
                        event, piece, reply_to if first else None, reason
                    )
                    collected.append(text)
                    sent_image = sent_image or had_image
                    first = False
                    sent_any = True
                    gap = getattr(settings, "stream_chunk_gap_ms", (0, 0)) or (0, 0)
                    if gap[1] > 0 and len(collected) < max_messages:
                        await asyncio.sleep(
                            self.rng.randint(int(gap[0]), int(gap[1])) / 1000.0
                        )
        except LLMError as exc:
            if sent_any:
                log.warning("流式输出中断 %s: %s", event.scope_key, exc)
            else:
                log.warning("流式请求失败，退回非流式 %s: %s", event.scope_key, exc)
                return False
        finally:
            pump_task.cancel()
            if reacted and event.message_id and hasattr(self.sender, "react"):
                try:
                    await self.sender.react(
                        event.message_id,
                        str(getattr(settings, "stream_ack_emoji_id", "128531")),
                        set_like=False,
                    )
                except Exception:
                    log.debug("移除表情回应失败", exc_info=True)

        remaining = self._strip_leading_filler(buffer)
        if remaining and (sent_any is False or len(collected) < max_messages):
            if sent_any is False or self._visible_len(remaining) >= min_visible or not collected:
                if ack_sent and ack_id:
                    try:
                        await self.sender.recall(ack_id)
                    except Exception:
                        log.debug("撤回思考提示失败", exc_info=True)
                    ack_sent, ack_id = False, None
                text, had_image = await self._emit_piece(
                    event, remaining, reply_to if first else None, reason
                )
                collected.append(text)
                sent_image = sent_image or had_image
                sent_any = True
        if ack_sent and ack_id:
            try:
                await self.sender.recall(ack_id)
            except Exception:
                log.debug("撤回思考提示失败", exc_info=True)
            ack_sent, ack_id = False, None

        full_text = "".join(collected).strip()
        if not sent_any or not full_text:
            self.stats["ignored"] += 1
            return True

        self.store.add_message(
            event.scope_type,
            event.scope_id,
            sender_qq=self.client.self_id,
            sender_name=self.config.bot.name,
            text=full_text,
            is_bot=True,
        )
        self.limiter.note_send(
            event.scope_type, event.scope_id, event.user_id, full_text
        )
        if proactive and event.is_group:
            self.limiter.note_proactive(event.scope_id)
        self.stats["replies"] += 1
        if proactive:
            self.stats["proactive"] += 1
        if event.message_id:
            self.store.mark_replied(event.message_id)
        await self._maybe_auto_emoji(event, sent_image)
        return True

    async def _respond(
        self, event: evmod.MessageEvent, *, proactive: bool, reason: str
    ) -> None:
        lock = self._locks.setdefault(event.scope_key, asyncio.Lock())
        async with lock:
            image_cards = await self._describe_images(event)
            extra: list[dict] = []
            if self.semantic is not None and getattr(self.semantic, "enabled", False):
                try:
                    extra = await self.semantic.search(
                        event.scope_type, event.scope_id, event.text, limit=3
                    )
                except Exception:
                    log.debug("语义检索失败", exc_info=True)
            ctx = self._build_context(
                event, proactive, "", extra, image_cards=image_cards
            )
            messages = self.prompts.build(ctx)

            if getattr(self.config.llm, "stream", False) and hasattr(self.llm, "stream_chat"):
                try:
                    handled = await self._stream_reply(
                        event, messages, proactive=proactive, reason=reason
                    )
                except Exception:
                    log.warning("流式处理异常，退回非流式", exc_info=True)
                    handled = False
                if handled:
                    return

            try:
                result = await self.llm.chat(
                    messages,
                    purpose="proactive" if proactive else "chat",
                    scope_key=event.scope_key,
                    tools=self.toolbox.specs(),
                )
                result = await self._resolve_tools(result, messages, event)
            except LLMError as exc:
                self.stats["llm_errors"] += 1
                log.warning("模型调用失败 %s: %s", event.scope_key, exc)
                if not proactive and reason in ("at_me", "reply_to_me", "private"):
                    await self._send_fallback(event)
                return

            parsed = parse_reply(result.text, self.emojis.mapping)
            if parsed.empty:
                log.info("模型空回复，跳过 %s", event.scope_key)
                self.stats["ignored"] += 1
                return

            reply_to = None if proactive else (event.message_id or None)
            try:
                await self.sender.send_text(
                    event.scope_type,
                    event.scope_id,
                    parsed.text,
                    reply_to=reply_to,
                    reason=reason,
                )
                if parsed.images:
                    await self.sender.send_images(
                        event.scope_type, event.scope_id, parsed.images, reason=reason
                    )
                if parsed.voice:
                    await self._send_voice_value(event, parsed.voice)
            except Exception as exc:
                log.warning("发送失败 %s: %s", event.scope_key, exc)
                return

            text_for_store = parsed.text or "[图片]"
            self.store.add_message(
                event.scope_type,
                event.scope_id,
                sender_qq=self.client.self_id,
                sender_name=self.config.bot.name,
                text=text_for_store,
                is_bot=True,
            )
            self.limiter.note_send(
                event.scope_type, event.scope_id, event.user_id, text_for_store
            )
            if proactive and event.is_group:
                self.limiter.note_proactive(event.scope_id)
            self.stats["replies"] += 1
            if proactive:
                self.stats["proactive"] += 1
            if event.message_id:
                self.store.mark_replied(event.message_id)
            await self._maybe_auto_emoji(event, bool(parsed.images))

    async def _transcribe_voice(self, event: evmod.MessageEvent) -> str:
        """收到语音消息时，用外部命令转成文字。"""
        for item in event.segments[:2]:
            if seg.seg_type(item) != "record":
                continue
            file = str(seg.seg_data(item).get("file") or "")
            if not file:
                continue
            try:
                data = await self.client.get_record(file)
            except Exception as exc:
                log.debug("取语音文件失败: %s", exc)
                continue
            path = ""
            if isinstance(data, dict):
                path = str(data.get("file") or data.get("path") or "")
            if not path:
                continue
            text = await self.voice.transcribe(path)
            if text:
                log.info("语音转文字：%s", text[:60])
                return text
        return ""

    async def _describe_images(self, event: evmod.MessageEvent) -> list[str]:
        """把消息里的图变成图片卡文本；失败就当作没图，不阻塞回复。"""
        if not self.config.tools.vision:
            return []
        describe = getattr(self.llm, "chat", None)
        if not callable(describe):
            return []
        limit = max(1, int(getattr(self.config.tools, "vision_max_images", 2) or 2))
        timeout = float(getattr(self.config.tools, "vision_timeout_sec", 15.0) or 15.0)
        files = seg.image_files(event.segments)[:limit]
        if not files:
            return []

        async def one(file: str) -> str | None:
            loaded = await load_image_bytes(self.client, file, timeout=timeout)
            if not loaded:
                return None
            data, mime = loaded
            self._last_image.setdefault(event.scope_key, (data, mime))
            digest = hashlib.sha256(data).hexdigest()
            cached = self.store.get_image_card(digest)
            if cached:
                card = ImageCard.from_dict(cached)
            else:
                ocr_hint = ""
                if getattr(self.config.tools, "vision_ocr", True):
                    ocr = getattr(self.client, "ocr_image", None)
                    if callable(ocr):
                        try:
                            result = await ocr(file)
                            if isinstance(result, dict):
                                ocr_hint = str(
                                    result.get("text")
                                    or result.get("ocr")
                                    or " ".join(result.get("texts") or [])
                                )
                            elif isinstance(result, str):
                                ocr_hint = result
                        except Exception:
                            log.debug("NapCat OCR 不可用", exc_info=True)
                card = await analyze_image(
                    self.llm,
                    data,
                    mime,
                    scope_key=event.scope_key,
                    ocr_hint=ocr_hint,
                )
                if card is None:
                    return None
                try:
                    self.store.upsert_image_card(
                        digest,
                        card.to_dict(),
                        scope_type=event.scope_type,
                        scope_id=event.scope_id,
                    )
                except Exception:
                    log.debug("写图片卡失败", exc_info=True)
            if not (card.summary or card.ocr or card.meme):
                return None
            return card.to_prompt()

        results = await asyncio.gather(
            *(one(file) for file in files), return_exceptions=True
        )
        return [item for item in results if isinstance(item, str) and item.strip()]

    async def _look_closer(self, question: str, region: str = "full") -> str:
        item = self._last_image.get(self.toolbox.current_scope or "")
        if not item:
            return "现在没有可看的图；等对方发一张我再看。"
        data, mime = item
        return await ask_image(
            self.llm,
            data,
            mime,
            question,
            region=region,
            scope_key=self.toolbox.current_scope,
        )

    def _emoji_names_for_prompt(self) -> list[str]:
        names = list(self.emojis.names())
        if emoji_inbox.latest(1):
            names.append("随机表情")
        return names

    def _build_context(
        self,
        event: evmod.MessageEvent,
        proactive: bool,
        image_note: str = "",
        extra_memories: list[dict] | None = None,
        *,
        image_cards: list[str] | None = None,
    ) -> PromptContext:
        scope_type, scope_id = event.scope_type, event.scope_id
        profile = self.store.get_profile(scope_type, scope_id, event.user_id)
        person = self.store.get_person(event.user_id) or {}
        members = (
            self.store.list_profiles(scope_type, scope_id, limit=8)
            if scope_type == "group"
            else []
        )
        memories = self.store.search_memories(
            scope_type,
            scope_id,
            event.text,
            subject_qq=event.user_id,
            limit=self.config.memory.retrieve_top_k,
        )
        if extra_memories:
            seen_ids = {row.get("id") for row in memories}
            for row in extra_memories:
                if row.get("id") not in seen_ids:
                    memories.append(row)
            memories = memories[: self.config.memory.retrieve_top_k]
        memes: list[str] = []
        if scope_type == "group":
            memes = [
                str(row.get("content") or "")
                for row in self.store.list_memories(scope_type, scope_id, kind="meme", limit=6)
            ]
        open_loops = self.store.list_open_loops(
            scope_type, scope_id, subject_qq=event.user_id, limit=5
        )
        session = ChatSession.load(
            self.store,
            scope_type,
            scope_id,
            subject_qq=event.user_id,
            persona_dir=self.prompts.persona_dir,
        )
        episodes = session.episodes
        relationship_events = session.relationship_events
        summaries = self.store.latest_summaries(scope_type, scope_id, limit=1)
        summary = summaries[0]["content"] if summaries else ""
        history = self.store.recent_messages(
            scope_type,
            scope_id,
            limit=self.config.memory.window_messages,
            max_tokens=self.config.memory.window_tokens,
        )
        if history and history[-1].get("message_id") == event.message_id:
            history = history[:-1]
        elif history and time.time() - int(history[-1].get("ts") or 0) < 3:
            history = history[:-1]

        quoted_text = ""
        if event.reply_id:
            row = self.store.get_message_by_id(event.reply_id)
            if row:
                quoted_text = str(row.get("text") or "")

        title = ""
        if scope_type == "group":
            title = self.group_titles.get(scope_id) or (
                (self.store.get_chat("group", scope_id) or {}).get("title") or ""
            )

        return PromptContext(
            scope_type=scope_type,
            scope_id=scope_id,
            scope_title=title,
            sender_qq=event.user_id,
            sender_name=event.sender_name,
            sender_profile=profile,
            mood=self.mood.describe(),
            person_note=str(person.get("global_note") or ""),
            members=members,
            memories=memories,
            memes=memes,
            open_loops=open_loops,
            episodes=episodes,
            relationship_events=relationship_events,
            summary=summary,
            history=history,
            current_text=(
                f"{event.text}（对方发了图：{image_note}）" if image_note else event.text
            ),
            image_cards=list(image_cards or []),
            is_at=event.self_id in event.at_ids,
            is_proactive=proactive,
            quoted_text=quoted_text,
            now=now_local(),
            bot_name=self.config.bot.name,
            emoji_names=self._emoji_names_for_prompt(),
            voice_kinds=[self.VOICE_KIND_LABELS.get(k, k) for k in self.voice.pack.kinds()],
        )

    async def _send_fallback(self, event: evmod.MessageEvent) -> None:
        text = self.rng.choice(FALLBACKS)
        try:
            await self.sender.send_text(
                event.scope_type,
                event.scope_id,
                text,
                reply_to=event.message_id or None,
                reason="fallback",
            )
            self.store.add_message(
                event.scope_type,
                event.scope_id,
                sender_qq=self.client.self_id,
                sender_name=self.config.bot.name,
                text=text,
                is_bot=True,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------
    async def handle_notice(self, event: evmod.NoticeEvent) -> None:
        if event.kind in ("group_recall", "friend_recall"):
            if event.message_id:
                self.store.mark_recalled(event.message_id)
            return

        if event.kind == "poke":
            await self._handle_poke(event)
            return

        if event.kind == "group_increase":
            await self._handle_welcome(event)
            return

        if event.kind in ("group_decrease", "group_admin", "friend_add"):
            log.info("通知 %s %s:%s", event.kind, event.scope_type, event.scope_id)

    async def _handle_poke(self, event: evmod.NoticeEvent) -> None:
        if event.target_id != (event.self_id or self.client.self_id):
            return
        if event.is_group and not self.config.access.group_ok(event.scope_id):
            return
        if not event.is_group and not self.config.access.friend_ok(event.user_id):
            return
        if self._muted(event.scope_key):
            return
        if self.config.passive or self.config.readonly:
            return
        self.mood.note_poke()
        verdict = self.limiter.check_group_send(event.scope_id)
        if not verdict:
            return

        # 摸头语音：命中概率且有 pat 语音就发语音，否则发文字。
        line = None
        if self.voice.pack.enabled and self.rng.random() < self.config.tools.poke_voice_probability:
            line = self.voice.pack.pick("pat", self.rng)
        text = self.rng.choice(POKE_REPLIES)
        sent_as_voice = False
        try:
            if line is not None:
                try:
                    await self.sender.send_record(
                        event.scope_type,
                        event.scope_id,
                        line.path,
                        reason="poke-voice",
                    )
                    text = line.text or text
                    sent_as_voice = True
                except Exception as exc:
                    log.debug("摸头语音发送失败，改用文字：%s", exc)
            if not sent_as_voice:
                await self.sender.send_text(
                    event.scope_type,
                    event.scope_id,
                    text,
                    quote=False,
                    reason="poke",
                )
            self.limiter.note_send(
                event.scope_type, event.scope_id, event.user_id, text
            )
            self.store.add_message(
                event.scope_type,
                event.scope_id,
                sender_qq=self.client.self_id,
                sender_name=self.config.bot.name,
                text=text,
                is_bot=True,
            )
        except Exception:
            pass

    async def _handle_welcome(self, event: evmod.NoticeEvent) -> None:
        if not event.is_group or not self.config.access.group_ok(event.scope_id):
            return
        if self.config.passive or self.config.readonly:
            return
        name = ""
        try:
            info = await self.client.get_group_member_info(event.scope_id, event.user_id)
            if isinstance(info, dict):
                name = str(info.get("card") or info.get("nickname") or "")
        except Exception:
            pass
        text = f"欢迎{'「' + name + '」' if name else '新人'}～"
        try:
            await self.sender.send_text(
                event.scope_type, event.scope_id, text, quote=False, reason="welcome"
            )
        except Exception:
            pass


def command_text(event: evmod.MessageEvent) -> str:
    """去掉 @ 和引用之后剩下的纯文本，用来识别 /命令。"""
    parts = []
    for item in event.segments:
        kind = seg.seg_type(item)
        if kind in ("reply", "at"):
            continue
        if kind == "text":
            parts.append(str(seg.seg_data(item).get("text", "")))
    return "".join(parts).strip()


def _parse_score(text: str) -> float:
    import re

    match = re.search(r"(\d+(?:\.\d+)?)", str(text or ""))
    if not match:
        return 0.0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0.0
    if value > 1.0:
        value = value / 10.0 if value <= 10 else 1.0
    return max(0.0, min(1.0, value))
