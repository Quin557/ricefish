"""梗图包：读取 assets/memes/manifest.json。

和表情包的区别：表情包按情绪名映射（[表情:开心]），梗图按编号随机发（/梗图），
图片本身通常带台词，不参与情绪判断。
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("ricefish.memes")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@dataclass(frozen=True)
class Meme:
    id: str
    caption: str
    path: str
    source_url: str = ""


class MemeBook:
    def __init__(self, items: list[Meme] | None = None, source: str = "") -> None:
        self.items: list[Meme] = items or []
        self.source = source

    @classmethod
    def load(cls, directory: str = "assets/memes") -> "MemeBook":
        if not directory.strip():
            return cls()
        base = Path(directory.strip())
        if not base.is_absolute():
            base = Path.cwd() / base
        manifest = base / "manifest.json"
        if not manifest.is_file():
            return cls()
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            log.warning("梗图 manifest 读取失败：%s", exc)
            return cls()
        rows = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            return cls()
        items: list[Meme] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("file") or "").strip()
            if not name:
                continue
            candidate = Path(name)
            if not candidate.is_absolute():
                candidate = base / candidate
            # 兼容 manifest 里写项目根相对路径（如 assets/memes/010.png）
            if not candidate.is_file() and not Path(name).is_absolute():
                candidate = Path.cwd() / name
            if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            meme_id = str(row.get("id") or "").strip().lstrip("#")
            if not meme_id:
                meme_id = candidate.stem
            items.append(
                Meme(
                    id=meme_id,
                    caption=str(row.get("caption") or "").strip(),
                    path=str(candidate),
                    source_url=str(row.get("source_url") or "").strip(),
                )
            )
        if items:
            log.debug("已载入梗图包 %s（%d 张）", base, len(items))
        return cls(items, source=str(base))

    @property
    def enabled(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, meme_id: object) -> bool:
        return any(item.id == str(meme_id).lstrip("#") for item in self.items)

    def find(self, meme_id: str) -> Meme | None:
        wanted = str(meme_id).strip().lstrip("#")
        for item in self.items:
            if item.id == wanted:
                return item
        return None

    def pick(self, meme_id: str = "", rng: random.Random | None = None) -> Meme | None:
        if meme_id:
            return self.find(meme_id)
        if not self.items:
            return None
        return (rng or random).choice(self.items)

    def listing(self, limit: int = 30) -> list[str]:
        return [f"{item.id}  {item.caption}".strip() for item in self.items[:limit]]
