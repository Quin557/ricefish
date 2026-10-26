"""极简 YAML 子集解析器（零依赖）。

本项目的配置文件由项目自己编写，只需要 YAML 的一个小子集：

    key: value
    key:
      nested: 1
    list:
      - a
      - b
    inline_list: [1, 2, 3]
    inline_map: {a: 1, b: 2}
    text: "带 # 号的值"

支持：缩进映射、`- ` 列表（标量或映射）、行内 list/dict、单双引号、
`#` 注释、true/false/null/~、整数、浮点。

如果环境里装了 PyYAML，`loads()` 会优先用它，行为更强；否则退回本实现。
"""

from __future__ import annotations

import re
from typing import Any

try:  # pragma: no cover - 取决于运行环境
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover
    _pyyaml = None


__all__ = ["loads", "load_file", "YamlError", "MiniYamlError"]


class YamlError(ValueError):
    """YAML 子集解析失败。"""


# 兼容旧名字
MiniYamlError = YamlError


_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"null", "~", "none", ""}

_NUM_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def loads(text: str) -> Any:
    """解析 YAML 文本，返回 Python 对象。"""
    if _pyyaml is not None:  # pragma: no cover
        try:
            return _pyyaml.safe_load(text)
        except Exception:
            pass  # 退回本实现
    return _MiniYamlParser(text).parse()


def load_file(path: str, default: Any = None) -> Any:
    """读取并解析 YAML 文件；文件不存在时返回 default。"""
    import os

    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = loads(fh.read())
    return default if data is None else data


def _strip_comment(line: str) -> str:
    """去掉行尾注释，但保留引号内的 #。"""
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            # 只有前面是空白（或行首）才算注释
            if not out or out[-1] in " \t":
                break
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _split_top_level(value: str, sep: str = ",") -> list[str]:
    """按分隔符切分，忽略引号与括号内部的 separator。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    for ch in value:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_key_value(line: str) -> tuple[str, str] | None:
    """把 `key: value` 拆开；不是键值行则返回 None。"""
    quote = ""
    for idx, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == ":":
            # `key:` 后面必须是空白或行尾，避免误伤 `http://x`
            rest = line[idx + 1 :]
            if rest == "" or rest[:1] in " \t":
                return line[:idx].strip(), rest.strip()
    return None


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        body = raw[1:-1]
        if raw[0] == '"':
            return (
                body.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return body
    return raw


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return None
    if raw[0] in "[{":
        if raw[0] == "[" and raw.rstrip().endswith("]"):
            inner = raw.strip()[1:-1].strip()
            if not inner:
                return []
            return [_parse_scalar(p) for p in _split_top_level(inner)]
        if raw[0] == "{" and raw.rstrip().endswith("}"):
            inner = raw.strip()[1:-1].strip()
            if not inner:
                return {}
            result: dict[str, Any] = {}
            for part in _split_top_level(inner):
                kv = _parse_key_value(part)
                if kv is None:
                    raise YamlError(f"无法解析行内映射项: {part!r}")
                result[_unquote(kv[0])] = _parse_scalar(kv[1])
            return result
        raise YamlError(f"括号不匹配: {raw!r}")
    if raw[0] in "\"'":
        return _unquote(raw)
    low = raw.lower()
    if low in _NULL:
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if _NUM_RE.match(raw):
        try:
            return int(raw)
        except ValueError:
            return float(raw)
    return raw


class _MiniYamlParser:
    def __init__(self, text: str) -> None:
        self.lines: list[tuple[int, str]] = []
        self.pos = 0
        for raw in text.replace("\r\n", "\n").split("\n"):
            stripped = _strip_comment(raw)
            if not stripped.strip():
                continue
            if stripped.strip() in ("---", "..."):
                continue
            indent = len(stripped) - len(stripped.lstrip(" "))
            if "\t" in stripped[:indent]:
                raise YamlError("YAML 不支持 Tab 缩进，请用空格")
            self.lines.append((indent, stripped.strip()))

    # -- public ---------------------------------------------------------
    def parse(self) -> Any:
        if not self.lines:
            return None
        value, i = self._parse_block(0, self.lines[0][0])
        if i != len(self.lines):
            indent, content = self.lines[i]
            raise YamlError(f"多余的内容（第 {i + 1} 个有效行）: {content!r}")
        return value

    # -- internals ------------------------------------------------------
    def _parse_block(self, start: int, indent: int) -> tuple[Any, int]:
        if start >= len(self.lines):
            return None, start
        if self.lines[start][1].startswith("- "):
            return self._parse_list(start, indent)
        if self.lines[start][1] == "-":
            return self._parse_list(start, indent)
        return self._parse_map(start, indent)

    def _parse_list(self, start: int, indent: int) -> tuple[list[Any], int]:
        items: list[Any] = []
        i = start
        while i < len(self.lines):
            cur_indent, content = self.lines[i]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise YamlError(f"列表缩进异常: {content!r}")
            if not (content == "-" or content.startswith("- ")):
                break
            inner = content[1:].strip()
            if not inner:
                i += 1
                if i < len(self.lines) and self.lines[i][0] > indent:
                    value, i = self._parse_block(i, self.lines[i][0])
                    items.append(value)
                else:
                    items.append(None)
                continue
            kv = _parse_key_value(inner)
            if kv is None:
                items.append(_parse_scalar(inner))
                i += 1
                continue
            # 列表项是一个映射（"- key: value" 后面可能还跟着同级的键）
            virtual_indent = cur_indent + 2
            mapping: dict[str, Any] = {}
            key, raw_value = kv
            key = _unquote(key)
            nxt = i + 1
            if raw_value == "":
                if nxt < len(self.lines) and self.lines[nxt][0] > virtual_indent:
                    mapping[key], nxt = self._parse_block(nxt, self.lines[nxt][0])
                else:
                    mapping[key] = None
            else:
                mapping[key] = _parse_scalar(raw_value)
            if nxt < len(self.lines) and self.lines[nxt][0] == virtual_indent:
                rest, nxt = self._parse_map(nxt, virtual_indent)
                mapping.update(rest)
            items.append(mapping)
            i = nxt
        return items, i

    def _parse_map(self, start: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        i = start
        while i < len(self.lines):
            cur_indent, content = self.lines[i]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise YamlError(f"映射缩进异常: {content!r}")
            if content == "-" or content.startswith("- "):
                break
            kv = _parse_key_value(content)
            if kv is None:
                raise YamlError(f"不是 key: value 形式: {content!r}")
            key, raw_value = kv
            key = _unquote(key)
            if raw_value == "":
                nxt = i + 1
                if nxt < len(self.lines) and self.lines[nxt][0] > indent:
                    value, i = self._parse_block(nxt, self.lines[nxt][0])
                    result[key] = value
                    continue
                if nxt < len(self.lines) and self.lines[nxt][0] == indent and (
                    self.lines[nxt][1].startswith("- ") or self.lines[nxt][1] == "-"
                ):
                    value, i = self._parse_list(nxt, indent)
                    result[key] = value
                    continue
                result[key] = None
                i += 1
                continue
            result[key] = _parse_scalar(raw_value)
            i += 1
        return result, i
