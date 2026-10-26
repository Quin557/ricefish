"""表情包映射表：名字 -> 本地图片路径。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .. import yamlish

log = logging.getLogger("ricefish.emoji")

EMOJI_DIR = Path("assets/emoji")
MAPPING_PATH = Path("config/emoji.yaml")

#: 名字里只留中英文、数字、下划线，避免路径穿越
_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fa5]+")

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class EmojiBook:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping: dict[str, str] = mapping or {}

    @classmethod
    def load(cls, path: str = "config/emoji.yaml") -> "EmojiBook":
        raw = yamlish.load_file(path, default={}) or {}
        if not isinstance(raw, dict):
            return cls()
        mapping: dict[str, str] = {}
        missing: list[str] = []
        for name, value in raw.items():
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = Path(value.strip())
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            if candidate.exists():
                mapping[str(name)] = str(candidate)
            else:
                missing.append(str(name))
        if missing:
            log.info("表情文件缺失，已跳过：%s", ", ".join(missing))
        return cls(mapping)

    def get(self, name: str) -> str | None:
        return self.mapping.get(name)

    def names(self) -> list[str]:
        return list(self.mapping.keys())

    def __contains__(self, name: object) -> bool:
        return name in self.mapping

    def __len__(self) -> int:
        return len(self.mapping)

    # -- 收纳新表情 --------------------------------------------------
    @staticmethod
    def safe_name(name: str, fallback: str = "表情") -> str:
        cleaned = _SAFE_NAME.sub("", (name or "").strip())[:12]
        return cleaned or fallback

    def add(self, name: str, source: Path, *, persist: bool = True) -> str:
        """把一张图收进表情库：复制到 assets/emoji，写进映射表。

        同名会覆盖；返回最终文件路径。
        """
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = ".png"
        safe = self.safe_name(name)
        already = safe in self.mapping
        EMOJI_DIR.mkdir(parents=True, exist_ok=True)
        # 绝对路径：NapCat 拼 file:// URI 时不认相对路径
        target = (EMOJI_DIR / f"{safe}{suffix}").resolve()
        target.write_bytes(source.read_bytes())
        self.mapping[safe] = str(target)
        if persist and not already:
            try:
                MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
                with MAPPING_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(f'{safe}: "{target.as_posix()}"\n')
            except OSError as exc:
                log.warning("写表情映射失败：%s", exc)
        return str(target)
