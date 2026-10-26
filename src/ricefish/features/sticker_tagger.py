"""按需给表情库打标：只处理还没有情绪/标签的图，一次少量，避免烧钱。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..vision.cards import analyze_image, guess_mime

log = logging.getLogger("ricefish.stickers")


async def tag_pending(app, limit: int = 5) -> int:
    try:
        rows = app.store.list_stickers(include_disabled=True, limit=500)
    except Exception:
        return 0
    pending = [
        row
        for row in rows
        if not str(row.get("emotion") or "").strip()
        and not str(row.get("tags") or "").strip()
    ]
    tagged = 0
    for row in pending[: max(1, min(20, int(limit or 5)))]:
        path = Path(str(row.get("path") or ""))
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            card = await analyze_image(
                app.llm, data, guess_mime(data, str(path)), scope_key="sticker-tag"
            )
        except Exception:
            log.debug("表情打标失败", exc_info=True)
            continue
        if card is None:
            continue
        tags = ",".join(
            [item for item in [card.kind, *card.entities] if item][:6]
        )
        app.store.update_sticker_tags(
            int(row["id"]),
            emotion=card.emotion or "",
            tags=tags,
            nsfw=1 if card.safety == "nsfw" else 0,
        )
        tagged += 1
    return tagged
