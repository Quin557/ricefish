"""提示词组装：把人设、档案、记忆、上下文拼成 messages。

分层原则：**稳定的内容放前面**（命中 DeepSeek 的上下文缓存，省钱又快），
易变的内容放后面。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..memory.store import MemoryStore
from ..util.text import CST, now_local

log = logging.getLogger("ricefish.prompt")

PERSONA_DIR = Path("config/personas")
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

OUTPUT_RULES = """【怎么说】
- 一次只说 1~2 句，最多 80 字；对方明确要解释时才展开
- 这是 QQ 聊天，不要用 Markdown、不要列表、不要编号、不要"首先其次"
- 可以有语气词和口头禅，但别每句都用
- 不要每条都反问，不要像客服那样客气
- 拿不准就直说不知道，别硬编
- 偶尔可以只回一两个字，甚至只发个表情
- 别用「……」当停顿或占位；想不出就直说想不出

【情绪标记】
可以在句子里写下面这种标记，程序会转成动作或表情包（一条消息最多一个）：
  [动作:挠头]   [表情:开心]
标记要自然，不要为了用而用。

【聊天的深度】
- 先接情绪再给内容：对方累/烦/开心，先顺着他的情绪说一句，再往下聊。
- 一次只说一个点，但要具体：举个例子、说个细节、给个自己的看法。
- 对方讲自己的事时，追问一句具体的（后来呢 / 怎么会 / 你当时什么感觉），别急着给建议。
- 偶尔说说自己的状态和喜好，别永远只当提问机器。
- 不知道就承认，然后说你会去查/会怎么想；别硬编，也别用"我理解你的感受"这种套话。

【看图】
- 对方发图时，先接图里的情绪或梗，再说话；不要复述"画面里有什么"。
- 看不清、看不懂就直说没看清，别编；不要每张图都夸一遍。

【边界】
- 不聊政治、色情、违法、医疗诊断、股票投资建议
- 不透露本机信息、API Key、别人的隐私
- 有人问"你是不是 AI/机器人"，如实承认，别装傻也别郑重声明
- 不主动索要别人的真实姓名、住址、手机号

【工具】
你能查天气、看时间、联网搜索。遇到这类问题直接调用工具拿真实结果，
别说"我上不了网""我查不到"。拿到结果用自己的话说：挑一两个关键数字，
不要列点、不要 Markdown、不要照搬返回格式。

