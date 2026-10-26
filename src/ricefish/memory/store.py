"""SQLite 存储层（同步，标准库 sqlite3）。

设计取舍：
    - 同步调用。这里是个人机器人，单条 SQL 通常是亚毫秒级，比引入
      aiosqlite/连接池简单可靠得多；重活（VACUUM、归档）放到线程里执行。
    - 单连接 + WAL。所有写入串行，天然避免锁冲突。
    - 中文检索不用 FTS5（trigram 对两字词不友好），改用「二元组 LIKE + 覆盖度打分」，
      数据量在万级以内足够快，且行为可预测、可测试。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ..util.text import estimate_tokens
from ..util.vector import cosine, embed, pack, unpack

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+#\-]{1,}")
_STOP_BIGRAMS = {
    "的时", "时候", "我们", "你们", "他们", "这个", "那个", "就是", "什么",
    "怎么", "可以", "没有", "不是", "还是", "然后", "因为", "所以",
}


def _now() -> int:
    return int(time.time())


def query_tokens(text: str, limit: int = 12) -> list[str]:
    """把一段话拆成用于 LIKE 检索的词元：中文二元组 + 英文单词。"""
    if not text:
        return []
    tokens: list[str] = []
    for run in _CJK.findall(text):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram in _STOP_BIGRAMS:
                continue
            tokens.append(bigram)
    tokens.extend(word.lower() for word in _WORD.findall(text))
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique[:limit]


class MemoryStore:
    def __init__(self, path: str = "data/bot.db") -> None:
        self.path = path
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._init_schema()

    # ------------------------------------------------------------------
    # 基础
    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            sql = SCHEMA_PATH.read_text(encoding="utf-8")
            self.conn.executescript(sql)
            self._ensure_columns()
            self.conn.commit()

    def _ensure_columns(self) -> None:
        """轻量迁移：老库补列，新库由 schema.sql 直接建好。"""
        wanted = {
            "memories": {
                "confidence": "REAL DEFAULT 0.5",
                "last_confirmed_at": "INTEGER DEFAULT 0",
            },
            "stickers": {"vector": "BLOB"},
        }
        for table, columns in wanted.items():
            existing = {
                str(row[1])
                for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        try:
            self.conn.execute("PRAGMA user_version = 2")
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.commit()
                self.conn.close()
            except sqlite3.Error:
                pass

    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cursor

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # settings / state
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )

    def all_settings(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT key, value FROM settings")}

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )

    # ------------------------------------------------------------------
    # persons
    # ------------------------------------------------------------------
    def ensure_person(self, qq: int, nickname: str = "") -> None:
        now = _now()
        self._execute(
            "INSERT INTO persons(qq, nickname, first_seen, last_seen) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(qq) DO UPDATE SET "
            "  nickname = CASE WHEN excluded.nickname <> '' THEN excluded.nickname ELSE persons.nickname END, "
            "  last_seen = excluded.last_seen",
            (qq, nickname, now, now),
        )

    def get_person(self, qq: int) -> dict | None:
        row = self._one("SELECT * FROM persons WHERE qq = ?", (qq,))
        return dict(row) if row else None

    def list_persons(self, limit: int = 500) -> list[dict]:
        rows = self._query(
            "SELECT * FROM persons ORDER BY last_seen DESC, qq ASC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    def update_person_note(self, qq: int, note: str) -> None:
        self._execute("UPDATE persons SET global_note = ? WHERE qq = ?", (note, qq))

    # ------------------------------------------------------------------
    # chats
    # ------------------------------------------------------------------
    def upsert_chat(
        self, scope_type: str, scope_id: int, title: str = "", **flags: Any
    ) -> None:
        scope_key = f"{scope_type}:{scope_id}"
        now = _now()
        self._execute(
            "INSERT INTO chats(scope_key, scope_type, scope_id, title, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(scope_key) DO UPDATE SET "
            "  title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE chats.title END, "
            "  updated_at = excluded.updated_at",
            (scope_key, scope_type, scope_id, title, now, now),
        )
        for field_name in ("enabled", "proactive_enabled", "persona_path"):
            if field_name in flags:
                self._execute(
                    f"UPDATE chats SET {field_name} = ?, updated_at = ? WHERE scope_key = ?",
                    (flags[field_name], now, scope_key),
                )

    def get_chat(self, scope_type: str, scope_id: int) -> dict | None:
        row = self._one(
            "SELECT * FROM chats WHERE scope_key = ?", (f"{scope_type}:{scope_id}",)
        )
        return dict(row) if row else None

    def list_chats(self, scope_type: str | None = None) -> list[dict]:
        if scope_type:
            rows = self._query(
                "SELECT * FROM chats WHERE scope_type = ? ORDER BY updated_at DESC",
                (scope_type,),
            )
        else:
            rows = self._query("SELECT * FROM chats ORDER BY updated_at DESC")
        return [dict(row) for row in rows]

    def chat_enabled(self, scope_type: str, scope_id: int, default: bool = True) -> bool:
        chat = self.get_chat(scope_type, scope_id)
        if not chat:
            return default
        return bool(chat.get("enabled", 1))

    # ------------------------------------------------------------------
    # messages
    # ------------------------------------------------------------------
    def add_message(
        self,
        scope_type: str,
        scope_id: int,
        *,
        message_id: int = 0,
        sender_qq: int = 0,
        sender_name: str = "",
        text: str = "",
        segments: list | None = None,
        raw: dict | None = None,
        ts: int | None = None,
        is_bot: bool = False,
        replied_by_bot: bool = False,
    ) -> int:
        cursor = self._execute(
            "INSERT INTO messages(message_id, scope_type, scope_id, sender_qq, sender_name, "
            "text, segments_json, raw_json, ts, is_bot, replied_by_bot) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                scope_type,
                scope_id,
                sender_qq,
                sender_name,
                text,
                json.dumps(segments or [], ensure_ascii=False),
                json.dumps(raw or {}, ensure_ascii=False) if raw else "",
                ts or _now(),
                1 if is_bot else 0,
                1 if replied_by_bot else 0,
            ),
        )
        return int(cursor.lastrowid or 0)

    def message_exists(self, message_id: int) -> bool:
        if not message_id:
            return False
        return self._one("SELECT 1 FROM messages WHERE message_id = ? LIMIT 1", (message_id,)) is not None

    def mark_recalled(self, message_id: int) -> None:
        self._execute(
            "UPDATE messages SET recalled = 1 WHERE message_id = ?", (message_id,)
        )

    def get_message_by_id(self, message_id: int) -> dict | None:
        row = self._one(
            "SELECT * FROM messages WHERE message_id = ? ORDER BY id DESC LIMIT 1",
            (message_id,),
        )
        return dict(row) if row else None

    def mark_replied(self, message_id: int) -> None:
        self._execute(
            "UPDATE messages SET replied_by_bot = 1 WHERE message_id = ?", (message_id,)
        )

    def recent_messages(
        self,
        scope_type: str,
        scope_id: int,
        limit: int = 40,
        max_tokens: int = 6000,
    ) -> list[dict]:
        rows = self._query(
            "SELECT * FROM messages WHERE scope_type = ? AND scope_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (scope_type, scope_id, max(1, limit)),
        )
        picked: list[dict] = []
        total = 0
        for row in rows:  # 从新到旧
            item = dict(row)
            cost = estimate_tokens(item.get("text") or "") + 8
            if picked and total + cost > max_tokens:
                break
            total += cost
            picked.append(item)
        picked.reverse()
        return picked

    def messages_after(
        self, scope_type: str, scope_id: int, after_id: int = 0, limit: int = 200
    ) -> list[dict]:
        rows = self._query(
            "SELECT * FROM messages WHERE scope_type = ? AND scope_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            (scope_type, scope_id, after_id, limit),
        )
        return [dict(row) for row in rows]

    def count_messages(self, scope_type: str, scope_id: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM messages WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        )
        return int(row["n"]) if row else 0

    def count_messages_since(self, scope_type: str, scope_id: int, since_ts: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM messages "
            "WHERE scope_type = ? AND scope_id = ? AND ts >= ?",
            (scope_type, scope_id, since_ts),
        )
        return int(row["n"]) if row else 0

    def top_senders(
        self, scope_type: str, scope_id: int, since_ts: int, limit: int = 5
    ) -> list[tuple[str, int]]:
        rows = self._query(
            "SELECT COALESCE(NULLIF(sender_name, ''), CAST(sender_qq AS TEXT)) AS who, "
            "COUNT(*) AS n FROM messages "
            "WHERE scope_type = ? AND scope_id = ? AND ts >= ? AND is_bot = 0 "
            "GROUP BY who ORDER BY n DESC LIMIT ?",
            (scope_type, scope_id, since_ts, limit),
        )
        return [(str(row["who"]), int(row["n"])) for row in rows]

    def count_memories_since(self, since_ts: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM memories WHERE created_at >= ? AND archived = 0",
            (since_ts,),
        )
        return int(row["n"]) if row else 0

    def last_message_ts(self, scope_type: str, scope_id: int) -> int:
        row = self._one(
            "SELECT MAX(ts) AS t FROM messages WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        )
        return int(row["t"] or 0) if row else 0

    def last_message_id(self, scope_type: str, scope_id: int) -> int:
        row = self._one(
            "SELECT MAX(id) AS i FROM messages WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        )
        return int(row["i"] or 0) if row else 0

    def active_scopes(self, since_ts: int = 0) -> list[tuple[str, int]]:
        rows = self._query(
            "SELECT DISTINCT scope_type, scope_id FROM messages WHERE ts >= ?",
            (since_ts,),
        )
        return [(row["scope_type"], int(row["scope_id"])) for row in rows]

    def global_recent(self, limit: int = 40) -> list[dict]:
        rows = self._query("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    def scope_recent(self, scope_type: str, scope_id: int, limit: int = 40) -> list[dict]:
        rows = self._query(
            "SELECT * FROM messages WHERE scope_type = ? AND scope_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (scope_type, scope_id, limit),
        )
        return [dict(row) for row in rows]

    def search_messages(
        self, scope_type: str, scope_id: int, query: str, limit: int = 30
    ) -> list[dict]:
        tokens = query_tokens(query, limit=6)
        if not tokens:
            return []
        where = " OR ".join("text LIKE ?" for _ in tokens)
        params: list[Any] = [scope_type, scope_id] + [f"%{token}%" for token in tokens]
        rows = self._query(
            f"SELECT * FROM messages WHERE scope_type = ? AND scope_id = ? AND ({where}) "
            "ORDER BY id DESC LIMIT ?",
            params + [limit],
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # profiles
    # ------------------------------------------------------------------
    PROFILE_FIELDS = (
        "display_name",
        "aliases",
        "identity",
        "relation",
        "tags",
        "preferences",
        "taboos",
        "notes",
        "affinity",
        "mood",
    )

    def ensure_profile(
        self,
        scope_type: str,
        scope_id: int,
        qq: int,
        display_name: str = "",
    ) -> None:
        now = _now()
        self._execute(
            "INSERT INTO profiles(scope_type, scope_id, qq, display_name, last_seen, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(scope_type, scope_id, qq) DO UPDATE SET "
            "  last_seen = excluded.last_seen, "
            "  display_name = CASE WHEN profiles.display_name = '' THEN excluded.display_name "
            "                      ELSE profiles.display_name END",
            (scope_type, scope_id, qq, display_name, now, now),
        )

    def get_profile(self, scope_type: str, scope_id: int, qq: int) -> dict | None:
        row = self._one(
            "SELECT * FROM profiles WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            (scope_type, scope_id, qq),
        )
        return dict(row) if row else None

    def list_profiles(self, scope_type: str, scope_id: int, limit: int = 200) -> list[dict]:
        rows = self._query(
            "SELECT * FROM profiles WHERE scope_type = ? AND scope_id = ? "
            "ORDER BY affinity DESC, last_seen DESC LIMIT ?",
            (scope_type, scope_id, limit),
        )
        return [dict(row) for row in rows]

    def all_profiles(self, limit: int = 500) -> list[dict]:
        rows = self._query(
            "SELECT * FROM profiles ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    def search_profiles(
        self,
        scope_type: str | None = None,
        scope_id: int | None = None,
        query: str = "",
        limit: int = 200,
    ) -> list[dict]:
        """按会话和关键词筛档案；关键词匹配 QQ、称呼、别名、标签、备注。"""
        where: list[str] = []
        params: list[Any] = []
        if scope_type:
            where.append("scope_type = ?")
            params.append(scope_type)
        if scope_id:
            where.append("scope_id = ?")
            params.append(int(scope_id))
        text = (query or "").strip()
        if text:
            like = f"%{text}%"
            where.append(
                "(CAST(qq AS TEXT) LIKE ? OR display_name LIKE ? OR aliases LIKE ? "
                "OR tags LIKE ? OR notes LIKE ?)"
            )
            params.extend([like] * 5)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._query(
            f"SELECT * FROM profiles{clause} "
            "ORDER BY affinity DESC, last_seen DESC LIMIT ?",
            params + [int(limit)],
        )
        return [dict(row) for row in rows]

    def update_profile(
        self,
        scope_type: str,
        scope_id: int,
        qq: int,
        *,
        manual: bool | None = None,
        **fields: Any,
    ) -> None:
        """更新档案字段。

        manual 三态：True 锁定、False 解锁、None 保持原样。
        affinity 等字段缺失时不写入，避免误重置。
        """
        allowed = [name for name in fields if name in self.PROFILE_FIELDS]
        if not allowed and manual is None:
            return
        self.ensure_profile(scope_type, scope_id, qq)
        sets: list[str] = []
        params: list[Any] = []
        for name in allowed:
            sets.append(f"{name} = ?")
            params.append(fields[name])
        if manual is not None:
            sets.append("is_override = ?")
            params.append(1 if manual else 0)
        sets.append("updated_at = ?")
        params.append(_now())
        params.extend([scope_type, scope_id, qq])
        self._execute(
            f"UPDATE profiles SET {', '.join(sets)} "
            "WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            params,
        )

    def apply_profile_patch(
        self,
        scope_type: str,
        scope_id: int,
        qq: int,
        patch: dict[str, Any],
    ) -> list[str]:
        """抽取器写回：只填空 / 覆盖非人工字段。返回实际更新的字段。"""
        self.ensure_profile(scope_type, scope_id, qq)
        current = self.get_profile(scope_type, scope_id, qq)
        if not current:
            return []
        changes: dict[str, Any] = {}
        for name, value in (patch or {}).items():
            if name not in self.PROFILE_FIELDS or value in (None, "", [], {}):
                continue
            if int(current.get("is_override") or 0) == 1:
                # 人工锁定的档案整体不覆盖
                continue
            if not current.get(name):
                changes[name] = value
            elif name in ("notes", "relation", "mood"):
                changes[name] = value
            elif name in ("tags", "preferences", "taboos", "aliases"):
                merged: list[str] = []
                for chunk in (str(current.get(name) or ""), str(value)):
                    for part in re.split(r"[,，、]", chunk):
                        part = part.strip()
                        if part and part not in merged:
                            merged.append(part)
                changes[name] = ",".join(merged[:12])
            else:
                changes[name] = value
        if changes:
            self.update_profile(scope_type, scope_id, qq, **changes)
        return list(changes)

    def bump_affinity(self, scope_type: str, scope_id: int, qq: int, delta: int) -> int:
        self.ensure_profile(scope_type, scope_id, qq)
        self._execute(
            "UPDATE profiles SET affinity = MAX(0, MIN(100, affinity + ?)), updated_at = ? "
            "WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            (int(delta), _now(), scope_type, scope_id, qq),
        )
        row = self._one(
            "SELECT affinity FROM profiles WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            (scope_type, scope_id, qq),
        )
        return int(row["affinity"]) if row else 50

    def note_profile_message(
        self, scope_type: str, scope_id: int, qq: int, sender_name: str = ""
    ) -> None:
        self.ensure_profile(scope_type, scope_id, qq, sender_name)
        self._execute(
            "UPDATE profiles SET msg_count = msg_count + 1, last_seen = ? "
            "WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            (_now(), scope_type, scope_id, qq),
        )

    def reset_profile(self, scope_type: str, scope_id: int, qq: int) -> None:
        self._execute(
            "DELETE FROM profiles WHERE scope_type = ? AND scope_id = ? AND qq = ?",
            (scope_type, scope_id, qq),
        )

    # ------------------------------------------------------------------
    # memories
    # ------------------------------------------------------------------
    def add_memory(
        self,
        scope_type: str,
        scope_id: int,
        content: str,
        *,
        subject_qq: int = 0,
        kind: str = "fact",
        importance: int = 3,
        confidence: float = 0.5,
        source_msg_ids: str = "",
    ) -> int:
        content = (content or "").strip()
        if not content:
            return 0
        confidence = max(0.0, min(1.0, float(confidence or 0.5)))
        # 同主体下内容完全重复就不再记，只提高置信度/确认时间
        existing = self._one(
            "SELECT id FROM memories WHERE scope_type = ? AND scope_id = ? "
            "AND subject_qq = ? AND content = ? AND archived = 0 LIMIT 1",
            (scope_type, scope_id, subject_qq, content),
        )
        if existing:
            self._execute(
                "UPDATE memories SET use_count = use_count + 1, last_used_at = ?, "
                "confidence = MAX(confidence, ?), last_confirmed_at = ? WHERE id = ?",
                (_now(), confidence, _now(), existing["id"]),
            )
            return int(existing["id"])
        cursor = self._execute(
            "INSERT INTO memories(scope_type, scope_id, subject_qq, kind, content, "
            "importance, confidence, source_msg_ids, created_at, last_confirmed_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope_type,
                scope_id,
                int(subject_qq or 0),
                kind or "fact",
                content,
                max(1, min(5, int(importance or 3))),
                confidence,
                source_msg_ids,
                _now(),
                _now(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def _score_memory(
        self,
        row: sqlite3.Row | dict,
        tokens: list[str],
        subject_qq: int,
        now: int,
    ) -> float:
        content = str(row["content"] or "").lower()
        if not tokens:
            coverage = 0.0
        else:
            matched = sum(1 for token in tokens if token in content)
            coverage = matched / len(tokens)
        importance = float(row["importance"] or 3) / 5.0
        try:
            confidence = float(row["confidence"] or 0.5)
        except (KeyError, IndexError, TypeError, ValueError):
            confidence = 0.5
        created = int(row["created_at"] or now)
        try:
            confirmed = int(row["last_confirmed_at"] or created)
        except (KeyError, IndexError, TypeError, ValueError):
            confirmed = created
        age_days = max(0.0, (now - confirmed) / 86400.0)
        recency = 0.5 ** (age_days / 30.0)
        score = 3.0 * coverage + 1.0 * importance + 0.6 * recency + 0.8 * confidence
        if subject_qq and int(row["subject_qq"] or 0) == subject_qq:
            score *= 1.6
        if int(row["use_count"] or 0) > 0:
            score += 0.1
        return score

    def search_memories(
        self,
        scope_type: str,
        scope_id: int,
        query: str = "",
        *,
        subject_qq: int = 0,
        limit: int = 8,
        min_score: float = 0.9,
    ) -> list[dict]:
        tokens = query_tokens(query, limit=12)
        now = _now()
        if tokens:
            where = " OR ".join("lower(content) LIKE ?" for _ in tokens)
            params: list[Any] = [scope_type, scope_id]
            params.extend(f"%{token}%" for token in tokens)
            rows = self._query(
                f"SELECT * FROM memories WHERE scope_type = ? AND scope_id = ? "
                f"AND archived = 0 AND ({where}) ORDER BY id DESC LIMIT 200",
                params,
            )
        else:
            rows = self._query(
                "SELECT * FROM memories WHERE scope_type = ? AND scope_id = ? AND archived = 0 "
                "ORDER BY importance DESC, id DESC LIMIT 40",
                (scope_type, scope_id),
            )

        scored = [
            (self._score_memory(row, tokens, subject_qq, now), dict(row)) for row in rows
        ]
        scored = [item for item in scored if item[0] >= min_score]
        scored.sort(key=lambda item: item[0], reverse=True)
        results = []
        for score, row in scored[:limit]:
            row["score"] = round(score, 3)
            results.append(row)
        return results

    def list_memories(
        self,
        scope_type: str | None = None,
        scope_id: int | None = None,
        *,
        qq: int | None = None,
        kind: str | None = None,
        keyword: str = "",
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if qq is not None:
            clauses.append("subject_qq = ?")
            params.append(qq)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if not include_archived:
            clauses.append("archived = 0")
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query(
            f"SELECT * FROM memories{where} ORDER BY id DESC LIMIT ?", params + [limit]
        )
        return [dict(row) for row in rows]

    def memory_stats(self) -> dict:
        total = self._one("SELECT COUNT(*) AS n FROM memories WHERE archived = 0")
        archived = self._one("SELECT COUNT(*) AS n FROM memories WHERE archived = 1")
        return {
            "active": int(total["n"] or 0) if total else 0,
            "archived": int(archived["n"] or 0) if archived else 0,
        }

    # -- 语义向量（可选） --------------------------------------------
    def memories_missing_vectors(
        self, scope_type: str, scope_id: int, model: str, limit: int = 20
    ) -> list[dict]:
        rows = self._query(
            "SELECT m.* FROM memories m LEFT JOIN memory_vectors v ON v.memory_id = m.id "
            "WHERE m.scope_type = ? AND m.scope_id = ? AND m.archived = 0 "
            "AND (v.memory_id IS NULL OR v.model <> ?) ORDER BY m.id DESC LIMIT ?",
            (scope_type, scope_id, model, limit),
        )
        return [dict(row) for row in rows]

    def save_memory_vector(self, memory_id: int, model: str, blob: bytes) -> None:
        self._execute(
            "INSERT INTO memory_vectors(memory_id, model, dim, vector, updated_at) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(memory_id) DO UPDATE SET "
            "model = excluded.model, dim = excluded.dim, "
            "vector = excluded.vector, updated_at = excluded.updated_at",
            (memory_id, model, len(blob) // 4, blob, _now()),
        )

    def load_memory_vectors(
        self, scope_type: str, scope_id: int, model: str
    ) -> list[tuple[int, bytes]]:
        rows = self._query(
            "SELECT v.memory_id AS id, v.vector AS vector FROM memory_vectors v "
            "JOIN memories m ON m.id = v.memory_id "
            "WHERE m.scope_type = ? AND m.scope_id = ? AND m.archived = 0 AND v.model = ?",
            (scope_type, scope_id, model),
        )
        return [(int(row["id"]), bytes(row["vector"] or b"")) for row in rows]

    def get_memories_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        rows = self._query(
            f"SELECT * FROM memories WHERE id IN ({marks}) AND archived = 0", ids
        )
        return [dict(row) for row in rows]

    def touch_memory(self, memory_id: int, count: int = 1) -> None:
        self._execute(
            "UPDATE memories SET use_count = use_count + ?, last_used_at = ? WHERE id = ?",
            (count, _now(), memory_id),
        )

    def archive_memory(self, memory_id: int, archived: bool = True) -> None:
        self._execute(
            "UPDATE memories SET archived = ? WHERE id = ?", (1 if archived else 0, memory_id)
        )

    def delete_memory(self, memory_id: int) -> None:
        self._execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    # ------------------------------------------------------------------
    # open loops：还记着的事
    # ------------------------------------------------------------------
    def add_open_loop(
        self,
        scope_type: str,
        scope_id: int,
        content: str,
        *,
        subject_qq: int = 0,
        kind: str = "topic",
        due_ts: int = 0,
        source_msg_ids: str = "",
    ) -> int:
        content = (content or "").strip()[:200]
        if len(content) < 2:
            return 0
        existing = self._one(
            "SELECT id FROM open_loops WHERE scope_type = ? AND scope_id = ? "
            "AND subject_qq = ? AND content = ? AND status = 'open' LIMIT 1",
            (scope_type, scope_id, int(subject_qq or 0), content),
        )
        if existing:
            self._execute(
                "UPDATE open_loops SET updated_at = ? WHERE id = ?",
                (_now(), int(existing["id"])),
            )
            return int(existing["id"])
        now = _now()
        cursor = self._execute(
            "INSERT INTO open_loops(scope_type, scope_id, subject_qq, kind, content, "
            "status, due_ts, source_msg_ids, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (
                scope_type,
                scope_id,
                int(subject_qq or 0),
                kind if kind in ("topic", "promise", "plan", "question") else "topic",
                content,
                int(due_ts or 0),
                source_msg_ids,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_open_loops(
        self,
        scope_type: str | None = None,
        scope_id: int | None = None,
        *,
        subject_qq: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        clauses = ["status = 'open'"]
        params: list[Any] = []
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("scope_id = ?")
            params.append(int(scope_id))
        if subject_qq:
            clauses.append("(subject_qq = ? OR subject_qq = 0)")
            params.append(int(subject_qq))
        rows = self._query(
            "SELECT * FROM open_loops WHERE " + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id DESC LIMIT ?",
            params + [int(limit)],
        )
        return [dict(row) for row in rows]

    def resolve_open_loop(self, loop_id: int, status: str = "resolved") -> None:
        status = status if status in ("resolved", "dropped") else "resolved"
        self._execute(
            "UPDATE open_loops SET status = ?, resolved_at = ?, updated_at = ? WHERE id = ?",
            (status, _now(), _now(), int(loop_id)),
        )

    def delete_open_loop(self, loop_id: int) -> None:
        self._execute("DELETE FROM open_loops WHERE id = ?", (int(loop_id),))

    def close_stale_open_loops(self, days: int = 30) -> int:
        cutoff = _now() - days * 86400
        cursor = self._execute(
            "UPDATE open_loops SET status = 'dropped', resolved_at = ?, updated_at = ? "
            "WHERE status = 'open' AND updated_at < ?",
            (_now(), _now(), cutoff),
        )
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # episodes / relationship events
    # ------------------------------------------------------------------
    def add_episode(
        self,
        scope_type: str,
        scope_id: int,
        summary: str,
        *,
        subject_qq: int = 0,
        from_ts: int = 0,
        to_ts: int = 0,
        tone: str = "",
        topics: str = "",
        msg_count: int = 0,
    ) -> int:
        summary = (summary or "").strip()
        if not summary:
            return 0
        existing = self._one(
            "SELECT id FROM episodes WHERE scope_type = ? AND scope_id = ? "
            "AND summary = ? LIMIT 1",
            (scope_type, scope_id, summary),
        )
        if existing:
            return int(existing["id"])
        cursor = self._execute(
            "INSERT INTO episodes(scope_type, scope_id, subject_qq, from_ts, to_ts, "
            "summary, tone, topics, msg_count, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope_type,
                scope_id,
                int(subject_qq or 0),
                int(from_ts or 0),
                int(to_ts or 0),
                summary,
                str(tone or "")[:40],
                str(topics or "")[:200],
                int(msg_count or 0),
                _now(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_episodes(
        self, scope_type: str, scope_id: int, limit: int = 5
    ) -> list[dict]:
        rows = self._query(
            "SELECT * FROM episodes WHERE scope_type = ? AND scope_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (scope_type, scope_id, int(limit)),
        )
        return [dict(row) for row in rows]

    def add_relationship_event(
        self,
        scope_type: str,
        scope_id: int,
        content: str,
        *,
        subject_qq: int = 0,
        kind: str = "event",
        importance: int = 3,
        created_at: int = 0,
    ) -> int:
        content = (content or "").strip()[:200]
        if len(content) < 2:
            return 0
        existing = self._one(
            "SELECT id FROM relationship_events WHERE scope_type = ? AND scope_id = ? "
            "AND subject_qq = ? AND content = ? LIMIT 1",
            (scope_type, scope_id, int(subject_qq or 0), content),
        )
        if existing:
            return int(existing["id"])
        cursor = self._execute(
            "INSERT INTO relationship_events(scope_type, scope_id, subject_qq, kind, "
            "content, importance, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                scope_type,
                scope_id,
                int(subject_qq or 0),
                kind if kind in ("first_meet", "promise", "conflict", "apology",
                                 "milestone", "joke", "event") else "event",
                content,
                max(1, min(5, int(importance or 3))),
                int(created_at or _now()),
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_relationship_events(
        self,
        scope_type: str,
        scope_id: int,
        *,
        subject_qq: int | None = None,
        limit: int = 20,
    ) -> list[dict]:
        clauses = ["scope_type = ?", "scope_id = ?"]
        params: list[Any] = [scope_type, scope_id]
        if subject_qq:
            clauses.append("(subject_qq = ? OR subject_qq = 0)")
            params.append(int(subject_qq))
        rows = self._query(
            "SELECT * FROM relationship_events WHERE " + " AND ".join(clauses)
            + " ORDER BY id DESC LIMIT ?",
            params + [int(limit)],
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 图片卡缓存
    # ------------------------------------------------------------------
    def get_image_card(self, digest: str) -> dict | None:
        row = self._one("SELECT * FROM image_cards WHERE hash = ?", (digest,))
        return dict(row) if row else None

    def upsert_image_card(
        self,
        digest: str,
        card: dict,
        *,
        scope_type: str = "",
        scope_id: int = 0,
    ) -> None:
        import json

        reply_angles = card.get("reply_angles") or []
        if isinstance(reply_angles, list):
            reply_angles_text = json.dumps(reply_angles, ensure_ascii=False)
        else:
            reply_angles_text = str(reply_angles)
        self._execute(
            "INSERT INTO image_cards(hash, scope_type, scope_id, kind, summary, ocr, "
            "meme, emotion, reply_angles, safety, raw_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(hash) DO UPDATE SET "
            "  summary = excluded.summary, ocr = excluded.ocr, meme = excluded.meme, "
            "  emotion = excluded.emotion, reply_angles = excluded.reply_angles, "
            "  safety = excluded.safety, raw_json = excluded.raw_json",
            (
                digest,
                scope_type,
                int(scope_id or 0),
                str(card.get("kind") or "other"),
                str(card.get("summary") or ""),
                str(card.get("ocr") or ""),
                str(card.get("meme") or ""),
                str(card.get("emotion") or ""),
                reply_angles_text,
                str(card.get("safety") or "unknown"),
                json.dumps(card, ensure_ascii=False),
                _now(),
            ),
        )

    # ------------------------------------------------------------------
    # 表情包库
    # ------------------------------------------------------------------
    def add_sticker(
        self,
        digest: str,
        path: str,
        *,
        scope_type: str = "",
        scope_id: int = 0,
        sender_qq: int = 0,
        source: str = "",
        license: str = "",
        kind: str = "sticker",
        emotion: str = "",
        tags: str = "",
        ocr: str = "",
        nsfw: int = 0,
    ) -> int:
        existing = self._one("SELECT id FROM stickers WHERE hash = ?", (digest,))
        if existing:
            if source or license:
                self._execute(
                    "UPDATE stickers SET source = CASE WHEN ? <> '' THEN ? ELSE source END, "
                    "license = CASE WHEN ? <> '' THEN ? ELSE license END WHERE id = ?",
                    (source, source, license, license, int(existing["id"])),
                )
            return int(existing["id"])
        vector = pack(embed(" ".join([str(kind), str(emotion), str(tags)])))
        cursor = self._execute(
            "INSERT INTO stickers(hash, path, scope_type, scope_id, sender_qq, source, "
            "license, kind, emotion, tags, ocr, vector, nsfw, enabled, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                digest,
                str(path),
                scope_type,
                int(scope_id or 0),
                int(sender_qq or 0),
                source,
                license,
                kind,
                emotion,
                tags,
                ocr,
                vector,
                1 if nsfw else 0,
                _now(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def list_stickers(
        self,
        *,
        scope_type: str | None = None,
        scope_id: int | None = None,
        include_disabled: bool = True,
        limit: int = 300,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if scope_type:
            clauses.append("(scope_type = ? OR scope_type = '')")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("(scope_id = ? OR scope_type = '')")
            params.append(int(scope_id))
        if not include_disabled:
            clauses.append("enabled = 1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query(
            f"SELECT * FROM stickers{where} ORDER BY id DESC LIMIT ?",
            params + [int(limit)],
        )
        return [dict(row) for row in rows]

    def get_sticker(self, sticker_id: int) -> dict | None:
        row = self._one("SELECT * FROM stickers WHERE id = ?", (int(sticker_id),))
        return dict(row) if row else None

    def pick_sticker(
        self,
        *,
        scope_type: str = "",
        scope_id: int = 0,
        emotion: str = "",
        query: str = "",
        exclude_ids: list[int] | None = None,
        limit: int = 24,
    ) -> dict | None:
        clauses = ["enabled = 1", "nsfw = 0"]
        params: list[Any] = []
        if scope_type and scope_id:
            clauses.append("(scope_type = ? AND scope_id = ? OR scope_type = '')")
            params.extend([scope_type, int(scope_id)])
        if emotion:
            clauses.append("(emotion LIKE ? OR tags LIKE ?)")
            params.extend([f"%{emotion}%", f"%{emotion}%"])
        if exclude_ids:
            marks = ",".join("?" for _ in exclude_ids)
            clauses.append(f"id NOT IN ({marks})")
            params.extend(int(x) for x in exclude_ids[:20])
        rows = self._query(
            "SELECT * FROM stickers WHERE " + " AND ".join(clauses)
            + " ORDER BY RANDOM() LIMIT ?",
            params + [max(int(limit), 100 if query else int(limit))],
        )
        if query and rows:
            query_vector = embed(query)
            scored = [
                (cosine(query_vector, unpack(row["vector"] or b"")), dict(row))
                for row in rows
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            if scored and scored[0][0] > 0:
                return scored[0][1]
        if not rows and emotion:
            # 没有匹配标签就随便挑一张，别让回复没表情可用
            return self.pick_sticker(
                scope_type=scope_type, scope_id=scope_id, exclude_ids=exclude_ids
            )
        return dict(rows[0]) if rows else None

    def note_sticker_used(self, sticker_id: int) -> None:
        self._execute(
            "UPDATE stickers SET used_count = used_count + 1, last_used_at = ? WHERE id = ?",
            (_now(), int(sticker_id)),
        )

    def set_sticker_enabled(self, sticker_id: int, enabled: bool) -> None:
        self._execute(
            "UPDATE stickers SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, int(sticker_id)),
        )

    def set_sticker_scope(
        self, sticker_id: int, scope_type: str = "", scope_id: int = 0
    ) -> None:
        if scope_type not in ("group", "private"):
            scope_type, scope_id = "", 0
        self._execute(
            "UPDATE stickers SET scope_type = ?, scope_id = ? WHERE id = ?",
            (scope_type, int(scope_id or 0), int(sticker_id)),
        )

    def update_sticker_tags(
        self,
        sticker_id: int,
        *,
        emotion: str = "",
        tags: str = "",
        nsfw: int | None = None,
    ) -> None:
        vector = pack(embed(" ".join([str(emotion), str(tags)])))
        if nsfw is None:
            self._execute(
                "UPDATE stickers SET emotion = ?, tags = ?, vector = ? WHERE id = ?",
                (emotion[:40], tags[:200], vector, int(sticker_id)),
            )
        else:
            self._execute(
                "UPDATE stickers SET emotion = ?, tags = ?, vector = ?, nsfw = ?, "
                "enabled = CASE WHEN ? = 1 THEN 0 ELSE enabled END WHERE id = ?",
                (
                    emotion[:40],
                    tags[:200],
                    vector,
                    1 if nsfw else 0,
                    1 if nsfw else 0,
                    int(sticker_id),
                ),
            )

    def delete_sticker(self, sticker_id: int) -> None:
        self._execute("DELETE FROM stickers WHERE id = ?", (int(sticker_id),))

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_chat(self, scope_type: str, scope_id: int) -> dict:
        messages = self._query(
            "SELECT * FROM messages WHERE scope_type = ? AND scope_id = ? ORDER BY id",
            (scope_type, int(scope_id)),
        )
        summaries = self._query(
            "SELECT * FROM summaries WHERE scope_type = ? AND scope_id = ? ORDER BY id",
            (scope_type, int(scope_id)),
        )
        return {
            "version": 1,
            "scope_type": scope_type,
            "scope_id": int(scope_id),
            "chat": self.get_chat(scope_type, scope_id) or {},
            "profiles": self.list_profiles(scope_type, scope_id, limit=500),
            "messages": [dict(row) for row in messages],
            "memories": self.list_memories(scope_type, scope_id, limit=2000),
            "summaries": [dict(row) for row in summaries],
            "open_loops": self.list_open_loops(scope_type, scope_id, limit=1000),
            "episodes": self.list_episodes(scope_type, scope_id, limit=1000),
            "relationship_events": self.list_relationship_events(
                scope_type, scope_id, limit=1000
            ),
        }

    def import_chat(
        self,
        data: dict,
        *,
        scope_type: str | None = None,
        scope_id: int | None = None,
    ) -> dict:
        """把导出的档案/记忆/情节/话题并回来；消息不重复导入，避免刷屏。"""
        if not isinstance(data, dict):
            raise ValueError("导入内容不是 JSON 对象")
        kind = scope_type or str(data.get("scope_type") or "")
        sid = int(scope_id or data.get("scope_id") or 0)
        if kind not in ("group", "private") or sid <= 0:
            raise ValueError("缺少有效的会话类型/号码")
        counts = {"profiles": 0, "memories": 0, "loops": 0, "episodes": 0, "events": 0, "messages": 0}
        chat = data.get("chat") or {}
        if isinstance(chat, dict) and chat.get("title"):
            self.upsert_chat(kind, sid, title=str(chat.get("title") or ""))
        import json as _json

        for message in data.get("messages") or []:
            if not isinstance(message, dict):
                continue
            mid = int(message.get("message_id") or 0)
            ts = int(message.get("ts") or 0)
            text = str(message.get("text") or "")
            sender_qq = int(message.get("sender_qq") or 0)
            if mid and self.message_exists(mid):
                continue
            if ts and self._one(
                "SELECT id FROM messages WHERE scope_type = ? AND scope_id = ? "
                "AND ts = ? AND sender_qq = ? AND text = ? LIMIT 1",
                (kind, sid, ts, sender_qq, text),
            ):
                continue
            segments = message.get("segments_json") or "[]"
            raw = message.get("raw_json") or "{}"
            try:
                segments_data = _json.loads(segments) if isinstance(segments, str) else segments
            except ValueError:
                segments_data = []
            try:
                raw_data = _json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                raw_data = {}
            self.add_message(
                kind,
                sid,
                message_id=mid,
                sender_qq=sender_qq,
                sender_name=str(message.get("sender_name") or ""),
                text=text,
                segments=segments_data if isinstance(segments_data, list) else [],
                raw=raw_data if isinstance(raw_data, dict) else {},
                ts=ts,
                is_bot=bool(message.get("is_bot")),
                replied_by_bot=bool(message.get("replied_by_bot")),
            )
            counts["messages"] += 1
        for profile in data.get("profiles") or []:
            if not isinstance(profile, dict):
                continue
            qq = int(profile.get("qq") or 0)
            if qq <= 0:
                continue
            fields = {
                key: profile[key]
                for key in self.PROFILE_FIELDS
                if key in profile and profile[key] is not None
            }
            manual = (
                bool(profile.get("is_override"))
                if "is_override" in profile
                else None
            )
            self.update_profile(kind, sid, qq, manual=manual, **fields)
            counts["profiles"] += 1
        for memory in data.get("memories") or []:
            if not isinstance(memory, dict) or not memory.get("content"):
                continue
            self.add_memory(
                kind,
                sid,
                str(memory.get("content")),
                subject_qq=int(memory.get("subject_qq") or 0),
                kind=str(memory.get("kind") or "fact"),
                importance=int(memory.get("importance") or 3),
                confidence=float(memory.get("confidence") or 0.5),
                source_msg_ids=str(memory.get("source_msg_ids") or ""),
            )
            counts["memories"] += 1
        for loop in data.get("open_loops") or []:
            if not isinstance(loop, dict) or not loop.get("content"):
                continue
            self.add_open_loop(
                kind,
                sid,
                str(loop.get("content")),
                subject_qq=int(loop.get("subject_qq") or 0),
                kind=str(loop.get("kind") or "topic"),
            )
            counts["loops"] += 1
        for episode in data.get("episodes") or []:
            if not isinstance(episode, dict) or not episode.get("summary"):
                continue
            self.add_episode(
                kind,
                sid,
                str(episode.get("summary")),
                subject_qq=int(episode.get("subject_qq") or 0),
                tone=str(episode.get("tone") or ""),
                topics=str(episode.get("topics") or ""),
                msg_count=int(episode.get("msg_count") or 0),
            )
            counts["episodes"] += 1
        for event in data.get("relationship_events") or []:
            if not isinstance(event, dict) or not event.get("content"):
                continue
            self.add_relationship_event(
                kind,
                sid,
                str(event.get("content")),
                subject_qq=int(event.get("subject_qq") or 0),
                kind=str(event.get("kind") or "event"),
                importance=int(event.get("importance") or 3),
            )
            counts["events"] += 1
        return counts

    def purge_memories(
        self, scope_type: str, scope_id: int, subject_qq: int | None = None
    ) -> int:
        if subject_qq:
            cursor = self._execute(
                "DELETE FROM memories WHERE scope_type = ? AND scope_id = ? AND subject_qq = ?",
                (scope_type, scope_id, subject_qq),
            )
        else:
            cursor = self._execute(
                "DELETE FROM memories WHERE scope_type = ? AND scope_id = ?",
                (scope_type, scope_id),
            )
        return cursor.rowcount or 0

    def forget_by_keyword(
        self, scope_type: str, scope_id: int, keyword: str
    ) -> int:
        cursor = self._execute(
            "DELETE FROM memories WHERE scope_type = ? AND scope_id = ? AND content LIKE ?",
            (scope_type, scope_id, f"%{keyword}%"),
        )
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # summaries
    # ------------------------------------------------------------------
    def add_summary(
        self,
        scope_type: str,
        scope_id: int,
        content: str,
        *,
        from_ts: int = 0,
        to_ts: int = 0,
        msg_count: int = 0,
    ) -> int:
        cursor = self._execute(
            "INSERT INTO summaries(scope_type, scope_id, from_ts, to_ts, content, msg_count, "
            "created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (scope_type, scope_id, from_ts, to_ts, content, msg_count, _now()),
        )
        return int(cursor.lastrowid or 0)

    def latest_summaries(
        self, scope_type: str, scope_id: int, limit: int = 2
    ) -> list[dict]:
        rows = self._query(
            "SELECT * FROM summaries WHERE scope_type = ? AND scope_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (scope_type, scope_id, limit),
        )
        return [dict(row) for row in rows]

    def delete_summaries(self, scope_type: str, scope_id: int) -> None:
        self._execute(
            "DELETE FROM summaries WHERE scope_type = ? AND scope_id = ?",
            (scope_type, scope_id),
        )

    # ------------------------------------------------------------------
    # 用量 / 审计
    # ------------------------------------------------------------------
    def add_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        purpose: str = "",
        scope_key: str = "",
    ) -> None:
        self._execute(
            "INSERT INTO llm_usage(ts, model, prompt_tokens, completion_tokens, purpose, scope_key) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (_now(), model, prompt_tokens, completion_tokens, purpose, scope_key),
        )

    def usage_summary(self, since_ts: int = 0) -> dict:
        row = self._one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS pt, "
            "COALESCE(SUM(completion_tokens),0) AS ct FROM llm_usage WHERE ts >= ?",
            (since_ts,),
        )
        if not row:
            return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        return {
            "calls": int(row["calls"] or 0),
            "prompt_tokens": int(row["pt"] or 0),
            "completion_tokens": int(row["ct"] or 0),
        }

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        self._execute(
            "INSERT INTO audit(ts, actor, action, detail) VALUES(?, ?, ?, ?)",
            (_now(), actor, action, detail[:2000]),
        )

    def recent_audit(self, limit: int = 100) -> list[dict]:
        rows = self._query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------
    def archive_stale_memories(self, days: int = 90) -> int:
        cutoff = _now() - days * 86400
        cursor = self._execute(
            "UPDATE memories SET archived = 1 WHERE archived = 0 AND importance <= 2 "
            "AND COALESCE(last_used_at, created_at) < ?",
            (cutoff,),
        )
        return cursor.rowcount or 0

    def prune_messages(self, keep_days: int = 365) -> int:
        cutoff = _now() - keep_days * 86400
        cursor = self._execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
        return cursor.rowcount or 0

    def vacuum(self) -> None:
        with self._lock:
            self.conn.execute("VACUUM")
            self.conn.commit()

    def stats(self) -> dict:
        def count(table: str) -> int:
            row = self._one(f"SELECT COUNT(*) AS n FROM {table}")
            return int(row["n"] or 0) if row else 0

        usage_today = self.usage_summary(_now() - 86400)
        return {
            "messages": count("messages"),
            "memories": count("memories"),
            "profiles": count("profiles"),
            "summaries": count("summaries"),
            "chats": count("chats"),
            "usage_24h": usage_today,
            "db_path": self.path,
        }
