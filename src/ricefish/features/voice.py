"""语音能力。

分两层，都可以单独关掉：

1. 本地语音包（默认开）：``assets/voices`` 里的预合成 WAV + manifest，
   离线播放、不调用任何 TTS 服务。适合固定台词（摸头、待机）。
2. 外部 TTS / ASR（默认关）：填一条命令模板，本项目负责调它、收结果。

TTS（文字转语音）：
    tts_command: 'edge-tts --voice zh-CN-XiaoyiNeural --text "{text}" --write-media "{output}"'
    # 或者用 gpt-sovits / 各种本地 TTS，只要最后产出一个音频文件

ASR（语音转文字）：
    asr_command: 'whisper "{input}" --output_format txt --output_dir "{outdir}"'

命令在项目根目录下用 shell 执行，超时 60 秒。不填就当作没开启。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from . import tts as _tts

log = logging.getLogger("ricefish.voice")

AUDIO_SUFFIXES = {".mp3", ".wav", ".silk", ".amr", ".ogg", ".m4a", ".flac"}


@dataclass(frozen=True)
class VoiceLine:
    """语音包里的一句预合成台词。"""

    id: int
    kind: str
    text: str
    path: str


class VoicePack:
    """本地预合成语音包。

    目录里放一个 ``manifest.json`` + 若干音频文件，格式见 ``assets/voices``。
    没有音频、没有 manifest、或者路径不存在时都安静地降级为空包，不影响聊天。
    """

    def __init__(self, lines: list[VoiceLine] | None = None, source: str = "") -> None:
        self.lines: list[VoiceLine] = lines or []
        self.source = source

    @classmethod
    def load(cls, directory: str = "assets/voices") -> "VoicePack":
        """加载一个或多个语音包目录，逗号/分号分隔；不存在的目录自动跳过。"""
        parts = [part for part in re.split(r"[,;，；]", directory or "") if part.strip()]
        lines: list[VoiceLine] = []
        sources: list[str] = []
        for part in parts:
            found = cls._load_one(part.strip())
            if found:
                lines.extend(found)
                sources.append(str(Path(part.strip())))
        return cls(lines, source=", ".join(sources))

    @classmethod
    def _load_one(cls, directory: str) -> list[VoiceLine]:
        base = Path(directory)
        if not base.is_absolute():
            base = Path.cwd() / base
        manifest = base / "manifest.json"
        if not manifest.is_file():
            return []
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            log.warning("语音包 manifest 读取失败：%s", exc)
            return []
        items = raw.get("lines") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return []
        lines: list[VoiceLine] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("file") or "").strip()
            if not name:
                continue
            candidate = Path(name)
            if not candidate.is_absolute():
                candidate = base / candidate
            # 兼容上游把 file 写成 "voices/line-00.wav" 的格式
            if not candidate.is_file():
                fallback = base / candidate.name
                candidate = fallback if fallback.is_file() else candidate
            if not candidate.is_file() or candidate.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            try:
                line_id = int(item.get("id"))
            except (TypeError, ValueError):
                line_id = len(lines)
            lines.append(
                VoiceLine(
                    id=line_id,
                    kind=str(item.get("kind") or "idle").strip() or "idle",
                    text=str(item.get("text") or "").strip(),
                    path=str(candidate),
                )
            )
        if lines:
            log.info("已载入语音包 %s（%d 句）", base, len(lines))
        return lines

    @property
    def enabled(self) -> bool:
        return bool(self.lines)

    def kinds(self) -> list[str]:
        return sorted({line.kind for line in self.lines})

    def find(self, kind: str | None = None) -> list[VoiceLine]:
        if not kind:
            return list(self.lines)
        return [line for line in self.lines if line.kind == kind]

    def pick(self, kind: str | None = None, rng: random.Random | None = None) -> VoiceLine | None:
        pool = self.find(kind)
        if not pool:
            return None
        return (rng or random).choice(pool)


class Voice:
    def __init__(self, config: Config, pack: VoicePack | None = None) -> None:
        self.config = config
        self.pack = pack if pack is not None else VoicePack.load(config.tools.voice_pack)

    @property
    def tts_enabled(self) -> bool:
        if self.config.tools.tts_command.strip():
            return True
        return _tts.available()

    @property
    def asr_enabled(self) -> bool:
        return bool(self.config.tools.asr_command.strip())

    # ------------------------------------------------------------------
    async def synthesize(self, text: str) -> str | None:
        template = self.config.tools.tts_command.strip()
        if not template or not text.strip():
            # 没配命令模板就走内置 edge-tts
            if template:
                return None
            return await _tts.synthesize(
                text,
                voice=self.config.tools.tts_voice,
                rate=self.config.tools.tts_rate,
                pitch=self.config.tools.tts_pitch,
                expressiveness=self.config.tools.tts_expressiveness,
            )
        out_dir = Path(tempfile.mkdtemp(prefix="ricefish_tts_"))
        output = out_dir / "voice.mp3"
        command = template.format(text=text.replace('"', "'")[:200], output=str(output))
        code, _, stderr = await self._run(command, timeout=60)
        if code != 0:
            log.warning("TTS 失败（%s）：%s", code, stderr[-300:])
            return None
        if output.exists() and output.stat().st_size > 0:
            return str(output)
        # 有些命令输出到别的文件名，兜底找一个音频
        for file in out_dir.iterdir():
            if file.suffix.lower() in AUDIO_SUFFIXES and file.stat().st_size > 0:
                return str(file)
        log.warning("TTS 没有产出音频文件")
        return None

    async def transcribe(self, audio_path: str) -> str | None:
        template = self.config.tools.asr_command.strip()
        if not template or not audio_path:
            return None
        out_dir = Path(tempfile.mkdtemp(prefix="ricefish_asr_"))
        command = template.format(
            input=audio_path, outdir=str(out_dir), output=str(out_dir / "text.txt")
        )
        code, stdout, stderr = await self._run(command, timeout=120)
        if code != 0:
            log.warning("ASR 失败（%s）：%s", code, stderr[-300:])
            return None
        for candidate in (out_dir / "text.txt", out_dir / "voice.txt"):
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    return text
        text = (stdout or "").strip()
        return text or None

    # ------------------------------------------------------------------
    @staticmethod
    async def _run(command: str, timeout: float = 60) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, ValueError) as exc:
            log.warning("命令启动失败: %s", exc)
            return 127, "", str(exc)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return 124, "", "超时"
        return (
            int(proc.returncode or 0),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def split_command(template: str) -> list[str]:
        """给测试/调试用：看看模板会被拆成什么。"""
        return shlex.split(template, posix=False) if template else []
