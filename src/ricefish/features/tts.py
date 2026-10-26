"""实时语音合成（TTS）。

优先用 edge-tts（微软在线语音，免费、不用 Key、中文自然），
没装就安静降级——聊天不会因为没语音挂掉。

为了让声音别像念课文：
1. 按标点切句，逐句合成；
2. 疑问句音调抬高、语速放慢，感叹句加快、抬高，省略号放慢压低；
3. 长句稍微提速，短句保持原速；
4. 句子之间靠标点自带的停顿，再把分句 MP3 顺序拼起来。

合成结果按内容哈希缓存，同一句只花一次网络。
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

log = logging.getLogger("ricefish.tts")

CACHE_DIR = Path("data/tts")
MAX_CACHE_FILES = 300

#: 太长的一句话不切，避免碎成很多段
SEGMENT_MAX_CHARS = 60

_SENTENCE_RE = re.compile(r"[^。！？!?…\n]*[。！？!?…\n]+|[^。！？!?…\n]+$")

_END_QUESTION = ("？", "?")
_END_EXCLAIM = ("！", "!")
_END_ELLIPSIS = ("…", "...")


def available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except Exception:  # pragma: no cover - 取决于环境
        return False
    return True


# -- 参数解析 --------------------------------------------------------
def _parse_percent(value: str) -> int:
    try:
        return int(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0


def _parse_hz(value: str) -> int:
    try:
        return int(str(value).strip().lower().rstrip("hz"))
    except (TypeError, ValueError):
        return 0


def _fmt_percent(value: int) -> str:
    return f"{value:+d}%"


def _fmt_hz(value: int) -> str:
    return f"{value:+d}Hz"


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_RE.findall(text or "") if part.strip()]
    merged: list[str] = []
    for part in parts:
        # 只在上一句短得离谱时并进去，正常句子保持独立，语调才有起伏
        if merged and len(merged[-1]) < 4:
            merged[-1] += part
        else:
            merged.append(part)
    return merged or [text]


def _prosody(sentence: str, base_rate: int, base_pitch: int, amount: float) -> tuple[str, str]:
    """按句子类型给这一句微调语速和音调。"""
    rate, pitch = base_rate, base_pitch
    tail = sentence.rstrip()
    if tail.endswith(_END_QUESTION):
        pitch += round(9 * amount)
        rate -= round(5 * amount)
    elif tail.endswith(_END_EXCLAIM):
        rate += round(9 * amount)
        pitch += round(7 * amount)
    elif tail.endswith(_END_ELLIPSIS):
        rate -= round(9 * amount)
        pitch -= round(5 * amount)
    if len(sentence) > 36:
        rate += round(6 * amount)
    elif len(sentence) <= 8:
        rate -= round(3 * amount)
    return _fmt_percent(rate), _fmt_hz(pitch)


# -- 合成 ------------------------------------------------------------
async def synthesize(
    text: str,
    voice: str = "zh-CN-XiaoyiNeural",
    rate: str = "+6%",
    volume: str = "+0%",
    pitch: str = "+10Hz",
    expressiveness: float = 0.7,
) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        import edge_tts
    except Exception:
        log.info("没装 edge-tts，跳过实时合成（pip install edge-tts 可开启）")
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    amount = max(0.0, min(1.0, float(expressiveness or 0)))
    key = hashlib.sha1(
        f"{voice}|{rate}|{volume}|{pitch}|{amount:.2f}|{text}".encode("utf-8")
    ).hexdigest()[:16]
    # 必须给 NapCat 绝对路径，否则 file:///data/tts/... 打不开
    output = (CACHE_DIR / f"{key}.mp3").resolve()
    if output.exists() and output.stat().st_size > 0:
        return str(output)

    sentences = _split_sentences(text[:300]) if amount > 0 else [text[:300]]
    if len(sentences) == 1:
        rate_i, pitch_i = _parse_percent(rate), _parse_hz(pitch)
        return await _synthesize_single(
            edge_tts, sentences[0], voice, _fmt_percent(rate_i), volume, _fmt_hz(pitch_i), output
        )

    base_rate, base_pitch = _parse_percent(rate), _parse_hz(pitch)
    chunks: list[bytes] = []
    for sentence in sentences:
        sentence_rate, sentence_pitch = _prosody(
            sentence, base_rate, base_pitch, amount
        )
        data = await _synthesize_bytes(
            edge_tts, sentence, voice, sentence_rate, volume, sentence_pitch
        )
        if data:
            chunks.append(data)
    if not chunks:
        return None
    try:
        output.write_bytes(b"".join(chunks))
    except OSError as exc:
        log.warning("拼接语音失败：%s", exc)
        return None
    _cleanup()
    return str(output) if output.exists() and output.stat().st_size > 0 else None


async def _synthesize_single(edge_tts, text, voice, rate, volume, pitch, output: Path) -> str | None:
    temp = output.with_suffix(".part")
    try:
        communicate = edge_tts.Communicate(
            text, voice, rate=rate, volume=volume, pitch=pitch
        )
        await communicate.save(str(temp))
        temp.replace(output)
    except Exception as exc:  # 网络/鉴权/参数问题都走这里
        log.warning("TTS 合成失败：%s", exc)
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    _cleanup()
    return str(output) if output.exists() and output.stat().st_size > 0 else None


async def _synthesize_bytes(edge_tts, text, voice, rate, volume, pitch) -> bytes | None:
    """分句合成；分句结果单独缓存，改一句不用重合成整段。"""
    try:
        key = hashlib.sha1(
            f"{voice}|{rate}|{volume}|{pitch}|{text}".encode("utf-8")
        ).hexdigest()[:16]
        path = (CACHE_DIR / f"seg-{key}.mp3").resolve()
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        temp = path.with_suffix(".part")
        communicate = edge_tts.Communicate(
            text, voice, rate=rate, volume=volume, pitch=pitch
        )
        await communicate.save(str(temp))
        temp.replace(path)
        _cleanup()
        return path.read_bytes() if path.exists() else None
    except Exception as exc:
        log.warning("分句合成失败：%s", exc)
        return None


def _cleanup() -> None:
    """缓存太大时删掉最旧的一批。"""
    try:
        files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    if len(files) <= MAX_CACHE_FILES:
        return
    for path in files[: len(files) - MAX_CACHE_FILES]:
        try:
            path.unlink()
        except OSError:
            pass
