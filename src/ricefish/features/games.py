"""群聊小游戏：猜词、21 点、海龟汤。

设计原则：
- 状态只放内存。重启后游戏消失，不写库（游戏不是记忆）。
- 每个会话同时只允许一个进行中的游戏。
- 有超时（默认 10 分钟）和每日开局上限，防止把群刷成游戏厅。
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import Config
from ..llm.client import LLMError
from ..memory.store import MemoryStore

if TYPE_CHECKING:  # pragma: no cover
    from .commands import CommandContext

log = logging.getLogger("ricefish.games")

GAME_TIMEOUT = 600.0  # 10 分钟

WORDS: dict[str, list[str]] = {
    "吃的": ["米饭", "火锅", "包子", "奶茶", "西瓜", "面条", "饺子", "烤肉", "巧克力", "布丁"],
    "动物": ["企鹅", "树懒", "水母", "刺猬", "松鼠", "熊猫", "鲸鱼", "蜗牛", "狐狸", "猫头鹰"],
    "物品": ["雨伞", "台灯", "冰箱", "枕头", "镜子", "钥匙", "拖鞋", "扫把", "闹钟", "水杯"],
    "地方": ["学校", "医院", "机场", "超市", "图书馆", "海边", "山顶", "地铁", "菜市场", "电影院"],
}

OPENERS = {
    "吃的": "是能吃的东西",
    "动物": "是一种动物",
    "物品": "是日常用品",
    "地方": "是个地方",
}

# 这些短句在猜词进行中不该被当成答案
CHAT_NOISE = {
    "哈哈", "哈哈哈", "笑死", "好耶", "在吗", "草", "6", "666", "牛", "啊这",
    "确实", "好的", "嗯", "哦", "行", "对对对", "真的", "什么", "咋了", "人呢",
}


@dataclass
class Game:
    kind: str
    scope_key: str
    state: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    initiator: int = 0
    initiator_name: str = ""

    def expired(self) -> bool:
        return time.time() - self.started_at > GAME_TIMEOUT


class GameManager:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        llm=None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.llm = llm
        self.rng = rng or random.Random()
        self._games: dict[str, Game] = {}
        self._daily: dict[str, list] = {}  # scope_key -> [day, count]
        self.stats = {"started": 0, "won": 0, "answered": 0}

    # ------------------------------------------------------------------
    def active(self, scope_key: str) -> Game | None:
        game = self._games.get(scope_key)
        if game and game.expired():
            self._games.pop(scope_key, None)
            return None
        return game

    def stop(self, scope_key: str) -> bool:
        return self._games.pop(scope_key, None) is not None

    def _quota_left(self, scope_key: str) -> bool:
        day = time.strftime("%Y-%m-%d")
        entry = self._daily.setdefault(scope_key, [day, 0])
        if entry[0] != day:
            entry[0], entry[1] = day, 0
        return entry[1] < max(1, self.config.games.daily_limit)

    def _count(self, scope_key: str) -> None:
        entry = self._daily.setdefault(scope_key, [time.strftime("%Y-%m-%d"), 0])
        entry[1] += 1

    # ------------------------------------------------------------------
    async def handle(self, text: str, ctx: "CommandContext") -> bool:
        """处理游戏相关命令；返回 True 表示这条消息被游戏吃掉了。"""
        if not self.config.games.enabled:
            return False
        from .commands import split_command

        name, args = split_command(text)
        scope_key = f"{ctx.scope_type}:{ctx.scope_id}"

        if name in ("猜词", "猜"):
            await self._say(ctx, await self._start_word(scope_key, ctx))
            return True
        if name in ("21点", "21", "blackjack"):
            await self._say(ctx, await self._start_blackjack(scope_key, ctx))
            return True
        if name in ("汤", "海龟汤"):
            await self._say(ctx, await self._start_soup(scope_key, ctx))
            return True
        if name in ("揭晓", "答案"):
            await self._say(ctx, self._reveal_soup(scope_key, ctx))
            return True
        if name in ("不玩了", "结束游戏", "停"):
            game = self.active(scope_key)
            if game:
                self.stop(scope_key)
                await self._say(ctx, "行吧，那就不玩了。")
                return True
            return False
        if name in ("要", "要牌", "hit"):
            game = self.active(scope_key)
            if game and game.kind == "blackjack":
                await self._say(ctx, self._hit(scope_key, ctx))
                return True
            return False
        if name in ("停牌", "不要", "stand"):
            game = self.active(scope_key)
            if game and game.kind == "blackjack":
                await self._say(ctx, self._stand(scope_key, ctx))
                return True
            return False
        return False

    # ------------------------------------------------------------------
    async def intercept(self, event, ctx: "CommandContext", text: str | None = None) -> str | None:
        """普通聊天里猜答案 / 问海龟汤。返回要发的话，或 None 表示不管。"""
        scope_key = f"{event.scope_type}:{event.scope_id}"
        game = self.active(scope_key)
        if game is None:
            return None
        raw = text if text is not None else (getattr(event, "text", "") or "")
        raw = (raw or "").strip()
        if not raw:
            return None

        if game.kind == "word":
            return self._guess_word(game, raw, event)
        if game.kind == "soup":
            return await self._soup_question(game, raw)
        return None

    # ------------------------------------------------------------------
    # 猜词
    # ------------------------------------------------------------------
    async def _start_word(self, scope_key: str, ctx: "CommandContext") -> str:
        if self.active(scope_key):
            return "还有一局没玩完呢，先把这个猜完。"
        if not self._quota_left(scope_key):
            return "今天玩够多啦，明天再来。"
        category = self.rng.choice(list(WORDS))
        word = self.rng.choice(WORDS[category])
        self._games[scope_key] = Game(
            kind="word",
            scope_key=scope_key,
            state={"word": word, "category": category, "tries": 0, "used": [], "max": 8},
            initiator=ctx.user_qq,
            initiator_name=ctx.user_name,
        )
        self._count(scope_key)
        self.stats["started"] += 1
        return (
            f"猜词开始：{OPENERS[category]}，{len(word)} 个字，"
            f"第一个字是「{word[0]}」。最多猜 {8} 次。"
        )

    def _guess_word(self, game: Game, text: str, event) -> str | None:
        word = game.state["word"]
        guess = text.strip().strip("。.！!~～")
        if len(guess) > 6 or any(ch in guess for ch in "？?，,、\n"):
            return None  # 太长 / 像问句，交给正常聊天
        if guess in CHAT_NOISE:
            return None
        game.state["tries"] += 1
        game.state["used"].append(guess)
        self.stats["answered"] += 1

        if guess == word:
            self.stop(game.scope_key)
            self.stats["won"] += 1
            scope_type, _, scope_id = game.scope_key.partition(":")
            self.store.add_memory(
                scope_type,
                int(scope_id or 0),
                f"{event.sender_name or event.user_id} 猜中了「{word}」",
                kind="event",
                importance=1,
            )
            return f"对！就是「{word}」！{event.sender_name or ''} 还挺机灵。"
        if guess in word or (len(guess) >= 2 and word in guess):
            return "差一点点，再想想。"
        if game.state["tries"] >= game.state["max"]:
            self.stop(game.scope_key)
            return f"次数用完啦，答案是「{word}」。下次再来。"
        return "不对哦。"

    # ------------------------------------------------------------------
    # 21 点
    # ------------------------------------------------------------------
    def _new_deck(self) -> list[int]:
        deck = [n for n in range(1, 11) for _ in range(4)]
        self.rng.shuffle(deck)
        return deck

    async def _start_blackjack(self, scope_key: str, ctx: "CommandContext") -> str:
        if self.active(scope_key):
            return "这里已经在玩了，先收个尾。"
        if not self._quota_left(scope_key):
            return "今天玩够多啦。"
        deck = self._new_deck()
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]
        self._games[scope_key] = Game(
            kind="blackjack",
            scope_key=scope_key,
            state={"deck": deck, "player": player, "dealer": dealer},
            initiator=ctx.user_qq,
            initiator_name=ctx.user_name,
        )
        self._count(scope_key)
        self.stats["started"] += 1
        return (
            f"21 点开始。你：{player} = {sum(player)}；"
            f"我明牌 {dealer[0]}，暗牌扣着。\n发「/要」抽牌，「/停牌」停。"
        )

    def _hit(self, scope_key: str, ctx: "CommandContext") -> str:
        game = self._games[scope_key]
        deck = game.state["deck"]
        if not deck:
            return "牌发完了……这局算平局吧。"
        card = deck.pop()
        game.state["player"].append(card)
        total = sum(game.state["player"])
        if total > 21:
            self.stop(scope_key)
            return f"抽到 {card}，你爆了（{total}）。这局我赢。"
        if total == 21:
            return self._settle(scope_key, forced_win=True)
        return f"抽到 {card}，你现在 {total}。还要吗？"

    def _stand(self, scope_key: str, ctx: "CommandContext") -> str:
        return self._settle(scope_key)

    def _settle(self, scope_key: str, forced_win: bool = False) -> str:
        game = self._games[scope_key]
        deck = game.state["deck"]
        player = sum(game.state["player"])
        dealer_cards = game.state["dealer"]
        while sum(dealer_cards) < 17 and deck:
            dealer_cards.append(deck.pop())
        dealer = sum(dealer_cards)
        self.stop(scope_key)
        self.stats["answered"] += 1
        head = f"你 {player}，我 {dealer}（{dealer_cards}）。"
        if forced_win:
            return head + "21 点！你赢了。"
        if dealer > 21:
            return head + "我爆了，你赢。"
        if player > dealer:
            self.stats["won"] += 1
            return head + "你赢。"
        if player == dealer:
            return head + "平手。"
        return head + "我赢，嘿。"

    # ------------------------------------------------------------------
    # 海龟汤
    # ------------------------------------------------------------------
    async def _start_soup(self, scope_key: str, ctx: "CommandContext") -> str:
        if self.active(scope_key):
            return "先把这局玩完。"
        if not self._quota_left(scope_key):
            return "今天汤喝够了，明天再煮。"
        if self.llm is None or not getattr(self.llm, "available", False):
            return "今天脑子不太够，煮不了汤。"
        system = (
            "你出一道中文海龟汤。输出 JSON："
            '{"surface":"表面情境，30-60字","truth":"真相，30-60字"}。'
            "情境要反常、有悬念，但不要血腥恐怖。"
        )
        data = None
        try:
            data = await self.llm.complete_json(
                system, "来一道。", max_tokens=400, purpose="game", scope_key=scope_key
            )
        except LLMError:
            data = None
        if not data or not data.get("surface") or not data.get("truth"):
            return "汤锅坏了，换个时间再来吧。"
        self._games[scope_key] = Game(
            kind="soup",
            scope_key=scope_key,
            state={
                "surface": str(data["surface"])[:200],
                "truth": str(data["truth"])[:300],
                "asked": 0,
                "max": 15,
            },
            initiator=ctx.user_qq,
            initiator_name=ctx.user_name,
        )
        self._count(scope_key)
        self.stats["started"] += 1
        return (
            f"海龟汤：{data['surface']}\n"
            f"你可以提问，我只答「是」「不是」「无关」。最多 {15} 个问题，"
            "猜到了就说「/揭晓」。"
        )

    def _reveal_soup(self, scope_key: str, ctx: "CommandContext") -> str:
        game = self.active(scope_key)
        if not game or game.kind != "soup":
            return "现在没有汤可以揭。"
        truth = game.state["truth"]
        self.stop(scope_key)
        return f"真相是：{truth}"

    async def _soup_question(self, game: Game, text: str) -> str | None:
        if not text.endswith(("？", "?")) and len(text) > 20:
            return None
        game.state["asked"] += 1
        self.stats["answered"] += 1
        if game.state["asked"] > game.state["max"]:
            self.stop(game.scope_key)
            return "问题用完啦，可以打「/揭晓」看答案。"
        if self.llm is None or not getattr(self.llm, "available", False):
            return None
        system = (
            "你在主持海龟汤。只允许回答：是 / 不是 / 无关 / 接近了。"
            f"真相是：{game.state['truth']}\n"
            "玩家的问题如果和真相相关就按真相判断，完全不沾边回「无关」。"
            "只输出这四个词之一，不要解释。"
        )
        try:
            answer = await self.llm.complete(
                system, text, temperature=0.2, max_tokens=8, purpose="game",
                scope_key=game.scope_key,
            )
        except LLMError:
            return None
        answer = (answer or "").strip()[:6]
        if not answer:
            return None
        return answer

    # ------------------------------------------------------------------
    async def _say(self, ctx: "CommandContext", text: str) -> None:
        if not text:
            return
        await ctx.sender.send_text(
            ctx.scope_type, ctx.scope_id, text, quote=False, reason="game"
        )
