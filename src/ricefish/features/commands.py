"""聊天里的斜杠命令。

约定：命令永远优先于普通聊天处理；管理类命令只认 admin。
"""

from __future__ import annotations

import logging
import random
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from ..config import Config
from ..core.ratelimit import RateLimiter
from ..core.sender import Sender
from ..features import emoji_inbox
from ..features.tools import Tools
from ..llm.client import DeepSeekClient, LLMError
from ..llm.prompt import PromptBuilder
from ..llm.tags import strip_tags
from ..memory.store import MemoryStore
from ..util.text import fmt_time, parse_ts, truncate

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger("ricefish.commands")

COMMAND_PREFIXES = ("/", "／", "!", "！")


def _download_bytes(url: str, timeout: float = 15.0) -> bytes | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
        return data or None
    except Exception:  # noqa: BLE001
        log.debug("图片下载失败 %s", url)
        return None


@dataclass
class CommandContext:
    config: Config
    store: MemoryStore
    sender: Sender
    llm: DeepSeekClient
    prompts: PromptBuilder
    limiter: RateLimiter
    scope_type: str
    scope_id: int
    user_qq: int
    user_name: str
    args: str
    raw: str
    #: 当前消息里的图片 file id（/收表情 用）
    image_files: list[str] = field(default_factory=list)
    #: EmojiBook 实例，命令里收图时直接改它
    emojis: object | None = None
    #: OneBot 客户端，用来 get_image
    client: object | None = None

    @property
    def scope_key(self) -> str:
        return f"{self.scope_type}:{self.scope_id}"

    @property
    def is_admin(self) -> bool:
        return self.config.bot.is_admin(self.user_qq)

    @property
    def is_group(self) -> bool:
        return self.scope_type == "group"


HELP_TEXT = """可用指令：
/帮助              看这个
/我                看你在机器人眼里的档案
/记忆 [关键词]      查机器人记住了什么
/忘掉 <关键词>      让机器人忘掉相关记忆
/总结 [条数]        总结最近聊天（默认 30 条）
/梗                看这个群的梗词典
/天气 [城市]        查天气
/抽签              今日运势
——— 小游戏 ———
/猜词              猜一个词（直接发答案）
/21点              一局 21 点（/要 抽牌，/停牌 停）
/汤                海龟汤（我只答 是/不是/无关，/揭晓 看答案）
/不玩了            结束当前游戏
——— 梗图 ———
/梗图              随机发一张社区梗图
/梗图 010          按编号发（/梗图列表 看编号）
/梗图列表           看梗图包里的编号和台词
——— 语音 ———
/语音              随机播一句语音包
/语音 摸头          播一句摸头语音（撒娇/完成/难过…也行）
/语音列表           看语音包里有哪些台词
/语音 <文本>        用 TTS 念这句话（需配 tts_command）
——— 管理 ———
/开关              打开或关闭本会话
/主动 [开|关|状态]   打开/关闭本会话的主动陪伴
/主动 全局 开|关     打开/关闭所有私聊的主动陪伴（管理员）
/静音 [分钟]        暂时闭嘴（默认 60 分钟）
/名片 <QQ> <称呼>   给某人改称呼
/叫我 <称呼>        给自己设个称呼，以后直接用
/设定 <内容>        让她记住一条「你希望她有的设定」
/学习 <链接或资料>   看一遍、提炼成记忆，下次能想起来
/收表情 <名字>      发图时带上，把这张图收进表情库
/人设              看当前人设文件路径
/重置 <QQ>          清掉某人的记忆和档案
/周报 [天数]        这一段时间的群活跃报告"""


def is_command(text: str) -> bool:
    text = (text or "").strip()
    return bool(text) and text.startswith(COMMAND_PREFIXES)


def split_command(text: str) -> tuple[str, str]:
    text = (text or "").strip().lstrip("".join(COMMAND_PREFIXES))
    parts = text.split(None, 1)
    name = parts[0].strip().lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args


