"""可选的语义检索（本地 embedding）。

DeepSeek 不提供 embedding 接口，所以这里不依赖任何云服务：
默认走本机 Ollama（`ollama pull bge-m3` 之后就能用），关掉时整体退化为关键词检索。

设计上刻意做成"锦上添花"：
    - provider 不通 → 自动跳过，不影响聊天
    - 向量存 SQLite 的 BLOB，按会话隔离，量小
    - 只在词面检索之外补充几条高相似度记忆
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import struct
import time
import urllib.error
import urllib.request

from ..config import MemoryConfig
from .store import MemoryStore

log = logging.getLogger("ricefish.embedding")

DEFAULT_DIM_LIMIT = 4096
MIN_CHARS = 2


class EmbeddingProvider:
    """Ollama 兼容的 embedding 客户端。"""

    def __init__(self, url: str, model: str, timeout: float = 8.0) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._broken_until = 0.0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._broken_until

    def _post(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.url}/api/embeddings",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"embedding 请求失败: {exc}") from exc
            vector = data.get("embedding") if isinstance(data, dict) else None
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("embedding 返回格式异常")
            out.append([float(x) for x in vector[:DEFAULT_DIM_LIMIT]])
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await asyncio.to_thread(self._post, texts)
        except RuntimeError as exc:
            log.warning("embedding 暂时不可用，跳过语义检索：%s", exc)
            self._broken_until = time.monotonic() + 600  # 10 分钟内不再重试
            return []


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


class SemanticIndex:
    def __init__(
        self,
        config: MemoryConfig,
        store: MemoryStore,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.provider = provider
        self.stats = {"indexed": 0, "queries": 0, "hits": 0, "errors": 0}

    @property
    def enabled(self) -> bool:
        return self.provider is not None and self.provider.available

    # ------------------------------------------------------------------
    async def index_pending(
        self, scope_type: str, scope_id: int, limit: int = 20
    ) -> int:
        if not self.enabled:
            return 0
        pending = self.store.memories_missing_vectors(
            scope_type, scope_id, self.provider.model, limit
        )
        if not pending:
            return 0
        texts = [str(row["content"] or "")[:500] for row in pending]
        vectors = await self.provider.embed(texts)
        if len(vectors) != len(pending):
            return 0
        for row, vector in zip(pending, vectors):
            self.store.save_memory_vector(
                int(row["id"]), self.provider.model, pack(vector)
            )
        self.stats["indexed"] += len(vectors)
        log.debug("索引了 %d 条记忆（%s:%s）", len(vectors), scope_type, scope_id)
        return len(vectors)

    # ------------------------------------------------------------------
    async def search(
        self,
        scope_type: str,
        scope_id: int,
        query: str,
        limit: int = 4,
        min_score: float = 0.42,
    ) -> list[dict]:
        if not self.enabled or not query or len(query) < MIN_CHARS:
            return []
        self.stats["queries"] += 1
        vectors = await self.provider.embed([query[:300]])
        if not vectors:
            return []
        query_vector = vectors[0]
        rows = self.store.load_memory_vectors(scope_type, scope_id, self.provider.model)
        scored: list[tuple[float, int]] = []
        for memory_id, blob in rows:
            score = cosine(query_vector, unpack(blob))
            if score >= min_score:
                scored.append((score, memory_id))
        if not scored:
            return []
        scored.sort(reverse=True)
        ids = [memory_id for _, memory_id in scored[:limit]]
        memories = self.store.get_memories_by_ids(ids)
        for memory in memories:
            for score, memory_id in scored:
                if memory_id == memory["id"]:
                    memory["semantic_score"] = round(score, 3)
                    break
        self.stats["hits"] += len(memories)
        return memories
