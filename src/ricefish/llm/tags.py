"""解析模型回复里的内联标记。

约定：
    [动作:挠头]   -> 转成文字 "（挠头）"
    [表情:开心]   -> 查表情包表，发一张图（查不到就丢掉）
    [图:xxx.png]  -> 直接发文件
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TAG_RE = re.compile(r"[\[【](表情|动作|图|动作表情|语音)[:：]\s*([^\]】]{1,24})[\]】]")

_STRIP_MD = [
    re.compile(r"\*\*(.+?)\*\*", re.S),
    re.compile(r"`([^`]*)`"),
]


@dataclass
class ParsedReply:
    text: str
    images: list[str] = field(default_factory=list)
    tags: list[tuple[str, str]] = field(default_factory=list)
    #: [语音:type] 指定的语音包分类（greet/sad/happy…），空表示不发语音
    voice: str = ""

    @property
    def empty(self) -> bool:
        return not self.text.strip() and not self.images


def parse_reply(raw: str, emoji_map: dict[str, str] | None = None) -> ParsedReply:
    """把模型输出变成可发送的文本 + 图片列表。"""
    emoji_map = emoji_map or {}
    images: list[str] = []
    tags: list[tuple[str, str]] = []
    actions: list[str] = []
    voice = ""

    def replace(match: re.Match) -> str:
        nonlocal voice
        kind, name = match.group(1), match.group(2).strip()
        tags.append((kind, name))
        if kind in ("动作", "动作表情"):
            actions.append(name)
            return ""
        if kind == "表情":
            path = emoji_map.get(name) or emoji_map.get(name.strip("[]【】"))
            if path:
                images.append(path)
            return ""
        if kind == "图":
            images.append(name)
            return ""
        if kind == "语音":
            voice = name
            return ""
        return ""

    text = TAG_RE.sub(replace, raw or "")
    text = _clean(text)
    if actions:
        # 动作放到句尾，最多一个
        text = (text.rstrip() + f"（{actions[0]}）").strip()
    return ParsedReply(text=text, images=images, tags=tags, voice=voice)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    for pattern in _STRIP_MD:
        text = pattern.sub(r"\1", text)
    # 去掉模型偶尔加的引号包裹
    if len(text) >= 2 and text[0] in "「“\"" and text[-1] in "」”\"":
        text = text[1:-1].strip()
    # 去掉常见的自我说明前缀
    text = re.sub(r"^(回复|回答|小满[:：]|assistant[:：])\s*[:：]?\s*", "", text)
    # 合并多余空行，QQ 单条消息不需要换行结构
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def strip_tags(raw: str) -> str:
    """只去标记，不处理映射（用于日志/摘要）。"""
    return TAG_RE.sub("", raw or "").strip()