class CommandHandler:
    def __init__(self) -> None:
        self._last_summary: dict[str, float] = {}
        self._last_meme: dict[str, float] = {}

    async def handle(self, text: str, ctx: CommandContext) -> bool:
        name, args = split_command(text)
        if not name:
            return False
        handler = self._table().get(name)
        if handler is None:
            for alias, target in _ALIASES.items():
                if name == alias:
                    handler = self._table().get(target)
                    break
        if handler is None:
            return False
        try:
            reply = await handler(ctx, args)
        except Exception:
            log.exception("命令执行失败: %s", name)
            reply = "这条命令出错了，看看日志。"
        if reply:
            await ctx.sender.send_text(
                ctx.scope_type, ctx.scope_id, reply, quote=False, reason=f"cmd:{name}"
            )
        ctx.store.audit(ctx.user_name or str(ctx.user_qq), f"cmd:{name}", args)
        return True

    # -- 命令表 ------------------------------------------------------
    def _table(self) -> dict[str, Callable[[CommandContext, str], Awaitable[str | None]]]:
        return {
            "help": self.cmd_help,
            "帮助": self.cmd_help,
            "我": self.cmd_me,
            "me": self.cmd_me,
            "记忆": self.cmd_memory,
            "memory": self.cmd_memory,
            "忘掉": self.cmd_forget,
            "forget": self.cmd_forget,
            "总结": self.cmd_summary,
            "summary": self.cmd_summary,
            "梗": self.cmd_memes,
            "memes": self.cmd_memes,
            "天气": self.cmd_weather,
            "weather": self.cmd_weather,
            "抽签": self.cmd_fortune,
            "开关": self.cmd_toggle,
            "主动": self.cmd_proactive,
            "陪伴": self.cmd_proactive,
            "静音": self.cmd_mute,
            "名片": self.cmd_card,
            "叫我": self.cmd_call_me,
            "称呼": self.cmd_call_me,
            "设定": self.cmd_setting,
            "学习": self.cmd_learn,
            "收表情": self.cmd_collect_emoji,
            "收藏表情": self.cmd_collect_emoji,
            "人设": self.cmd_persona,
            "重置": self.cmd_reset,
            "周报": self.cmd_report,
            "report": self.cmd_report,
            "语音": self.cmd_voice,
            "voice": self.cmd_voice,
            "语音列表": self.cmd_voice_list,
            "voices": self.cmd_voice_list,
            "梗图": self.cmd_meme,
            "meme": self.cmd_meme,
            "梗图列表": self.cmd_meme_list,
            "memelist": self.cmd_meme_list,
        }

    # -- 普通命令 ----------------------------------------------------
    async def cmd_help(self, ctx: CommandContext, _: str) -> str:
        return HELP_TEXT

    async def cmd_me(self, ctx: CommandContext, _: str) -> str:
        profile = ctx.store.get_profile(ctx.scope_type, ctx.scope_id, ctx.user_qq)
        if not profile:
            return "我对你还没什么印象，多聊几句吧。"
        lines = [f"{profile.get('display_name') or ctx.user_name}："]
        if profile.get("tags"):
            lines.append(f"印象：{profile['tags']}")
        if profile.get("relation"):
            lines.append(f"关系：{profile['relation']}")
        if profile.get("notes"):
            lines.append(f"备注：{truncate(profile['notes'], 80)}")
        lines.append(f"好感度：{profile.get('affinity', 50)}/100（聊了 {profile.get('msg_count', 0)} 句）")
        return "\n".join(lines)

    async def cmd_memory(self, ctx: CommandContext, args: str) -> str:
        if args:
            memories = ctx.store.search_memories(
                ctx.scope_type, ctx.scope_id, args, subject_qq=ctx.user_qq, limit=6
            )
        else:
            memories = ctx.store.list_memories(
                ctx.scope_type, ctx.scope_id, limit=8
            )
        if not memories:
            return "没查到相关的记忆。"
        lines = ["我记得："]
        for memory in memories[:6]:
            lines.append(f"· {truncate(memory['content'], 60)}")
        return "\n".join(lines)

    async def cmd_forget(self, ctx: CommandContext, args: str) -> str:
        if not args:
            return "用法：/忘掉 关键词"
        removed = ctx.store.forget_by_keyword(ctx.scope_type, ctx.scope_id, args)
        if not removed:
            return f"没找到和「{args}」有关的记忆。"
        return f"忘掉了 {removed} 条和「{args}」有关的记忆。"

    async def cmd_summary(self, ctx: CommandContext, args: str) -> str | None:
        now = time.time()
        last = self._last_summary.get(ctx.scope_key, 0.0)
        if now - last < 60 and not ctx.is_admin:
            return "刚总结过，等一会儿再试。"
        try:
            count = max(5, min(200, int(args or 30)))
        except ValueError:
            count = 30
        messages = ctx.store.recent_messages(
            ctx.scope_type, ctx.scope_id, limit=count, max_tokens=8000
        )
        if len(messages) < 3:
            return "最近没什么可总结的。"
        history = "\n".join(
            f"{'你' if m.get('is_bot') else m.get('sender_name')}: {strip_tags(m.get('text') or '')}"
            for m in messages
        )
        system = "把聊天记录总结成 3 句话以内，中文，口语化，不要列表。"
        try:
            text = await ctx.llm.complete(
                system, history, temperature=0.4, max_tokens=300, purpose="summary"
            )
        except LLMError as exc:
            return f"总结失败：{exc}"
        self._last_summary[ctx.scope_key] = now
        return "刚才在聊：" + (text or "（没总结出来）")

    async def cmd_memes(self, ctx: CommandContext, args: str) -> str:
        memes = ctx.store.list_memories(
            ctx.scope_type, ctx.scope_id, kind="meme", keyword=args, limit=10
        )
        if not memes:
            return "这个群还没攒下什么梗。多聊聊，我会自己记。"
        return "这个群的梗：\n" + "\n".join(
            f"· {truncate(str(m['content']), 50)}" for m in memes
        )

    async def cmd_meme(self, ctx: CommandContext, args: str) -> str | None:
        from .memes import MemeBook

        book = MemeBook.load(ctx.config.tools.meme_pack)
        keyword = args.strip().lstrip("#")
        if keyword.lower() in ("列表", "list", "ls"):
            if not book.enabled:
                return "梗图包没加载。检查 config/bot.yaml 的 tools.meme_pack。"
            return "梗图包里这些：" + "\n" + "\n".join(f"· {line}" for line in book.listing())
        if not book.enabled:
            return "梗图包没加载。检查 config/bot.yaml 的 tools.meme_pack。"

        now = time.time()
        cooldown = int(ctx.config.tools.meme_cooldown_sec)
        last = self._last_meme.get(ctx.scope_key, 0.0)
        if cooldown and now - last < cooldown:
            wait = int(cooldown - (now - last)) + 1
            return f"刚发过梗图，等 {wait} 秒再来。"

        meme = book.pick(keyword) if keyword else book.pick()
        if meme is None:
            return "没有这张梗图。发 /梗图列表 看编号。"
        try:
            await ctx.sender.send_images(
                ctx.scope_type, ctx.scope_id, [meme.path], reason="meme"
            )
        except Exception as exc:
            return f"梗图没发出去：{exc}"
        self._last_meme[ctx.scope_key] = now
        return None

    async def cmd_meme_list(self, ctx: CommandContext, _: str) -> str | None:
        return await self.cmd_meme(ctx, "列表")

    async def cmd_weather(self, ctx: CommandContext, args: str) -> str:
        from .tools import Tools

        return await Tools(ctx.config).weather(args)

    async def cmd_voice(self, ctx: CommandContext, args: str) -> str | None:
        from .voice import Voice, VoicePack

        pack = VoicePack.load(ctx.config.tools.voice_pack)
        voice = Voice(ctx.config, pack)
        keyword = args.strip()
        kind_aliases = {
            "摸头": "pat",
            "pat": "pat",
            "贴贴": "pat",
            "待机": "idle",
            "idle": "idle",
            "撒娇": "coquetry",
            "coquetry": "coquetry",
            "干活": "running",
            "思考": "running",
            "running": "running",
            "完成": "celebrate",
            "庆祝": "celebrate",
            "celebrate": "celebrate",
            "难过": "sad",
            "出错": "sad",
            "sad": "sad",
            "进度": "todo",
            "todo": "todo",
            "欢迎": "greet",
            "greet": "greet",
            "批准": "approval",
            "approval": "approval",
            "随便": "",
            "来一句": "",
        }

        # 不带参数 / 指定 kind / 说「随便」：优先用本地语音包，不花 TTS 的钱
        if not keyword or keyword in kind_aliases:
            kind = kind_aliases.get(keyword, "")
            line = pack.pick(kind) if kind else (pack.pick("idle") or pack.pick())
            if line is not None:
                try:
                    await ctx.sender.send_record(
                        ctx.scope_type, ctx.scope_id, line.path, reason="voice-pack"
                    )
                except Exception as exc:
                    return f"语音没发出去：{exc}"
                return None
            if not keyword:
                if voice.tts_enabled:
                    return "用法：/语音 你想让我说的话"
                return "语音包没加载，TTS 也还没配。看看 tools.voice_pack / tools.tts_command。"
            return "语音包里没有这一类的台词。发 /语音列表 看看有什么。"

        if not voice.tts_enabled:
            return (
                "TTS 还没配。想听固定台词可以发 /语音 或 /语音 摸头；"
                "想让它念任意文字，先在 config/bot.yaml 里填 tools.tts_command。"
            )
        path = await voice.synthesize(keyword)
        if not path:
            return "这次没合成出来，等下再试。"
        try:
            await ctx.sender.send_record(
                ctx.scope_type, ctx.scope_id, path, reason="tts"
            )
        except Exception as exc:
            return f"语音发送失败：{exc}"
        return None

    async def cmd_voice_list(self, ctx: CommandContext, _: str) -> str:
        from .voice import VoicePack

        pack = VoicePack.load(ctx.config.tools.voice_pack)
        if not pack.enabled:
            return "语音包没加载。检查 config/bot.yaml 的 tools.voice_pack。"
        kinds = "、".join(pack.kinds())
        lines = [f"语音包：{len(pack.lines)} 句（{kinds}）"]
        for line in pack.lines[:20]:
            lines.append(f"· [{line.kind}] {truncate(line.text, 30)}")
        if len(pack.lines) > 20:
            lines.append(f"……还有 {len(pack.lines) - 20} 句")
        lines.append("发 /语音 随机播一句，/语音 摸头 只播摸头。")
        return "\n".join(lines)

    async def cmd_report(self, ctx: CommandContext, args: str) -> str:
        if not ctx.is_admin:
            return "这个只有主人能用。"
        from .report import WeeklyReport

        try:
            days = max(1, min(30, int(args or 7)))
        except ValueError:
            days = 7
        _, body = WeeklyReport(ctx.config, ctx.store, ctx.sender).build(days)
        return body

    async def cmd_fortune(self, ctx: CommandContext, _: str) -> str:
        rng = random.Random(f"{ctx.user_qq}-{time.strftime('%Y%m%d')}")
        level = rng.choice(["大吉", "中吉", "小吉", "末吉", "凶"])
        advice = rng.choice(
            [
                "适合主动找人聊天",
                "今天别熬夜",
                "会有意外的小惊喜",
                "少喝点奶茶",
                "适合把拖了很久的事做完",
                "有人偷偷在想你",
            ]
        )
        return f"今日运势：{level}。{advice}。"

    # -- 管理命令 ----------------------------------------------------
    async def cmd_toggle(self, ctx: CommandContext, _: str) -> str:
        if not ctx.is_admin:
            return "这个只有主人能用。"
        chat = ctx.store.get_chat(ctx.scope_type, ctx.scope_id) or {}
        enabled = 0 if chat.get("enabled", 1) else 1
        ctx.store.upsert_chat(ctx.scope_type, ctx.scope_id, enabled=enabled)
        return "开了。" if enabled else "关掉了，我先潜水。"

    async def cmd_proactive(self, ctx: CommandContext, args: str) -> str:
        """管理员开关：本会话 / 全局的主动陪伴。"""
        if not ctx.is_admin:
            return "这个只有主人能用。"
        arg = (args or "").strip()
        normalized = arg.lower()

        if normalized.startswith("全局") or normalized.startswith("global"):
            sub = normalized.replace("global", "全局", 1)[2:].strip()
            if sub in ("状态", "status"):
                return f"全局私聊主动陪伴：{'开' if ctx.config.companion.enabled else '关'}"
            current = bool(ctx.config.companion.enabled)
            if sub in ("开", "on", "1", "打开"):
                new = True
            elif sub in ("关", "off", "0", "关闭"):
                new = False
            elif not sub:
                new = not current
            else:
                return "用法：/主动 全局 开|关，或 /主动 全局 状态"
            ctx.config.companion.enabled = new
            ctx.store.set_setting("companion_enabled", "1" if new else "0")
            return f"全局私聊主动陪伴{'开了' if new else '关了'}。"

        if normalized in ("状态", "status"):
            chat = ctx.store.get_chat(ctx.scope_type, ctx.scope_id) or {}
            current = bool(chat.get("proactive_enabled", 1))
            return (
                f"本会话主动陪伴：{'开' if current else '关'}；"
                f"全局：{'开' if ctx.config.companion.enabled else '关'}"
            )

        chat = ctx.store.get_chat(ctx.scope_type, ctx.scope_id) or {}
        current = bool(chat.get("proactive_enabled", 1))
        if normalized in ("开", "on", "1", "打开"):
            new = True
        elif normalized in ("关", "off", "0", "关闭"):
            new = False
        elif not normalized:
            new = not current
        else:
            return "用法：/主动 [开|关|状态]，或 /主动 全局 开|关"
        ctx.store.upsert_chat(
            ctx.scope_type, ctx.scope_id, proactive_enabled=1 if new else 0
        )
        return "本会话的主动陪伴开了。" if new else "本会话的主动陪伴关了。"

    async def cmd_mute(self, ctx: CommandContext, args: str) -> str:
        if not ctx.is_admin:
            return "这个只有主人能用。"
        try:
            minutes = max(1, min(1440, int(args or 60)))
        except ValueError:
            minutes = 60
        until = int(time.time()) + minutes * 60
        ctx.store.set_state(f"mute:{ctx.scope_key}", until)
        return f"好，我安静 {minutes} 分钟。"

    async def cmd_card(self, ctx: CommandContext, args: str) -> str:
        if not ctx.is_admin:
            return "这个只有主人能用。"
        parts = args.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            return "用法：/名片 1234567 新称呼"
        qq, name = int(parts[0]), parts[1].strip()[:20]
        ctx.store.update_profile(
            ctx.scope_type, ctx.scope_id, qq, display_name=name, manual=True
        )
        return f"以后我就叫他「{name}」了。"

    async def cmd_call_me(self, ctx: CommandContext, args: str) -> str:
        """任何人都能给自己设一个称呼，记进档案，下次直接用。"""
        name = args.strip().strip("「」\"'“”")[:16]
        if not name:
            return "用法：/叫我 阿澈（下次我就这么叫你）"
        profile = ctx.store.get_profile(ctx.scope_type, ctx.scope_id, ctx.user_qq) or {}
        aliases = [
            item.strip()
            for item in str(profile.get("aliases") or "").split(",")
            if item.strip()
        ]
        if name not in aliases:
            aliases.append(name)
        ctx.store.update_profile(
            ctx.scope_type,
            ctx.scope_id,
            ctx.user_qq,
            display_name=name,
            aliases=",".join(aliases[-6:]),
            manual=True,
        )
        return f"行吧，以后叫你「{name}」。……别老改。"

    async def cmd_setting(self, ctx: CommandContext, args: str) -> str:
        """/设定 —— 让别人把「希望你有的设定」写进人设，立刻生效。"""
        text = args.strip().strip("「」\"'“”")
        if not text:
            return "用法：/设定 别人夸你的时候要先脸红一下"
        path = Path("config/personas/learned.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        if text in ("清空", "重置", "删除"):
            if not ctx.is_admin:
                return "只有主人能清空设定。"
            path.write_text("# 后来学到的设定\n", encoding="utf-8")
            return "学到的设定清空了。"
        if path.exists() and path.stat().st_size > 8000:
            return "设定太多了，让主人用 /设定 清空 一下再记。"
        text = text[:200]
        stamp = time.strftime("%m-%d")
        who = ctx.user_name or str(ctx.user_qq)
        header = "" if path.exists() and path.stat().st_size else "# 后来学到的设定\n\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(header)
            handle.write(f"- {text}（{who}，{stamp}）\n")
        return f"记下了：{text}"

    async def cmd_learn(self, ctx: CommandContext, args: str) -> str:
        """/学习 —— 给个链接或一段资料，提炼成记忆，下次能想起来。"""
        target = args.strip()
        if not target:
            return "用法：/学习 https://某个网页 或 /学习 一段资料"
        if target.startswith(("http://", "https://")):
            text = await Tools(ctx.config).read_url(target, limit=3000)
            if not text:
                return "这个链接打不开，或者里面没正文。"
            source = target
        else:
            text = target[:3000]
            source = "聊天"
        prompt = (
            "把下面的资料提炼成 3 条以内、每条一句话的事实，"
            "用「- 」开头，只写事实，不要客套和总结句。\n\n" + text
        )
        try:
            facts = await ctx.llm.complete(
                "你是一个资料整理助手，只输出条目。", prompt, max_tokens=400, purpose="learn"
            )
        except LLMError as exc:
            return f"看完了，但整理失败了：{exc}"
        stored = 0
        for line in (facts or "").splitlines():
            line = line.strip().lstrip("-·*• ").strip()
            if len(line) < 5:
                continue
            ctx.store.add_memory(
                ctx.scope_type,
                ctx.scope_id,
                line[:200],
                kind="knowledge",
                importance=4,
            )
            stored += 1
            if stored >= 5:
                break
        if not stored:
            return "看完了，但没提炼出能记的东西。"
        return f"看完了，记住了 {stored} 条（来源：{source[:40]}）"

    async def cmd_collect_emoji(self, ctx: CommandContext, args: str) -> str:
        """/收表情 —— 收当前消息里的图；没带图就从收件箱拿最近一张。"""
        book = ctx.emojis
        if book is None:
            return "表情库没接上，先别收了。"
        name = (args or "").strip() or f"表情{len(book) + 1}"
        temp = False
        if ctx.image_files:
            try:
                source = await self._fetch_image(ctx, ctx.image_files[0])
            except Exception as exc:  # noqa: BLE001
                log.debug("收表情失败", exc_info=True)
                return f"图没拿到：{exc}"
            temp = True
        else:
            recent = emoji_inbox.latest(1)
            source = emoji_inbox.get(recent[0]["id"]) if recent else None
            if not source:
                return "发图的时候带上 /收表情 名字；或者等别人发一张，再说 /收表情 名字"
        if not source:
            return "图没下下来，再发一次试试。"
        try:
            book.add(name, Path(source))
        except OSError as exc:
            return f"存图失败：{exc}"
        finally:
            if temp:
                try:
                    Path(source).unlink()
                except OSError:
                    pass
        return f"收下了，以后可以发「{book.safe_name(name)}」"

    @staticmethod
    async def _fetch_image(ctx: CommandContext, file_id: str) -> Path | None:
        """把图片落到临时文件，返回路径。"""
        import base64
        import asyncio

        suffix = ".png"
        for ext in (".jpg", ".jpeg", ".gif", ".webp", ".png"):
            if file_id.lower().endswith(ext):
                suffix = ext
                break
        tmpdir = Path(tempfile.mkdtemp(prefix="ricefish_emoji_"))
        target = tmpdir / ("img" + suffix)

        if file_id.startswith("base64://"):
            try:
                target.write_bytes(base64.b64decode(file_id[9:]))
                return target
            except Exception:  # noqa: BLE001
                return None

        url = file_id if file_id.startswith(("http://", "https://")) else ""
        local = "" if url else file_id
        if not url and ctx.client is not None:
            try:
                info = await ctx.client.get_image(file_id)
            except Exception:  # noqa: BLE001
                info = None
            if isinstance(info, dict):
                url = str(info.get("url") or "")
                local = str(info.get("file") or local)
        if url.startswith(("http://", "https://")):
            data = await asyncio.to_thread(_download_bytes, url)
            if data:
                target.write_bytes(data)
                return target
            return None
        if local and Path(local).exists():
            target.write_bytes(Path(local).read_bytes())
            return target
        return None

    async def cmd_persona(self, ctx: CommandContext, _: str) -> str:
        base = "config/personas"
        path = f"{base}/global.md"
        if ctx.is_group:
            path = f"{base}/groups/{ctx.scope_id}.md"
        return f"人设文件：{path}（改完立刻生效）"

    async def cmd_reset(self, ctx: CommandContext, args: str) -> str:
        if not ctx.is_admin:
            return "这个只有主人能用。"
        if not args.strip().isdigit():
            return "用法：/重置 QQ号"
        qq = int(args.strip())
        removed = ctx.store.purge_memories(ctx.scope_type, ctx.scope_id, subject_qq=qq)
        ctx.store.reset_profile(ctx.scope_type, ctx.scope_id, qq)
        return f"清掉了 {qq} 的 {removed} 条记忆和档案。"


_ALIASES = {
    "h": "help",
    "?": "help",
    "summary": "总结",
    "mem": "记忆",
    "who": "我",
    "profile": "我",
    "card": "名片",
    "mute": "静音",
    "toggle": "开关",
    "reset": "重置",
    "persona": "人设",
    "fortune": "抽签",
}