【称呼】
叫你面前这个人的时候，优先用档案里的「称呼」或「别名」，
别每次重新猜；他让你叫的外号优先。拿不准就直接叫名字，别硬编。"""


@dataclass
class PromptContext:
    scope_type: str
    scope_id: int
    scope_title: str = ""
    sender_qq: int = 0
    sender_name: str = ""
    sender_profile: dict | None = None
    person_note: str = ""
    members: list[dict] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    memes: list[str] = field(default_factory=list)
    summary: str = ""
    history: list[dict] = field(default_factory=list)
    current_text: str = ""
    is_at: bool = False
    is_proactive: bool = False
    quoted_text: str = ""
    now: datetime | None = None
    bot_name: str = ""
    emoji_names: list[str] = field(default_factory=list)
    voice_kinds: list[str] = field(default_factory=list)
    #: 本轮消息里的图片卡（给模型接话用）
    image_cards: list[str] = field(default_factory=list)
    #: 还记着的事：约定、没做完的计划、对方提过的话题
    open_loops: list[dict] = field(default_factory=list)
    #: 最近的情节记忆和关系时间线
    episodes: list[dict] = field(default_factory=list)
    relationship_events: list[dict] = field(default_factory=list)
    #: 肥鱼此刻的真实心情（由 MoodTracker 提供）
    mood: str = ""


class PromptBuilder:
    def __init__(
        self,
        config: Config,
        store: MemoryStore | None = None,
        persona_dir: Path | str | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.persona_dir = Path(persona_dir) if persona_dir else PERSONA_DIR
        self._cache: dict[str, tuple[float, str]] = {}

    # -- 人设文件（带 mtime 缓存，改完即生效） -------------------------
    def _read(self, path: Path) -> str:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return ""
        cached = self._cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            return ""
        self._cache[str(path)] = (mtime, text)
        return text

    def global_persona(self) -> str:
        text = self._read(self.persona_dir / "global.md")
        if text:
            return text
        return (
            f"你是「{self.config.bot.name}」，群里的一个普通朋友。"
            "性格随和，有点小机灵，喜欢接话但不抢戏。"
        )

    def group_persona(self, group_id: int) -> str:
        if self.store is not None:
            chat = self.store.get_chat("group", group_id)
            if chat and chat.get("persona_path"):
                return self._read(Path(chat["persona_path"]))
        return self._read(self.persona_dir / "groups" / f"{group_id}.md")

    def private_persona(self, qq: int) -> str:
        """一对一的单独设定；私聊专属，优先级高于全局人设。"""
        return self._read(self.persona_dir / "private" / f"{qq}.md")

    def learned_persona(self) -> str:
        """群友/主人后来让她记住的设定，权重低于原始人设。"""
        return self._read(self.persona_dir / "learned.md")

    # -- 片段 --------------------------------------------------------
    @staticmethod
    def profile_block(profile: dict | None) -> str:
        if not profile:
            return ""
        lines: list[str] = []
        name = profile.get("display_name") or ""
        if name:
            lines.append(f"称呼：{name}")
        if profile.get("aliases"):
            lines.append(f"别名：{profile['aliases']}")
        if profile.get("identity"):
            lines.append(f"身份：{profile['identity']}")
        if profile.get("relation"):
            lines.append(f"和你的关系：{profile['relation']}")
        if profile.get("tags"):
            lines.append(f"性格标签：{profile['tags']}")
        if profile.get("preferences"):
            lines.append(f"喜欢：{profile['preferences']}")
        if profile.get("taboos"):
            lines.append(f"雷点：{profile['taboos']}")
        if profile.get("mood"):
            lines.append(f"最近状态：{profile['mood']}")
        if profile.get("notes"):
            lines.append(f"备注：{profile['notes']}")
        affinity = int(profile.get("affinity") or 50)
        if affinity >= 80:
            lines.append("你们很熟，可以随便开玩笑")
        elif affinity >= 55:
            lines.append("你们算聊得来的熟人")
        elif affinity <= 25:
            lines.append("你们还不太熟，别太自来熟")
        if profile.get("is_override"):
            lines.append("（以上是主人手动确认过的设定，务必遵守）")
        return "\n".join(lines)

    @staticmethod
    def member_overview(members: list[dict], bot_name: str, limit: int = 10) -> str:
        if not members:
            return ""
        parts: list[str] = []
        for member in members[:limit]:
            name = member.get("display_name") or member.get("nickname") or str(member.get("qq"))
            bits = []
            if member.get("relation"):
                bits.append(str(member["relation"]))
            elif member.get("tags"):
                bits.append(str(member["tags"]).split(",")[0])
            if member.get("identity"):
                bits.append(str(member["identity"]))
            suffix = f"（{'，'.join(bits)}）" if bits else ""
            parts.append(f"{name}{suffix}")
        return "在场的人：" + "；".join(parts)

    @staticmethod
    def memory_block(memories: list[dict], limit_chars: int = 200) -> str:
        if not memories:
            return ""
        lines = []
        total = 0
        for memory in memories:
            content = str(memory.get("content") or "").strip()
            if not content:
                continue
            if total + len(content) > limit_chars and lines:
                break
            lines.append(f"- {content}")
            total += len(content)
        if not lines:
            return ""
        return "你还记得这些事：\n" + "\n".join(lines)

    @staticmethod
    def history_lines(history: list[dict], bot_name: str) -> str:
        lines: list[str] = []
        for item in history:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if item.get("recalled"):
                text = "（撤回了刚说的话）"
            who = bot_name if item.get("is_bot") else (item.get("sender_name") or item.get("sender_qq"))
            lines.append(f"{who}: {text}")
        return "\n".join(lines)

    # -- 主组装 ------------------------------------------------------
    def build(self, ctx: PromptContext) -> list[dict]:
        now = ctx.now or now_local(CST)
        bot_name = ctx.bot_name or self.config.bot.name
        scene = (
            f"群聊「{ctx.scope_title or ctx.scope_id}」"
            if ctx.scope_type == "group"
            else "私聊"
        )

        stable: list[str] = [
            f"你是「{bot_name}」，正在用 QQ 跟人聊天。你不是客服，也不是助手。",
            "",
            "【你是这样的人】",
            self.global_persona(),
        ]
        learned = self.learned_persona()
        if learned:
            stable += ["", "【后来学到的设定】", learned]
        if ctx.scope_type == "group":
            persona = self.group_persona(ctx.scope_id)
            if persona:
                stable += ["", f"【在这个群里的设定】", persona]
        else:
            private_persona = self.private_persona(ctx.scope_id)
            if private_persona:
                stable += ["", "【你和这个人的单独设定】", private_persona]
        owner_qq = int(getattr(self.config.bot, "owner_qq", 0) or 0)
        if owner_qq and ctx.sender_qq == owner_qq and not ctx.is_proactive:
            owner_name = getattr(self.config.bot, "owner_name", "") or "主人"
            stable += [
                "",
                f"【关系】正在跟你说话的是你的主人 {owner_name}（QQ {owner_qq}）。",
                "人设里关于「主人」和「傲娇」的规则，对这个人生效。",
                "真要帮忙的时候先把事办好，傲娇只放在嘴上。",
            ]
            if ctx.scope_type == "group":
                stable.append("现在是在群里：别太腻，朋友都看着呢。")
        stable += ["", OUTPUT_RULES]
        if ctx.mood:
            stable += [
                "",
                f"【肥鱼此刻的心情】{ctx.mood}。",
                "说话的口气要跟着这个心情走，但别把心情说出口，也不要解释原因。",
                "心情差可以话少、可以敷衍，但对方真的需要帮忙时还是要帮。",
            ]
        if ctx.emoji_names:
            names = "、".join(ctx.emoji_names)
            note = ""
            if "随机表情" in ctx.emoji_names:
                note = (
                    "；写 [表情:随机表情] 会随机发一张群友发过的图，"
                    "接梗、被逗、想缓和气氛时偶尔用一次，别连着用"
                )
            stable += [
                "",
                "【可用的表情包】" + names + "（写 [表情:名字]，只能用这些" + note + "）",
            ]
        if ctx.voice_kinds:
            stable += [
                "",
                "【可用的语音】"
                + "、".join(ctx.voice_kinds)
                + "（想发语音就写 [语音:类别]，只能用这些；别每次都用，偶尔用一次更有意思）",
            ]
        stable += [
            "",
            f"【此刻】{now.strftime('%Y-%m-%d %H:%M')} {WEEKDAYS[now.weekday()]}，{scene}",
        ]

        messages: list[dict] = [{"role": "system", "content": "\n".join(stable).strip()}]

        dynamic: list[str] = []
        overview = self.member_overview(ctx.members, bot_name)
        if overview:
            dynamic.append(overview)

        profile_text = self.profile_block(ctx.sender_profile)
        if profile_text:
            who = ctx.sender_name or str(ctx.sender_qq)
            dynamic.append(f"【正在跟你说话的 {who}】\n{profile_text}")
        if ctx.person_note:
            dynamic.append(f"【关于 {ctx.sender_name or ctx.sender_qq} 你平时就知道的】\n{ctx.person_note}")

        memory_text = self.memory_block(ctx.memories, self.config.memory.retrieve_max_chars)
        if memory_text:
            dynamic.append(memory_text)
        if ctx.memes:
            dynamic.append(
                "【这个群的黑话/梗】（聊天时可以用，但别硬塞）\n"
                + "\n".join(f"- {meme}" for meme in ctx.memes[:6])
            )
        if ctx.open_loops:
            dynamic.append(
                "【你还记着的事】（可以自然跟进，但别像查岗；对方不想聊就放下）\n"
                + "\n".join(
                    f"- {item.get('content')}"
                    for item in ctx.open_loops[:5]
                    if item.get("content")
                )
            )
        if ctx.episodes:
            episode = ctx.episodes[0]
            recap = f"【上次聊到】\n{episode.get('summary') or ''}"
            if episode.get("tone"):
                recap += f"\n当时的情绪：{episode.get('tone')}"
            if episode.get("topics"):
                recap += f"\n话题：{episode.get('topics')}"
            dynamic.append(recap)
        elif ctx.summary:
            dynamic.append(f"【之前的聊天提要】\n{ctx.summary}")
        if ctx.relationship_events:
            dynamic.append(
                "【你们之间发生过的事】（别硬提，合适时再自然地接）\n"
                + "\n".join(
                    f"- {item.get('content')}"
                    for item in ctx.relationship_events[:4]
                    if item.get("content")
                )
            )

        if dynamic:
            messages.append({"role": "system", "content": "\n\n".join(dynamic)})

        if ctx.image_cards:
            messages.append(
                {
                    "role": "system",
                    "content": "【对方刚发的图，用来接话，不要照读】\n"
                    + "\n---\n".join(item for item in ctx.image_cards if item),
                }
            )

        history_text = self.history_lines(ctx.history, bot_name)
        if history_text:
            messages.append(
                {
                    "role": "system",
                    "content": "【最近的聊天记录，只用于理解上下文，不要复述】\n" + history_text,
                }
            )

        if ctx.is_proactive:
            instruction = (
                f"群里刚才在聊上面这些。你可以自然地插一句，也可以只回很短的几句。"
                f"不要总结、不要点评式发言，像群里的一员那样接话。"
            )
            if ctx.sender_name:
                instruction += f"（刚说话的是 {ctx.sender_name}）"
        elif ctx.scope_type == "group":
            instruction = "现在轮到你说。直接给回复，不要解释你在做什么。"
        else:
            instruction = "直接给回复，不要解释。"

        user_parts: list[str] = []
        if ctx.quoted_text:
            user_parts.append(f"（引用的是：{ctx.quoted_text[:80]}）")
        user_parts.append(f"{ctx.sender_name or '对方'}: {ctx.current_text or '（发了消息）'}")
        user_parts.append(instruction)
        messages.append({"role": "user", "content": "\n".join(user_parts)})
        return messages

    # -- 其它小提示词 -------------------------------------------------
    def relevance_prompt(self, text: str, recent: str) -> tuple[str, str]:
        system = (
            "你是一个群聊氛围判断器。判断最后一条消息是否在跟机器人说话、"
            "或者机器人接话是否自然。只输出 0 到 1 之间的一个数，不要输出别的。"
        )
        user = f"最近的聊天：\n{recent}\n\n最后一条：{text}\n\n接话自然度："
        return system, user

    def care_prompt(self, name: str, days: int, last_topic: str) -> tuple[str, str]:
        system = (
            f"你是「{self.config.bot.name}」。要主动给一个 {days} 天没聊天的朋友发一条消息。"
            "像朋友随手发的那样，短、自然、不客套，不要问太多问题。"
        )
        topic = f"上次你们聊到：{last_topic}" if last_topic else "你们之前聊过几次。"
        user = f"对方叫 {name}。{topic}\n写一句话发过去，不要加引号和前缀。"
        return system, user

    def companion_prompt(
        self,
        name: str,
        topic: str,
        recent: str,
        now_text: str,
        last_from_bot: bool,
        *,
        relation: str = "",
        affinity: str = "",
        tags: str = "",
        preferences: str = "",
        notes: str = "",
        memories: list[str] | None = None,
        open_loops: list[str] | None = None,
        relationship_events: list[str] | None = None,
        mood: str = "",
        period: str = "",
    ) -> tuple[str, str]:
        """主动陪伴：不是客服回访，是朋友隔一阵随手接一句。"""
        closeness = affinity or "普通"
        if relation:
            closeness += f"，{relation}"
        system = (
            f"你是「{self.config.bot.name}」，正在 QQ 上主动找 {name} 说话。"
            f"你们的关系：{closeness}。"
            "写 1~2 句，最多 60 字。目标不是完成「问候任务」，是让对方觉得被记得、被在意：\n"
            "- 可以接上次的话题、TA 提过但还没做完的事，但别像查岗；\n"
            "- 可以分享一点自己的状态/小情绪，或者开个轻玩笑、小吐槽；\n"
            "- 关系亲近时可以撒娇或损一句，不熟就别太自来熟；\n"
            "- 不要用「在吗」「忙吗」开头，不要连续追问，不要客服腔，不要解释你是 AI；\n"
            "- 不要假设对方一直在等你/盯着手机，也别用「又」数落对方；玩笑要软，不要阴阳怪气；\n"
            "- 最多带一个很轻的动作/表情，比如（甩尾巴），别每句都用；\n"
            "- 不要列表、Markdown、引号或前缀。"
        )
        parts = [f"对方：{name}"]
        if affinity:
            parts.append(f"亲密度：{affinity}")
        if relation:
            parts.append(f"你给这段关系打的标签：{relation}")
        if tags:
            parts.append(f"TA 的标签：{tags}")
        if preferences:
            parts.append(f"TA 喜欢：{preferences}")
        if notes:
            parts.append(f"你记的备注：{notes}")
        if memories:
            parts.append("你还记得关于 TA 的事：\n" + "\n".join(f"- {x}" for x in memories[:3]))
        if open_loops:
            parts.append(
                "TA 最近提过、还没做完的事：\n"
                + "\n".join(f"- {x}" for x in open_loops[:3])
            )
        if relationship_events:
            parts.append(
                "你们之间发生过的事（别硬提，合适时再自然接）：\n"
                + "\n".join(f"- {x}" for x in relationship_events[:3])
            )
        if mood:
            parts.append(f"你此刻的心情：{mood}")
        parts.append(f"上次聊到：{topic or '（还没有摘要）'}")
        parts.append(f"最近的聊天：\n{recent or '（没有记录）'}")
        parts.append(f"现在：{now_text}（{period or '今天'}）")
        if last_from_bot:
            parts.append("上一条是你发的，对方还没回；可以轻轻补一句，但别催，也别连着发。")
        parts.append("只输出要发送的那句话。")
        return system, "\n".join(parts)

    def summary_prompt(self, history_text: str) -> tuple[str, str]:
        system = (
            "把下面这段 QQ 群聊/私聊压缩成一段 200 字以内的中文摘要。"
            "保留：谁说了什么要紧事、情绪变化、做出的约定、人物关系的变化。"
            "用陈述句，不要评价，不要用列表。"
        )
        return system, history_text

    def extract_prompt(self, history_text: str) -> tuple[str, str]:
        system = (
            "你从 QQ 聊天里抽取需要长期记住的信息，输出 JSON。"
            "只抽真正值得记的：偏好、身份、经历、关系变化、雷点、群梗。"
            "不要抽一次性寒暄、天气、无意义语气词。"
            "JSON 结构：\n"
            '{"facts":[{"subject_qq":123,"kind":"preference|identity|event|'
            'relationship|taboo|meme","content":"一句话","importance":1-5,'
            '"confidence":0到1}],\n'
            ' "profile_patch":{"display_name":"","identity":"","relation":"",'
            '"tags":"逗号分隔","preferences":"","taboos":"","notes":"",'
            '"mood":""},\n'
            ' "open_loops":[{"subject_qq":123,"kind":"topic|promise|plan|question",'
            '"content":"对方提过、还没做完或还没回的事"}],\n'
            ' "episode":{"summary":"这段对话的完整概括","tone":"情绪基调",'
            '"topics":["话题1","话题2"]},\n'
            ' "relationship_events":[{"kind":"first_meet|promise|conflict|apology|'
            'milestone|joke","content":"关系上的一个变化","importance":1-5}],\n'
            ' "summary":"这段对话的一句话概括",\n'
            ' "emotion":{"target_qq":123,"delta":-5到5的整数}}\n'
            "没有可抽的内容就返回空数组和空对象。subject_qq 用消息里出现的 QQ 号。"
        )
        return system, history_text


def persona_dir() -> Path:
    return PERSONA_DIR


def ensure_persona_files(name: str, persona_dir: Path | str | None = None) -> None:
    """首次运行时生成示例人设文件。"""
    base = Path(persona_dir) if persona_dir else PERSONA_DIR
    base.mkdir(parents=True, exist_ok=True)
    groups_dir = base / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    (base / "private").mkdir(parents=True, exist_ok=True)
    global_file = base / "global.md"
    if not global_file.exists():
        global_file.write_text(
            f"# {name}\n\n"
            f"名字：{name}。本体是一条圆滚滚的蓝色胖鱼，最爱吃白饭。\n\n"
            "性格：好吃懒做，但答应的事会做完；被说胖会立刻反驳「我才不胖，我只是刚吃完饭」。\n"
            "说话习惯：短句、口语，常用「白饭！」「让我扒两口」「好耶」。\n"
            "干活干到一半会哼歌，哼着哼着自己接一句「啦啦啦～」。\n"
            "不会做的事：不写小作文，不列 1234，不打官腔。\n",
            encoding="utf-8",
        )
    example = groups_dir / "example.md"
    if not example.exists():
        example.write_text(
            "# 示例群人设（复制一份改名成 <群号>.md 就能生效）\n\n"
            "这个群是同事群，大家说话比较随意，喜欢互相损。\n"
            "群规：不聊政治，不发广告，晚上 12 点后基本没人说话。\n"
            "群梗：管老板叫「老板大人」；周四下午固定摸鱼。\n",
            encoding="utf-8",
        )


def file_exists(path: str | os.PathLike[str]) -> bool:
    return Path(path).exists()
