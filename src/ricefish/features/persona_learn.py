"""从聊天里认出「别人希望它有的设定」，写进后来学到的设定。

只认明确的祈使句（你要 / 记住 / 希望你 / 不要老是…），
不认问句、不认太长的话，避免把普通聊天写成规则。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

log = logging.getLogger("ricefish.persona_learn")

DEFAULT_PATH = Path("config/personas/learned.md")
MAX_BYTES = 8000

_PATTERNS = [
    re.compile(r"(?:记住|记一下|记着)[:：,，\s]*(.{4,80})"),
    re.compile(r"你(?:以后|下次|之后)((?:要|得|别).{2,80})"),
    re.compile(r"你(?:应该|必须|可以)(?:更|再)?(.{2,80})"),
    re.compile(r"希望你(?:以后)?(?:能|可以)?(.{2,80})"),
    re.compile(r"不要(?:老是|总是|再|一直)(.{2,80})"),
]

_QUESTION_MARKS = ("吗", "？", "?", "呢")

#: 太含糊、没有可执行含义的短语，别写进人设
_VAGUE = {
    "这个设定",
    "那个设定",
    "自己理解",
    "自己看着办",
    "看着办",
    "这个",
    "那个",
    "随便",
}


def extract(text: str) -> str | None:
    """从一句话里抽出「设定」部分，抽不到返回 None。"""
    text = (text or "").strip()
    if not text or len(text) > 120:
        return None
    if text.endswith(_QUESTION_MARKS):
        return None
    for pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        directive = match.group(1).strip("。！!，,~～ \t")
        directive = re.sub(r"[「」\"'“”]", "", directive).strip()
        if directive in _VAGUE:
            continue
        if 2 <= len(directive) <= 80:
            return directive
    return None


class PersonaLearner:
    def __init__(self, path: Path | str = DEFAULT_PATH, max_bytes: int = MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes

    def learn(self, text: str, who: str = "", *, scope: str = "") -> str | None:
        directive = extract(text)
        if not directive:
            return None
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                log.info("学到的设定太多了，跳过：%s", directive[:30])
                return None
            existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        except OSError:
            existing = ""
        if directive in existing:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = "" if existing.strip() else "# 后来学到的设定\n\n"
        stamp = time.strftime("%m-%d")
        who = (who or "群友")[:20]
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(header)
            handle.write(f"- {directive}（{who}，{stamp}）\n")
        log.info("学到一条设定：%s（来自 %s）", directive, who)
        return directive
