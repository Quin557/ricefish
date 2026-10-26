"""表情收件箱：把群里发过的图悄悄存下来，之后 `/收表情` 随手收。

只存索引和文件，不依赖数据库；超过上限自动删最旧的。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("ricefish.emoji_inbox")

DIR = Path("data/emoji-inbox")
INDEX = DIR / "index.json"
MAX_ENTRIES = 300

_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _load() -> list[dict]:
    try:
        raw = INDEX.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(json.dumps(items[-MAX_ENTRIES:], ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.debug("写表情索引失败：%s", exc)


def latest(count: int = 1) -> list[dict]:
    """最近收进来的图（新的在后）。"""
    items = _load()
    return items[-count:] if count > 0 else []


def latest_for_scope(scope: str, count: int = 1) -> list[dict]:
    """某个会话最近收进来的图；找不到再退回全局最新。"""
    items = [item for item in _load() if str(item.get("scope") or "") == scope]
    if not items:
        return latest(count)
    return items[-count:] if count > 0 else []


def get(item_id: str) -> Path | None:
    # 绝对路径：NapCat 发图按 file:// 打开，相对路径会失败
    path = (DIR / item_id).resolve()
    return path if path.exists() else None


def _suffix(name: str) -> str:
    lowered = (name or "").lower().split("?")[0]
    for suffix in _SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    return ".png"


def _download(url: str, timeout: float = 12.0) -> bytes | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read() or None
    except Exception:  # noqa: BLE001
        log.debug("下载图片失败 %s", url)
        return None


async def capture(
    client,
    file_id: str,
    *,
    sender: str = "",
    qq: int = 0,
    scope: str = "",
) -> str | None:
    """把一张图存进收件箱，返回保存路径。失败返回 None。"""
    file_id = (file_id or "").strip()
    if not file_id:
        return None
    url, local = file_id, ""
    if not file_id.startswith(("http://", "https://", "base64://")):
        info = None
        if client is not None:
            try:
                info = await client.get_image(file_id)
            except Exception:  # noqa: BLE001
                info = None
        if isinstance(info, dict):
            url = str(info.get("url") or "")
            local = str(info.get("file") or "")

    data: bytes | None = None
    if file_id.startswith("base64://"):
        import base64

        try:
            data = base64.b64decode(file_id[9:])
        except Exception:  # noqa: BLE001
            data = None
    elif url.startswith(("http://", "https://")):
        data = await asyncio.to_thread(_download, url)
    if data is None and local and Path(local).exists():
        data = Path(local).read_bytes()
    if not data:
        return None
    if len(data) > 10_000_000:
        log.info("图片太大（%.1fMB），不收进表情箱", len(data) / 1_000_000)
        return None

    DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(data).hexdigest()[:16]
    filename = digest + _suffix(url or local)
    # 绝对路径：NapCat 发图时按 file:// 打开，相对路径会失败
    path = (DIR / filename).resolve()
    if not path.exists():
        path.write_bytes(data)
    items = _load()
    if any(item.get("id") == filename for item in items):
        return str(path)
    items.append(
        {
            "id": filename,
            "sender": (sender or "")[:20],
            "qq": int(qq or 0),
            "scope": scope[:40],
            "ts": int(time.time()),
        }
    )
    if len(items) > MAX_ENTRIES:
        for dropped in items[:-MAX_ENTRIES]:
            old = DIR / str(dropped.get("id") or "")
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
        items = items[-MAX_ENTRIES:]
    _save(items)
    return str(path)
