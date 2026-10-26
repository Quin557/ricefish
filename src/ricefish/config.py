"""配置加载：YAML 子集 -> 强类型 dataclass，并合并环境变量。

配置优先级（后者覆盖前者）：
    默认值  <  config/bot.yaml  <  环境变量  <  Web 面板里的运行时设置（存 DB）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import yamlish

DEFAULT_CONFIG_PATH = "config/bot.yaml"


def _split_ids(raw: str) -> list[int]:
    """把 "123,456 789" 这类输入拆成整数列表。"""
    out: list[int] = []
    for chunk in re.split(r"[,，;；\s]+", raw or ""):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


# --------------------------------------------------------------------------
# 类型转换工具
# --------------------------------------------------------------------------
def _int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on", "是"):
            return True
        if low in ("0", "false", "no", "off", "否"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _str(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None]


def _int_pair(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (_int(value[0], default[0]), _int(value[1], default[1]))
    return default


def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
        if node is None:
            return default
    return node


# --------------------------------------------------------------------------
# 各配置段
# --------------------------------------------------------------------------
@dataclass
class BotConfig:
    name: str = "肥鱼"
    aliases: list[str] = field(default_factory=lambda: ["肥鱼"])
    owner_qq: int = 0
    #: 主人的显示名，用于提示词里的【关系】块
    owner_name: str = ""
    admin_qqs: list[int] = field(default_factory=list)
    mode: str = "active"  # active / passive / readonly
    dry_run: bool = True
    timezone: str = "Asia/Shanghai"

    def is_admin(self, qq: int) -> bool:
        return qq == self.owner_qq or qq in self.admin_qqs


@dataclass
class OneBotConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/onebot/v11/ws"
    token: str = ""


@dataclass
class AccessConfig:
    allowed_groups: list[int] = field(default_factory=list)
    allowed_friends: list[int] = field(default_factory=list)
    blocked_users: list[int] = field(default_factory=list)

    def group_ok(self, group_id: int) -> bool:
        if group_id in self.blocked_users:
            return False
        return group_id in self.allowed_groups

    def friend_ok(self, user_id: int) -> bool:
        if user_id in self.blocked_users:
            return False
        return user_id in self.allowed_friends


@dataclass
class GroupTriggerConfig:
    at_or_reply: bool = True
    keywords: list[str] = field(default_factory=list)
    reply_to_other_bot: bool = False


@dataclass
class PrivateTriggerConfig:
    always_reply: bool = True


@dataclass
class ProactiveConfig:
    enabled: bool = True
    probability: float = 0.15
    min_interval_sec: int = 600
    daily_quota: int = 20
    relevance_threshold: float = 0.6


@dataclass
class TriggerConfig:
    group: GroupTriggerConfig = field(default_factory=GroupTriggerConfig)
    private: PrivateTriggerConfig = field(default_factory=PrivateTriggerConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)


@dataclass
class SendConfig:
    typing_ms_per_char: int = 80
    typing_min_ms: int = 800
    typing_max_ms: int = 6000
    jitter: float = 0.25
    max_segments: int = 3
    segment_max_chars: int = 120
    segment_gap_ms: tuple[int, int] = (600, 1500)
    quote_reply: bool = True
    #: 回复完随手补一张群友表情的概率（0 = 关）
    auto_emoji_probability: float = 0.12
    #: 同一个会话两次自动表情的最小间隔（秒）
    auto_emoji_cooldown_sec: int = 300


@dataclass
class RateLimitConfig:
    group_per_min: int = 3
    group_per_day: int = 150
    user_per_hour: int = 10
    duplicate_window_sec: int = 300
    quiet_hours: str = "00:30-07:30"
    quiet_group_proactive: bool = True
    quiet_private_passive: bool = True

    def is_quiet(self, hour: int, minute: int) -> bool:
        """判断某个时刻是否落在静默时段内（支持跨零点）。"""
        span = self.quiet_hours.strip()
        if not span or "-" not in span:
            return False
        try:
            start_raw, end_raw = span.split("-", 1)
            sh, sm = (int(x) for x in start_raw.strip().split(":"))
            eh, em = (int(x) for x in end_raw.strip().split(":"))
        except ValueError:
            return False
        cur = hour * 60 + minute
        start = sh * 60 + sm
        end = eh * 60 + em
        if start == end:
            return False
        if start < end:
            return start <= cur < end
        return cur >= start or cur < end


@dataclass
class ExtractorConfig:
    enabled: bool = True
    every_messages: int = 20
    idle_minutes: int = 3
    daily_budget_per_chat: int = 100
    model: str = "fast"


@dataclass
class MemoryConfig:
    window_messages: int = 40
    window_tokens: int = 6000
    summary_every_messages: int = 30
    summary_idle_minutes: int = 15
    retrieve_top_k: int = 8
    retrieve_max_chars: int = 200
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
    #: "off" = 只用关键词检索；"ollama" = 额外叠加本地语义向量
    embedding: str = "off"
    embedding_model: str = "bge-m3"
    embedding_url: str = "http://127.0.0.1:11434"


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    pro_model: str = "deepseek-v4-pro"
    vision_model: str = "deepseek-flash"
    temperature: float = 1.1
    max_output_tokens: int = 800
    timeout_sec: int = 30
    retries: int = 1
    api_key: str = ""
    #: "" = 不传该参数（用接口默认）；可填 enabled / disabled
    thinking: str = ""
    #: 视觉任务单独控制（默认关，避免推理吃掉输出 token）
    thinking_vision: str = "disabled"
    #: 抽取/摘要/相关性等工具任务单独控制
    thinking_utility: str = "disabled"
    #: 视觉单次输出上限
    vision_max_tokens: int = 700
    #: 流式输出：边生成边发，首字更快
    stream: bool = True
    #: 流式时缓冲到多少字才允许按句切分发送
    stream_min_chars: int = 8
    #: 单次流式发送的最大缓冲（超过就强制切一刀，避免长句卡住）
    stream_max_buffer: int = 60
    #: 可见字数达到多少才真正发出去（过滤"……""啊"这类碎片）
    stream_min_visible_chars: int = 10
    #: 一轮回复最多拆几条
    stream_max_messages: int = 2
    #: 两条流式消息之间的停顿（毫秒，模拟打字）
    stream_chunk_gap_ms: tuple[int, int] = (0, 0)
    #: 首字超过多少秒先给"在想"信号
    stream_ack_after_sec: float = 7.0
    #: reaction=给消息加表情回应；text=发一句短确认再撤回；off=不发
    stream_ack_mode: str = "reaction"
    #: 表情回应用的 QQ emoji id
    stream_ack_emoji_id: str = "128531"
    #: text 模式下的短确认文案
    stream_ack_texts: list[str] = field(
        default_factory=lambda: ["等下，我想想", "这个我得想一下", "嗯……让我捋捋"]
    )
    #: 首 token 看门狗（秒）
    stream_first_token_timeout_sec: float = 15.0
    #: >0 时，首 token 超过这么多秒就先发一句「想了 N 秒…」（默认关）
    stream_slow_note_sec: float = 0.0
    stream_slow_note_template: str = "想了 {sec} 秒…"

    def thinking_for(self, purpose: str) -> str:
        """按用途返回 thinking 参数：视觉/工具任务默认关，聊天用主开关。"""
        if purpose == "vision":
            return self.thinking_vision
        if purpose in ("extract", "summary", "relevance", "care", "companion"):
            return self.thinking_utility
        return self.thinking


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8721
    token: str = ""

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass
class ConcurrencyConfig:
    """同时生成回复的会话数上限。别调太大，模型和账号都受不了。"""

    max_parallel: int = 6
    max_queue: int = 24
    acquire_timeout_sec: int = 60

    def clamped(self) -> int:
        return max(1, min(32, int(self.max_parallel)))


@dataclass
class CareConfig:
    enabled: bool = False
    idle_days: int = 3
    cooldown_days: int = 7
    max_per_day: int = 5


@dataclass
class CompanionConfig:
    """更主动的私聊陪伴：隔一阵没说话就主动发一条。"""

    enabled: bool = False
    #: 最后一条是对方发的，隔多久可以主动找
    idle_minutes: int = 120
    #: 最后一条是机器人发的，隔多久可以补一句
    follow_up_minutes: int = 45
    #: 同一个会话两次主动之间的最小间隔
    cooldown_minutes: int = 180
    #: 每个会话每天最多主动几次
    daily_per_chat: int = 3
    #: 所有会话每天合计上限
    daily_total: int = 20
    #: 静默时段不主动
    respect_quiet_hours: bool = True
    #: 一轮 tick 最多发几条（避免同时轰炸）
    max_per_tick: int = 1


@dataclass
class ToolsConfig:
    weather: bool = True
    vision: bool = True
    #: 一轮最多理解几张图
    vision_max_images: int = 2
    #: 拉取图片的超时时间（秒）
    vision_timeout_sec: float = 15.0
    #: 尝试调用 NapCat 的 ocr_image（没有就静默跳过）
    vision_ocr: bool = True
    #: 收到新图时自动让视觉模型打情绪/标签（后台跑，不阻塞回复）
    sticker_autotag: bool = True
    #: 允许模型自己调用工具（天气/时间/搜索）
    enable_calls: bool = True
    search_key: str = ""
    tts_command: str = ""
    #: 内置 edge-tts 的音色和语速（没配 tts_command 时生效）
    tts_voice: str = "zh-CN-XiaoyiNeural"
    tts_rate: str = "+6%"
    tts_pitch: str = "+10Hz"
    #: 语气起伏强度 0~1：按句子类型（疑问/感叹/省略）自动改语速和音调
    tts_expressiveness: float = 0.7
    asr_command: str = ""
    #: 本地预合成语音包目录，支持逗号分隔多个（manifest.json + wav）。
    #: 不存在的目录会自动跳过；空字符串 = 关闭。
    voice_pack: str = "assets/voices,data/voice-packs/aceice"
    #: 被戳一戳时用语音包回应的概率（0~1），剩下的用文字。
    poke_voice_probability: float = 0.6
    #: 梗图包目录（manifest.json + 图片）。空字符串 = 关闭 /梗图。
    meme_pack: str = "assets/memes"
    #: 同一会话两次发梗图的最小间隔（秒）。
    meme_cooldown_sec: int = 30


@dataclass
class GamesConfig:
    enabled: bool = True
    daily_limit: int = 12


@dataclass
class PluginsConfig:
    enabled: bool = True
    dir: str = "plugins"


@dataclass
class Config:
    bot: BotConfig = field(default_factory=BotConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    send: SendConfig = field(default_factory=SendConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    web: WebConfig = field(default_factory=WebConfig)
    care: CareConfig = field(default_factory=CareConfig)
    companion: CompanionConfig = field(default_factory=CompanionConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    games: GamesConfig = field(default_factory=GamesConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)

    source_path: str = ""
    #: 面板可热改的运行时开关
    runtime: dict[str, Any] = field(default_factory=dict)

    # -- 加载 --------------------------------------------------------
    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        path = path or os.environ.get("RICEFISH_CONFIG") or DEFAULT_CONFIG_PATH
        raw = yamlish.load_file(path, default={}) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件格式错误（顶层应为映射）: {path}")
        cfg = cls.from_dict(raw)
        cfg.source_path = str(Path(path).resolve()) if Path(path).exists() else path
        cfg._apply_env()
        return cfg

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        bot = BotConfig(
            name=_str(_get(raw, "bot.name"), "肥鱼"),
            aliases=_str_list(_get(raw, "bot.aliases")) or [_str(_get(raw, "bot.name"), "肥鱼")],
            owner_qq=_int(_get(raw, "bot.owner_qq"), 0),
            owner_name=_str(_get(raw, "bot.owner_name"), ""),
            admin_qqs=_int_list(_get(raw, "bot.admin_qqs")),
            mode=_str(_get(raw, "bot.mode"), "active").lower(),
            dry_run=_bool(_get(raw, "bot.dry_run"), True),
            timezone=_str(_get(raw, "bot.timezone"), "Asia/Shanghai"),
        )
        if bot.owner_qq and bot.owner_qq not in bot.admin_qqs:
            bot.admin_qqs.append(bot.owner_qq)

        onebot = OneBotConfig(
            host=_str(_get(raw, "onebot.host"), "127.0.0.1"),
            port=_int(_get(raw, "onebot.port"), 8080),
            path=_str(_get(raw, "onebot.path"), "/onebot/v11/ws"),
            token=_str(_get(raw, "onebot.token"), ""),
        )

        access = AccessConfig(
            allowed_groups=_int_list(_get(raw, "access.allowed_groups")),
            allowed_friends=_int_list(_get(raw, "access.allowed_friends")),
            blocked_users=_int_list(_get(raw, "access.blocked_users")),
        )

        trigger = TriggerConfig(
            group=GroupTriggerConfig(
                at_or_reply=_bool(_get(raw, "trigger.group.at_or_reply"), True),
                keywords=_str_list(_get(raw, "trigger.group.keywords")),
                reply_to_other_bot=_bool(_get(raw, "trigger.group.reply_to_other_bot"), False),
            ),
            private=PrivateTriggerConfig(
                always_reply=_bool(_get(raw, "trigger.private.always_reply"), True),
            ),
            proactive=ProactiveConfig(
                enabled=_bool(_get(raw, "trigger.proactive.enabled"), True),
                probability=_float(_get(raw, "trigger.proactive.probability"), 0.15),
                min_interval_sec=_int(_get(raw, "trigger.proactive.min_interval_sec"), 600),
                daily_quota=_int(_get(raw, "trigger.proactive.daily_quota"), 20),
                relevance_threshold=_float(
                    _get(raw, "trigger.proactive.relevance_threshold"), 0.6
                ),
            ),
        )

        send = SendConfig(
            typing_ms_per_char=_int(_get(raw, "send.typing_ms_per_char"), 80),
            typing_min_ms=_int(_get(raw, "send.typing_min_ms"), 800),
            typing_max_ms=_int(_get(raw, "send.typing_max_ms"), 6000),
            jitter=_float(_get(raw, "send.jitter"), 0.25),
            max_segments=_int(_get(raw, "send.max_segments"), 3),
            segment_max_chars=_int(_get(raw, "send.segment_max_chars"), 120),
            segment_gap_ms=_int_pair(_get(raw, "send.segment_gap_ms"), (600, 1500)),
            quote_reply=_bool(_get(raw, "send.quote_reply"), True),
            auto_emoji_probability=max(
                0.0,
                min(1.0, _float(_get(raw, "send.auto_emoji_probability"), 0.12)),
            ),
            auto_emoji_cooldown_sec=_int(
                _get(raw, "send.auto_emoji_cooldown_sec"), 300
            ),
        )

        rate_limit = RateLimitConfig(
            group_per_min=_int(_get(raw, "rate_limit.group_per_min"), 3),
            group_per_day=_int(_get(raw, "rate_limit.group_per_day"), 150),
            user_per_hour=_int(_get(raw, "rate_limit.user_per_hour"), 10),
            duplicate_window_sec=_int(_get(raw, "rate_limit.duplicate_window_sec"), 300),
            quiet_hours=_str(_get(raw, "rate_limit.quiet_hours"), "00:30-07:30"),
            quiet_group_proactive=_bool(
                _get(raw, "rate_limit.quiet_group_proactive"), True
            ),
            quiet_private_passive=_bool(
                _get(raw, "rate_limit.quiet_private_passive"), True
            ),
        )

        memory = MemoryConfig(
            window_messages=_int(_get(raw, "memory.window_messages"), 40),
            window_tokens=_int(_get(raw, "memory.window_tokens"), 6000),
            summary_every_messages=_int(_get(raw, "memory.summary_every_messages"), 30),
            summary_idle_minutes=_int(_get(raw, "memory.summary_idle_minutes"), 15),
            retrieve_top_k=_int(_get(raw, "memory.retrieve_top_k"), 8),
            retrieve_max_chars=_int(_get(raw, "memory.retrieve_max_chars"), 200),
            extractor=ExtractorConfig(
                enabled=_bool(_get(raw, "memory.extractor.enabled"), True),
                every_messages=_int(_get(raw, "memory.extractor.every_messages"), 20),
                idle_minutes=_int(_get(raw, "memory.extractor.idle_minutes"), 3),
                daily_budget_per_chat=_int(
                    _get(raw, "memory.extractor.daily_budget_per_chat"), 100
                ),
                model=_str(_get(raw, "memory.extractor.model"), "fast"),
            ),
            embedding=_str(_get(raw, "memory.embedding"), "off").lower(),
            embedding_model=_str(_get(raw, "memory.embedding_model"), "bge-m3"),
            embedding_url=_str(
                _get(raw, "memory.embedding_url"), "http://127.0.0.1:11434"
            ),
        )

        llm = LLMConfig(
            base_url=_str(_get(raw, "llm.base_url"), "https://api.deepseek.com"),
            model=_str(_get(raw, "llm.model"), "deepseek-flash"),
            pro_model=_str(_get(raw, "llm.pro_model"), "deepseek-v4-pro"),
            vision_model=_str(_get(raw, "llm.vision_model"), "deepseek-flash"),
            temperature=_float(_get(raw, "llm.temperature"), 1.1),
            max_output_tokens=_int(_get(raw, "llm.max_output_tokens"), 800),
            timeout_sec=_int(_get(raw, "llm.timeout_sec"), 30),
            retries=_int(_get(raw, "llm.retries"), 1),
            thinking=_str(_get(raw, "llm.thinking"), ""),
            thinking_vision=_str(_get(raw, "llm.thinking_vision"), "disabled"),
            thinking_utility=_str(_get(raw, "llm.thinking_utility"), "disabled"),
            vision_max_tokens=_int(_get(raw, "llm.vision_max_tokens"), 700),
            stream=_bool(_get(raw, "llm.stream"), True),
            stream_min_chars=_int(_get(raw, "llm.stream_min_chars"), 8),
            stream_max_buffer=_int(_get(raw, "llm.stream_max_buffer"), 60),
            stream_min_visible_chars=_int(
                _get(raw, "llm.stream_min_visible_chars"), 10
            ),
            stream_max_messages=_int(_get(raw, "llm.stream_max_messages"), 2),
            stream_chunk_gap_ms=_int_pair(
                _get(raw, "llm.stream_chunk_gap_ms"), (0, 0)
            ),
            stream_ack_after_sec=_float(
                _get(raw, "llm.stream_ack_after_sec"), 7.0
            ),
            stream_ack_mode=_str(_get(raw, "llm.stream_ack_mode"), "reaction"),
            stream_ack_emoji_id=_str(
                _get(raw, "llm.stream_ack_emoji_id"), "128531"
            ),
            stream_ack_texts=_str_list(
                _get(
                    raw,
                    "llm.stream_ack_texts",
                    ["等下，我想想", "这个我得想一下", "嗯……让我捋捋"],
                )
            ),
            stream_first_token_timeout_sec=_float(
                _get(raw, "llm.stream_first_token_timeout_sec"), 15.0
            ),
            stream_slow_note_sec=max(
                0.0, _float(_get(raw, "llm.stream_slow_note_sec"), 0.0)
            ),
            stream_slow_note_template=_str(
                _get(raw, "llm.stream_slow_note_template"), "想了 {sec} 秒…"
            ),
        )

        web = WebConfig(
            enabled=_bool(_get(raw, "web.enabled"), True),
            host=_str(_get(raw, "web.host"), "127.0.0.1"),
            port=_int(_get(raw, "web.port"), 8721),
            token=_str(_get(raw, "web.token"), ""),
        )

        care = CareConfig(
            enabled=_bool(_get(raw, "care.enabled"), False),
            idle_days=_int(_get(raw, "care.idle_days"), 3),
            cooldown_days=_int(_get(raw, "care.cooldown_days"), 7),
            max_per_day=_int(_get(raw, "care.max_per_day"), 5),
        )

        companion = CompanionConfig(
            enabled=_bool(_get(raw, "companion.enabled"), False),
            idle_minutes=max(5, _int(_get(raw, "companion.idle_minutes"), 120)),
            follow_up_minutes=max(
                5, _int(_get(raw, "companion.follow_up_minutes"), 45)
            ),
            cooldown_minutes=max(
                5, _int(_get(raw, "companion.cooldown_minutes"), 180)
            ),
            daily_per_chat=max(1, _int(_get(raw, "companion.daily_per_chat"), 3)),
            daily_total=max(1, _int(_get(raw, "companion.daily_total"), 20)),
            respect_quiet_hours=_bool(
                _get(raw, "companion.respect_quiet_hours"), True
            ),
            max_per_tick=max(1, _int(_get(raw, "companion.max_per_tick"), 1)),
        )

        concurrency = ConcurrencyConfig(
            max_parallel=_int(_get(raw, "concurrency.max_parallel"), 6),
            max_queue=_int(_get(raw, "concurrency.max_queue"), 24),
            acquire_timeout_sec=_int(_get(raw, "concurrency.acquire_timeout_sec"), 60),
        )

        tools = ToolsConfig(
            weather=_bool(_get(raw, "tools.weather"), True),
            vision=_bool(_get(raw, "tools.vision"), True),
            vision_max_images=max(1, _int(_get(raw, "tools.vision_max_images"), 2)),
            vision_timeout_sec=max(
                1.0, _float(_get(raw, "tools.vision_timeout_sec"), 15.0)
            ),
            vision_ocr=_bool(_get(raw, "tools.vision_ocr"), True),
            sticker_autotag=_bool(_get(raw, "tools.sticker_autotag"), True),
            enable_calls=_bool(_get(raw, "tools.enable_calls"), True),
            search_key=_str(_get(raw, "tools.search_key"), ""),
            tts_command=_str(_get(raw, "tools.tts_command"), ""),
            tts_voice=_str(_get(raw, "tools.tts_voice"), "zh-CN-XiaoyiNeural"),
            tts_rate=_str(_get(raw, "tools.tts_rate"), "+6%"),
            tts_pitch=_str(_get(raw, "tools.tts_pitch"), "+10Hz"),
            tts_expressiveness=max(
                0.0,
                min(1.0, _float(_get(raw, "tools.tts_expressiveness"), 0.7)),
            ),
            asr_command=_str(_get(raw, "tools.asr_command"), ""),
            voice_pack=_str(
                _get(raw, "tools.voice_pack"), "assets/voices,data/voice-packs/aceice"
            ),
            poke_voice_probability=max(
                0.0, min(1.0, _float(_get(raw, "tools.poke_voice_probability"), 0.6))
            ),
            meme_pack=_str(_get(raw, "tools.meme_pack"), "assets/memes"),
            meme_cooldown_sec=max(0, _int(_get(raw, "tools.meme_cooldown_sec"), 30)),
        )
        games = GamesConfig(
            enabled=_bool(_get(raw, "games.enabled"), True),
            daily_limit=_int(_get(raw, "games.daily_limit"), 12),
        )
        plugins = PluginsConfig(
            enabled=_bool(_get(raw, "plugins.enabled"), True),
            dir=_str(_get(raw, "plugins.dir"), "plugins"),
        )

        return cls(
            bot=bot,
            onebot=onebot,
            access=access,
            trigger=trigger,
            send=send,
            rate_limit=rate_limit,
            memory=memory,
            llm=llm,
            web=web,
            care=care,
            companion=companion,
            concurrency=concurrency,
            tools=tools,
            games=games,
            plugins=plugins,
        )

    # -- 环境变量 ----------------------------------------------------
    def _apply_env(self) -> None:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if key:
            self.llm.api_key = key
        base = os.environ.get("DEEPSEEK_BASE_URL", "").strip()
        if base:
            self.llm.base_url = base
        ws_token = os.environ.get("RICEFISH_WS_TOKEN", "").strip()
        if ws_token:
            self.onebot.token = ws_token
        panel_token = os.environ.get("RICEFISH_PANEL_TOKEN", "").strip()
        if panel_token:
            self.web.token = panel_token
        panel_port = os.environ.get("RICEFISH_PANEL_PORT", "").strip()
        if panel_port.isdigit():
            self.web.port = int(panel_port)
        panel_host = os.environ.get("RICEFISH_WEB_HOST", "").strip()
        if panel_host:
            self.web.host = panel_host
        web_enabled = os.environ.get("RICEFISH_WEB_ENABLED", "").strip()
        if web_enabled:
            self.web.enabled = _bool(web_enabled, self.web.enabled)
        model = os.environ.get("RICEFISH_MODEL", "").strip()
        if model:
            self.llm.model = model

        # 一键安装脚本把这些写进 .env，用户不用手改 YAML
        owner = os.environ.get("RICEFISH_OWNER_QQ", "").strip()
        if owner.isdigit():
            self.bot.owner_qq = int(owner)
            if int(owner) not in self.bot.admin_qqs:
                self.bot.admin_qqs.append(int(owner))
        owner_name = os.environ.get("RICEFISH_OWNER_NAME", "").strip()
        if owner_name:
            self.bot.owner_name = owner_name
        groups = _split_ids(os.environ.get("RICEFISH_ALLOWED_GROUPS", ""))
        if groups:
            self.access.allowed_groups = groups
        friends = _split_ids(os.environ.get("RICEFISH_ALLOWED_FRIENDS", ""))
        if friends:
            self.access.allowed_friends = friends

    # -- 运行时开关（面板热改） ---------------------------------------
    def apply_runtime(self, values: dict[str, Any]) -> None:
        """把 DB 里的 settings 覆盖到配置上。"""
        self.runtime.update(values or {})
        if "dry_run" in self.runtime:
            self.bot.dry_run = _bool(self.runtime["dry_run"], self.bot.dry_run)
        if "mode" in self.runtime:
            self.bot.mode = _str(self.runtime["mode"], self.bot.mode).lower()
        if "proactive_enabled" in self.runtime:
            self.trigger.proactive.enabled = _bool(
                self.runtime["proactive_enabled"], self.trigger.proactive.enabled
            )
        if "proactive_probability" in self.runtime:
            self.trigger.proactive.probability = _float(
                self.runtime["proactive_probability"],
                self.trigger.proactive.probability,
            )
        if "proactive_min_interval" in self.runtime:
            self.trigger.proactive.min_interval_sec = max(
                0,
                _int(
                    self.runtime["proactive_min_interval"],
                    self.trigger.proactive.min_interval_sec,
                ),
            )
        if "proactive_daily_quota" in self.runtime:
            self.trigger.proactive.daily_quota = max(
                0,
                _int(
                    self.runtime["proactive_daily_quota"],
                    self.trigger.proactive.daily_quota,
                ),
            )
        if "proactive_relevance_threshold" in self.runtime:
            self.trigger.proactive.relevance_threshold = max(
                0.0,
                min(
                    1.0,
                    _float(
                        self.runtime["proactive_relevance_threshold"],
                        self.trigger.proactive.relevance_threshold,
                    ),
                ),
            )
        if "group_per_min" in self.runtime:
            self.rate_limit.group_per_min = _int(
                self.runtime["group_per_min"], self.rate_limit.group_per_min
            )
        if "group_per_day" in self.runtime:
            self.rate_limit.group_per_day = _int(
                self.runtime["group_per_day"], self.rate_limit.group_per_day
            )
        if "quiet_hours" in self.runtime:
            self.rate_limit.quiet_hours = _str(
                self.runtime["quiet_hours"], self.rate_limit.quiet_hours
            )
        if "max_parallel" in self.runtime:
            self.concurrency.max_parallel = max(
                1, _int(self.runtime["max_parallel"], self.concurrency.max_parallel)
            )
        if "bot_name" in self.runtime:
            self.bot.name = _str(self.runtime["bot_name"], self.bot.name)
        if "companion_enabled" in self.runtime:
            self.companion.enabled = _bool(
                self.runtime["companion_enabled"], self.companion.enabled
            )
        if "companion_idle_minutes" in self.runtime:
            self.companion.idle_minutes = max(
                5,
                _int(
                    self.runtime["companion_idle_minutes"],
                    self.companion.idle_minutes,
                ),
            )
        if "companion_follow_up_minutes" in self.runtime:
            self.companion.follow_up_minutes = max(
                5,
                _int(
                    self.runtime["companion_follow_up_minutes"],
                    self.companion.follow_up_minutes,
                ),
            )
        if "companion_cooldown_minutes" in self.runtime:
            self.companion.cooldown_minutes = max(
                5,
                _int(
                    self.runtime["companion_cooldown_minutes"],
                    self.companion.cooldown_minutes,
                ),
            )
        if "companion_daily_per_chat" in self.runtime:
            self.companion.daily_per_chat = max(
                1,
                _int(
                    self.runtime["companion_daily_per_chat"],
                    self.companion.daily_per_chat,
                ),
            )
        if "companion_daily_total" in self.runtime:
            self.companion.daily_total = max(
                1,
                _int(
                    self.runtime["companion_daily_total"],
                    self.companion.daily_total,
                ),
            )
        # 白名单：面板改完立刻生效；留空表示不改动（避免手滑清空）
        if "allowed_groups" in self.runtime:
            groups = _split_ids(str(self.runtime["allowed_groups"]))
            if groups:
                self.access.allowed_groups = groups
        if "allowed_friends" in self.runtime:
            friends = _split_ids(str(self.runtime["allowed_friends"]))
            if friends:
                self.access.allowed_friends = friends

    def apply_access(self, groups: list[int], friends: list[int]) -> None:
        """面板白名单：允许空列表（空 = 谁都不理），和 apply_runtime 的旧语义分开。"""
        self.access.allowed_groups = sorted({int(item) for item in groups if int(item) > 0})
        self.access.allowed_friends = sorted({int(item) for item in friends if int(item) > 0})

    # -- 便捷 --------------------------------------------------------
    def with_dry_run(self, dry_run: bool) -> "Config":
        return replace(self, bot=replace(self.bot, dry_run=dry_run))

    @property
    def readonly(self) -> bool:
        return self.bot.mode == "readonly"

    @property
    def passive(self) -> bool:
        return self.bot.mode == "passive"
