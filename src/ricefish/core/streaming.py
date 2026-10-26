"""流式切分/碎片过滤：纯函数，方便单测和复用。"""

from __future__ import annotations

FILLER_CHARS = set(" \t\r\n。，、；：！？!?…~～.,;:()（）[]【】\"'“”‘’—-_")


def visible_len(text: str) -> int:
    return sum(1 for ch in text if ch not in FILLER_CHARS)


def is_filler(text: str) -> bool:
    # 只有纯标点/空白或单个语气词才算碎片；"在的呀。"这种短句要正常发
    return visible_len(text) <= 1


def strip_leading_filler(text: str) -> str:
    return (text or "").lstrip(" \t\r\n。，、；：！？!?…~～.,;:")


def find_cut(buffer: str, min_chars: int, max_buffer: int) -> int:
    cut = 0
    if len(buffer) >= min_chars:
        # 取最早出现的句末，保证流式按句发，而不是等最后一句
        for index in range(len(buffer)):
            if buffer[index] in "。！？!?…\n":
                cut = index + 1
                break
        if not cut and len(buffer) >= max_buffer:
            for index in range(len(buffer) - 1, -1, -1):
                if buffer[index] in "，,；;、 ":
                    cut = index + 1
                    break
            cut = cut or max_buffer
    if not cut:
        return 0
    # 别把 [表情:开心] 这种标记切成两半
    head = buffer[:cut]
    for opener, closer in (("[", "]"), ("【", "】")):
        start = head.rfind(opener)
        if start >= 0 and head.find(closer, start) < 0:
            cut = start
    return max(0, cut)
