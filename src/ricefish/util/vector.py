"""轻量文本向量：字符 bigram 特征哈希，不依赖任何模型/网络。

用途：表情库按「情绪/标签/场景」做相似度检索。不是语义模型，但对中文短标签
足够用，而且离线、确定、零成本。
"""

from __future__ import annotations

import hashlib
import math
import re
import struct

DIM = 256


def _tokens(text: str) -> list[str]:
    text = (text or "").lower()
    tokens: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens.extend(re.findall(r"[a-z0-9]{2,}", text))
    return tokens


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for token in _tokens(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 0:
        vector = [value / norm for value in vector]
    return vector


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
    return float(sum(x * y for x, y in zip(a, b)))
