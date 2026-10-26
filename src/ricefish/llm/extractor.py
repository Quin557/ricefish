"""抽取器：把聊天记录变成结构化记忆。

两件事：
    1. extract()  抽取事实 / 人设补丁 / 情绪 → 写进 memories、profiles
    2. summarize() 生成滚动摘要 → 写进 summaries
都用便宜的 flash 模型，异步跑，失败不影响聊天。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..llm.client import DeepSeekClient, LLMError, load_json_object
from ..llm.prompt import PromptBuilder
from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.extractor")

VALID_KINDS = {"preference", "identity", "event", "relationship", "taboo", "meme", "fact"}
MAX_INPUT_CHARS = 6000


def format_messages(messages: list[dict], bot_name: str = "") -> str:
    lines: list[str] = []
    for item in messages:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if item.get("recalled"):
            text = "（撤回了这句话）"
        if item.get("is_bot"):
            who = f"{bot_name or '你'}（机器人）"
        else:
            who = f"{item.get('sender_name') or '某人'}({item.get('sender_qq')})"
        lines.append(f"{who}: {text}")
    text = "\n".join(lines)
    if len(text) > MAX_INPUT_CHARS:
        text = text[-MAX_INPUT_CHARS:]
    return text


class Extractor:
    def __init__(
        self,
        config: Config,
        store: MemoryStore,
        llm: DeepSeekClient,
        prompts: PromptBuilder,
    ) -> None:
        self.config = config
        self.store = store
        self.llm = llm
        self.prompts = prompts

    def _model(self) -> str:
        setting = (self.config.memory.extractor.model or "fast").lower()
        return self.config.llm.pro_model if setting == "pro" else self.config.llm.model

    async def extract(
        self,
        scope_type: str,
        scope_id: int,
        messages: list[dict],
        *,
        subject_qq: int = 0,
        subject_name: str = "",
    ) -> int:
        """返回写入的事实条数；0 表示没抽到或失败。"""
        if not messages:
            return 0
        history_text = format_messages(messages, self.config.bot.name)
        if not history_text.strip():
            return 0
        system, user = self.prompts.extract_prompt(history_text)
        scope_key = f"{scope_type}:{scope_id}"
        llm_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = None
        for max_tokens in (900, 1400):
            try:
                result = await self.llm.chat(
                    llm_messages,
                    model=self._model(),
                    temperature=0.2,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    purpose="extract",
                    scope_key=scope_key,
                    thinking="disabled",
                )
            except LLMError as exc:
                log.debug("抽取失败: %s", exc)
                return 0
            if result.finish_reason == "length":
                log.warning(
                    "抽取输出被截断（%s，reasoning=%s），加预算重试",
                    scope_key,
                    result.reasoning_chars,
                )
                continue
            data = load_json_object(result.text)
            break
        if not data:
            return 0
        return self._apply(scope_type, scope_id, data, subject_qq, subject_name, messages)

    def _apply(
        self,
        scope_type: str,
        scope_id: int,
        data: dict[str, Any],
        subject_qq: int,
        subject_name: str,
        messages: list[dict],
    ) -> int:
        added = 0
        msg_ids = ",".join(
            str(item.get("message_id") or item.get("id") or "") for item in messages[-5:]
        )

        facts = data.get("facts")
        if isinstance(facts, list):
            for fact in facts[:12]:
                if not isinstance(fact, dict):
                    continue
                content = str(fact.get("content") or "").strip()
                if len(content) < 2:
                    continue
                kind = str(fact.get("kind") or "fact").strip().lower()
                if kind not in VALID_KINDS:
                    kind = "fact"
                try:
                    importance = int(fact.get("importance") or 3)
                except (TypeError, ValueError):
                    importance = 3
                try:
                    confidence = max(0.0, min(1.0, float(fact.get("confidence") or 0.5)))
                except (TypeError, ValueError):
                    confidence = 0.5
                try:
                    fact_subject = int(fact.get("subject_qq") or 0)
                except (TypeError, ValueError):
                    fact_subject = 0
                if not fact_subject:
                    fact_subject = subject_qq
                self.store.add_memory(
                    scope_type,
                    scope_id,
                    content,
                    subject_qq=fact_subject,
                    kind=kind,
                    importance=importance,
                    confidence=confidence,
                    source_msg_ids=msg_ids,
                )
                added += 1

        loops = data.get("open_loops")
        if isinstance(loops, list):
            for loop in loops[:6]:
                if not isinstance(loop, dict):
                    continue
                content = str(loop.get("content") or "").strip()
                if len(content) < 2:
                    continue
                try:
                    loop_subject = int(loop.get("subject_qq") or 0)
                except (TypeError, ValueError):
                    loop_subject = 0
                if not loop_subject:
                    loop_subject = subject_qq
                kind = str(loop.get("kind") or "topic").strip().lower()
                self.store.add_open_loop(
                    scope_type,
                    scope_id,
                    content,
                    subject_qq=loop_subject,
                    kind=kind,
                    source_msg_ids=msg_ids,
                )

        episode = data.get("episode")
        episode_added = False
        if isinstance(episode, dict):
            episode_summary = str(episode.get("summary") or "").strip()
            if episode_summary:
                topics = episode.get("topics")
                if isinstance(topics, list):
                    topics_text = ",".join(str(item)[:20] for item in topics[:6])
                else:
                    topics_text = str(topics or "")[:200]
                ts = [int(item.get("ts") or 0) for item in messages if item.get("ts")]
                self.store.add_episode(
                    scope_type,
                    scope_id,
                    episode_summary,
                    subject_qq=subject_qq,
                    from_ts=min(ts) if ts else 0,
                    to_ts=max(ts) if ts else 0,
                    tone=str(episode.get("tone") or ""),
                    topics=topics_text,
                    msg_count=len(messages),
                )
                episode_added = True

        events = data.get("relationship_events")
        if isinstance(events, list):
            for event in events[:5]:
                if not isinstance(event, dict):
                    continue
                content = str(event.get("content") or "").strip()
                if len(content) < 2:
                    continue
                try:
                    event_subject = int(event.get("subject_qq") or 0)
                except (TypeError, ValueError):
                    event_subject = 0
                if not event_subject:
                    event_subject = subject_qq
                try:
                    event_importance = int(event.get("importance") or 3)
                except (TypeError, ValueError):
                    event_importance = 3
                self.store.add_relationship_event(
                    scope_type,
                    scope_id,
                    content,
                    subject_qq=event_subject,
                    kind=str(event.get("kind") or "event").lower(),
                    importance=event_importance,
                )

        patch = data.get("profile_patch")
        if isinstance(patch, dict) and patch:
            target = subject_qq or _guess_subject(messages)
            if target:
                clean = {k: v for k, v in patch.items() if isinstance(v, (str, int))}
                if subject_name and not clean.get("display_name"):
                    clean.pop("display_name", None)
                changed = self.store.apply_profile_patch(scope_type, scope_id, target, clean)
                if changed:
                    log.info("档案更新 %s:%s %s -> %s", scope_type, scope_id, target, changed)

        emotion = data.get("emotion")
        if isinstance(emotion, dict):
            try:
                target = int(emotion.get("target_qq") or subject_qq)
                delta = int(emotion.get("delta") or 0)
            except (TypeError, ValueError):
                target, delta = subject_qq, 0
            if target and delta:
                self.store.bump_affinity(scope_type, scope_id, target, max(-10, min(10, delta)))

        summary = str(data.get("summary") or "").strip()
        if summary:
            ts = [int(item.get("ts") or 0) for item in messages if item.get("ts")]
            self.store.add_summary(
                scope_type,
                scope_id,
                summary,
                from_ts=min(ts) if ts else 0,
                to_ts=max(ts) if ts else 0,
                msg_count=len(messages),
            )
            if not episode_added:
                self.store.add_episode(
                    scope_type,
                    scope_id,
                    summary,
                    subject_qq=subject_qq,
                    from_ts=min(ts) if ts else 0,
                    to_ts=max(ts) if ts else 0,
                    msg_count=len(messages),
                )
        return added

    async def summarize(
        self, scope_type: str, scope_id: int, messages: list[dict]
    ) -> str:
        history_text = format_messages(messages, self.config.bot.name)
        if len(history_text) < 30:
            return ""
        system, user = self.prompts.summary_prompt(history_text)
        scope_key = f"{scope_type}:{scope_id}"
        llm_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for max_tokens in (800, 1200):
            try:
                result = await self.llm.chat(
                    llm_messages,
                    model=self._model(),
                    temperature=0.3,
                    max_tokens=max_tokens,
                    purpose="summary",
                    scope_key=scope_key,
                    thinking="disabled",
                )
            except LLMError as exc:
                log.debug("摘要失败: %s", exc)
                return ""
            if result.finish_reason == "length":
                log.warning(
                    "摘要输出被截断（%s，reasoning=%s），加预算重试",
                    scope_key,
                    result.reasoning_chars,
                )
                continue
            text = (result.text or "").strip()
            if not text:
                return ""
            ts = [int(item.get("ts") or 0) for item in messages if item.get("ts")]
            self.store.add_summary(
                scope_type,
                scope_id,
                text,
                from_ts=min(ts) if ts else 0,
                to_ts=max(ts) if ts else 0,
                msg_count=len(messages),
            )
            self.store.add_episode(
                scope_type,
                scope_id,
                text,
                subject_qq=0,
                from_ts=min(ts) if ts else 0,
                to_ts=max(ts) if ts else 0,
                msg_count=len(messages),
            )
            return text
        log.warning("摘要连续两次被截断，本次不落库：%s", scope_key)
        return ""


def _guess_subject(messages: list[dict]) -> int:
    """没人指定主体时，取最近一条人类消息的发送者。"""
    for item in reversed(messages):
        if not item.get("is_bot") and item.get("sender_qq"):
            return int(item["sender_qq"])
    return 0
