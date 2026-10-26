"""图片理解：把一张图变成模型能接话的「图片卡」。

这里只做一件事：取字节 → 让视觉模型按固定 JSON 输出 → 容错解析。
缓存、并发、注入提示词由 dispatcher 负责。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("ricefish.vision")

MAX_IMAGE_BYTES = 10_000_000

VISION_SYSTEM_PROMPT = """你是 QQ 聊天机器人的视觉模块。看一张聊天里发来的图，
输出一个 JSON 对象，不要输出任何别的文字。字段：
{
  "kind": "meme|screenshot|photo|chart|product|other",
  "summary": "一句话客观描述画面，40 字以内",
  "ocr": "图上可见的文字，原样抄写；没有就空字符串",
  "entities": ["画面里的主要人物/物体/作品名，最多 5 个"],
  "meme": "如果是表情包或梗图，解释梗和常见用法；不是就空字符串",
  "emotion": "这张图传递的情绪，比如开心/无语/震惊/撒娇",
  "reply_angles": ["看到这张图可以接的 2~3 句话，短、口语"],
  "safety": "ok|nsfw|unknown"
}
看不清、不确定的字段就留空，不要编造。不要输出 Markdown 代码块。"""


@dataclass
class ImageCard:
    kind: str = "other"
    summary: str = ""
    ocr: str = ""
    entities: list[str] = field(default_factory=list)
    meme: str = ""
    emotion: str = ""
    reply_angles: list[str] = field(default_factory=list)
    safety: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "summary": self.summary,
            "ocr": self.ocr,
            "entities": list(self.entities),
            "meme": self.meme,
            "emotion": self.emotion,
            "reply_angles": list(self.reply_angles),
            "safety": self.safety,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImageCard":
        reply_angles = data.get("reply_angles") or []
        if isinstance(reply_angles, str):
            try:
                reply_angles = json.loads(reply_angles)
            except (TypeError, ValueError):
                reply_angles = []
        entities = data.get("entities") or []
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except (TypeError, ValueError):
                entities = []
        return cls(
            kind=str(data.get("kind") or "other"),
            summary=str(data.get("summary") or ""),
            ocr=str(data.get("ocr") or ""),
            entities=[str(x) for x in entities if str(x).strip()][:5],
            meme=str(data.get("meme") or ""),
            emotion=str(data.get("emotion") or ""),
            reply_angles=[str(x) for x in reply_angles if str(x).strip()][:3],
            safety=str(data.get("safety") or "unknown"),
        )

    def to_prompt(self) -> str:
        lines: list[str] = []
        if self.summary:
            lines.append(f"画面：{self.summary}")
        if self.ocr:
            lines.append(f"图上的字：「{self.ocr[:160]}」")
        kind_label = {
            "meme": "表情包/梗图",
            "screenshot": "截图",
            "photo": "照片",
            "chart": "图表",
            "product": "商品图",
        }.get(self.kind, "图片")
        extra = f"（梗：{self.meme}）" if self.meme else ""
        lines.append(f"像是：{kind_label}{extra}")
        if self.emotion:
            lines.append(f"情绪：{self.emotion}")
        angles = [item for item in self.reply_angles if item][:3]
        if angles:
            lines.append("可以接：" + " / ".join(angles))
        return "\n".join(lines)


def guess_mime(data: bytes, hint: str = "") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    low = (hint or "").lower().split("?")[0]
    for suffix, mime in (
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".gif", "image/gif"),
        (".webp", "image/webp"),
        (".bmp", "image/bmp"),
    ):
        if low.endswith(suffix):
            return mime
    return "image/png"


def data_uri(data: bytes, mime: str = "") -> str:
    mime = mime or guess_mime(data)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def shrink_image(
    data: bytes, mime: str = "", max_side: int = 1600, max_bytes: int = 4_000_000
) -> tuple[bytes, str]:
    """大图缩到适合视觉接口的尺寸；Pillow 不在就原样返回。"""
    if not data:
        return data, mime
    if len(data) <= max_bytes and mime not in ("image/bmp",):
        return data, mime or guess_mime(data)
    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return data, mime or guess_mime(data)
    try:
        image = Image.open(io.BytesIO(data))
        image.thumbnail((max_side, max_side))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        buffer = io.BytesIO()
        if image.mode == "RGBA":
            image.save(buffer, format="PNG", optimize=True)
            out_mime = "image/png"
        else:
            image.save(buffer, format="JPEG", quality=86, optimize=True)
            out_mime = "image/jpeg"
        out = buffer.getvalue()
        return (out, out_mime) if len(out) < len(data) else (data, mime or guess_mime(data))
    except Exception:  # noqa: BLE001
        return data, mime or guess_mime(data)


def _download(url: str, timeout: float = 15.0) -> bytes | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read() or None
    except Exception:  # noqa: BLE001
        log.debug("下载图片失败 %s", url[:80], exc_info=True)
        return None


async def load_image_bytes(client, file: str, *, timeout: float = 15.0) -> tuple[bytes, str] | None:
    """把 OneBot 的 file/url/base64/本地路径统一成 (bytes, mime)。"""
    file = (file or "").strip()
    if not file:
        return None
    if file.startswith("base64://"):
        try:
            data = base64.b64decode(file[9:])
        except Exception:  # noqa: BLE001
            return None
        return (data, guess_mime(data)) if data else None
    if file.startswith("data:"):
        try:
            header, _, payload = file.partition(",")
            data = base64.b64decode(payload)
        except Exception:  # noqa: BLE001
            return None
        mime = header[5:].split(";")[0] or guess_mime(data)
        return (data, mime) if data else None

    url = file
    local = ""
    if not file.startswith(("http://", "https://")):
        if os.path.exists(file):
            local = file
        elif client is not None:
            info = None
            try:
                info = await client.get_image(file)
            except Exception:  # noqa: BLE001
                info = None
            if isinstance(info, dict):
                url = str(info.get("url") or "")
                local = str(info.get("file") or "")

    data: bytes | None = None
    if local:
        try:
            with open(local, "rb") as handle:
                data = handle.read()
        except OSError:
            data = None
    if data is None and url.startswith(("http://", "https://")):
        data = await asyncio.to_thread(_download, url, timeout)
    if not data:
        return None
    if len(data) > MAX_IMAGE_BYTES:
        log.info("图片太大（%.1fMB），跳过理解", len(data) / 1_000_000)
        return None
    return data, guess_mime(data, url or local)


def _crop(data: bytes, region: str) -> bytes:
    """可选裁剪：装了 Pillow 就按区域放大，没装就返回原图。"""
    region = (region or "full").lower()
    if region in ("", "full", "all"):
        return data
    try:
        import io

        from PIL import Image
    except Exception:  # noqa: BLE001
        return data
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        boxes = {
            "left": (0, 0, width // 2, height),
            "right": (width // 2, 0, width, height),
            "top": (0, 0, width, height // 2),
            "bottom": (0, height // 2, width, height),
            "center": (width // 4, height // 4, width * 3 // 4, height * 3 // 4),
        }
        box = boxes.get(region)
        if not box:
            return data
        buffer = io.BytesIO()
        image.crop(box).save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return data


async def ask_image(
    llm: Any,
    data: bytes,
    mime: str,
    question: str,
    *,
    region: str = "full",
    scope_key: str = "",
) -> str:
    """对同一张图再看一眼：按问题放大某个区域，返回一句回答。"""
    data = _crop(data, region)
    data, mime = shrink_image(data, mime)
    config = getattr(llm, "config", None)
    model = getattr(config, "vision_model", None) or None
    messages = [
        {
            "role": "system",
            "content": "你是聊天机器人的视觉助手。只回答对方的问题，简短、具体；"
            "看不清就直说，不要编。不要 Markdown。",
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri(data, mime)}},
                {"type": "text", "text": question or "这张图里最关键/最容易被忽略的细节是什么？"},
            ],
        },
    ]
    try:
        result = await llm.chat(
            messages,
            model=model,
            temperature=0.2,
            max_tokens=500,
            purpose="vision",
            scope_key=scope_key,
            thinking="disabled",
        )
    except Exception:  # noqa: BLE001
        return "没看清，图好像打不开。"
    return (result.text or "").strip()[:300]


def parse_card(text: str) -> ImageCard | None:
    """从模型输出里抠 JSON；容忍 ```json 包裹和前后废话。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    card = ImageCard()
    card.kind = str(data.get("kind") or "other")[:20]
    card.summary = str(data.get("summary") or "")[:200]
    card.ocr = str(data.get("ocr") or "")[:300]
    entities = data.get("entities")
    if isinstance(entities, list):
        card.entities = [str(item)[:30] for item in entities if str(item).strip()][:5]
    card.meme = str(data.get("meme") or "")[:200]
    card.emotion = str(data.get("emotion") or "")[:40]
    angles = data.get("reply_angles")
    if isinstance(angles, list):
        card.reply_angles = [str(item)[:60] for item in angles if str(item).strip()][:3]
    safety = str(data.get("safety") or "unknown").lower()
    card.safety = safety if safety in ("ok", "nsfw", "unknown") else "unknown"
    if not card.summary and not card.ocr and not card.meme:
        return None
    return card


async def analyze_image(
    llm: Any,
    data: bytes,
    mime: str,
    *,
    scope_key: str = "",
    ocr_hint: str = "",
) -> ImageCard | None:
    """走主 LLM 的视觉接口；失败返回 None，调用方按「没看清」处理。"""
    if not data:
        return None
    data, mime = shrink_image(data, mime)
    config = getattr(llm, "config", None)
    model = getattr(config, "vision_model", None) or None
    max_tokens = int(getattr(config, "vision_max_tokens", 700) or 700)
    messages = [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri(data, mime)}},
                {
                    "type": "text",
                    "text": "看这张聊天图片，按 JSON 输出。"
                    + (f"\nOCR 参考：{ocr_hint[:300]}" if ocr_hint else ""),
                },
            ],
        },
    ]
    try:
        result = await llm.chat(
            messages,
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
            purpose="vision",
            scope_key=scope_key,
            thinking="disabled",
        )
    except Exception:  # noqa: BLE001
        log.debug("视觉调用失败", exc_info=True)
        return None

    card = parse_card(result.text)
    if card is None and (result.text or "").strip():
        # 模型没按 JSON 回，就把文本当摘要，至少别丢信息
        card = ImageCard(summary=str(result.text).strip()[:200], kind="other")
    if card is None and result.finish_reason == "length":
        log.info("视觉输出被截断（reasoning=%s），本张图跳过", result.reasoning_chars)
    if card is not None and ocr_hint and not card.ocr:
        card.ocr = ocr_hint.strip()[:300]
    return card
