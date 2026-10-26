-- DeepSeek QQ Bot 数据库结构（SQLite）
-- 约定：所有时间都是 Unix 秒（INTEGER）；scope_type 为 'group' 或 'private'

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- 跨群稳定的"人"信息
CREATE TABLE IF NOT EXISTS persons (
    qq          INTEGER PRIMARY KEY,
    nickname    TEXT    DEFAULT '',
    global_note TEXT    DEFAULT '',
    first_seen  INTEGER DEFAULT 0,
    last_seen   INTEGER DEFAULT 0
);

-- 某个会话里的成员档案（同一个人在不同群可以完全不同）
CREATE TABLE IF NOT EXISTS profiles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type   TEXT    NOT NULL,
    scope_id     INTEGER NOT NULL,
    qq           INTEGER NOT NULL,
    display_name TEXT    DEFAULT '',   -- 机器人怎么称呼他
    aliases      TEXT    DEFAULT '',   -- 逗号分隔的别名/外号
    identity     TEXT    DEFAULT '',   -- 身份：同事/学生/群主...
    relation     TEXT    DEFAULT '',   -- 与机器人的关系阶段
    tags         TEXT    DEFAULT '',   -- 逗号分隔的性格标签
    preferences  TEXT    DEFAULT '',   -- 喜欢什么
    taboos       TEXT    DEFAULT '',   -- 雷点
    notes        TEXT    DEFAULT '',   -- 自由备注（画像总结）
    affinity     INTEGER DEFAULT 50,   -- 亲密度 0-100
    mood         TEXT    DEFAULT '',   -- 最近的情绪基调
    is_override  INTEGER DEFAULT 0,    -- 1=人工改过，抽取器不许覆盖
    msg_count    INTEGER DEFAULT 0,
    last_seen    INTEGER DEFAULT 0,
    updated_at   INTEGER DEFAULT 0,
    UNIQUE(scope_type, scope_id, qq)
);
CREATE INDEX IF NOT EXISTS idx_profiles_qq ON profiles(qq);

-- 原始消息（短期记忆 + 回放调试）
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   INTEGER DEFAULT 0,
    scope_type   TEXT    NOT NULL,
    scope_id     INTEGER NOT NULL,
    sender_qq    INTEGER DEFAULT 0,
    sender_name  TEXT    DEFAULT '',
    text         TEXT    DEFAULT '',
    segments_json TEXT   DEFAULT '',
    raw_json     TEXT    DEFAULT '',
    ts           INTEGER DEFAULT 0,
    is_bot       INTEGER DEFAULT 0,
    recalled     INTEGER DEFAULT 0,
    replied_by_bot INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(scope_type, scope_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_mid ON messages(message_id);

-- 滚动摘要（中期记忆）
CREATE TABLE IF NOT EXISTS summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT    NOT NULL,
    scope_id   INTEGER NOT NULL,
    from_ts    INTEGER DEFAULT 0,
    to_ts      INTEGER DEFAULT 0,
    content    TEXT    DEFAULT '',
    msg_count  INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_summaries_scope ON summaries(scope_type, scope_id, id);

-- 长期记忆：一条条原子事实
CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type     TEXT    NOT NULL,
    scope_id       INTEGER NOT NULL,
    subject_qq     INTEGER DEFAULT 0,   -- 0 表示"关于这个会话本身"
    kind           TEXT    DEFAULT 'fact',
    content        TEXT    NOT NULL,
    importance     INTEGER DEFAULT 3,   -- 1-5
    confidence     REAL    DEFAULT 0.5, -- 0-1，模型/人工对这条事实的把握
    source_msg_ids TEXT    DEFAULT '',
    created_at     INTEGER DEFAULT 0,
    last_confirmed_at INTEGER DEFAULT 0,
    last_used_at   INTEGER DEFAULT 0,
    use_count      INTEGER DEFAULT 0,
    archived       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_type, scope_id, archived);
CREATE INDEX IF NOT EXISTS idx_memories_subject ON memories(subject_qq);

-- 一段对话的情节记忆（比滚动摘要更完整，带情绪和话题）
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type  TEXT    NOT NULL,
    scope_id    INTEGER NOT NULL,
    subject_qq  INTEGER DEFAULT 0,
    from_ts     INTEGER DEFAULT 0,
    to_ts       INTEGER DEFAULT 0,
    summary     TEXT    DEFAULT '',
    tone        TEXT    DEFAULT '',
    topics      TEXT    DEFAULT '',
    msg_count   INTEGER DEFAULT 0,
    created_at  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes(scope_type, scope_id, id);

-- 关系时间线：第一次私聊、约定、冲突、道歉、里程碑、共同的梗
CREATE TABLE IF NOT EXISTS relationship_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type  TEXT    NOT NULL,
    scope_id    INTEGER NOT NULL,
    subject_qq  INTEGER DEFAULT 0,
    kind        TEXT    DEFAULT 'event',
    content     TEXT    NOT NULL,
    importance  INTEGER DEFAULT 3,
    created_at  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_scope ON relationship_events(scope_type, scope_id, id);

-- 图片卡缓存：同一张图（按 sha256）只理解一次
CREATE TABLE IF NOT EXISTS image_cards (
    hash        TEXT    PRIMARY KEY,
    scope_type  TEXT    DEFAULT '',
    scope_id    INTEGER DEFAULT 0,
    kind        TEXT    DEFAULT 'other',
    summary     TEXT    DEFAULT '',
    ocr         TEXT    DEFAULT '',
    meme        TEXT    DEFAULT '',
    emotion     TEXT    DEFAULT '',
    reply_angles TEXT   DEFAULT '',
    safety      TEXT    DEFAULT 'unknown',
    raw_json    TEXT    DEFAULT '',
    created_at  INTEGER DEFAULT 0
);

-- 表情包库：文件、来源、许可、标签、使用统计
CREATE TABLE IF NOT EXISTS stickers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hash        TEXT    UNIQUE,
    path        TEXT    NOT NULL,
    scope_type  TEXT    DEFAULT '',   -- 空 = 全局池
    scope_id    INTEGER DEFAULT 0,
    sender_qq   INTEGER DEFAULT 0,
    source      TEXT    DEFAULT '',
    license     TEXT    DEFAULT '',
    kind        TEXT    DEFAULT 'sticker',
    emotion     TEXT    DEFAULT '',
    tags        TEXT    DEFAULT '',
    ocr         TEXT    DEFAULT '',
    vector      BLOB,
    nsfw        INTEGER DEFAULT 0,
    enabled     INTEGER DEFAULT 1,
    used_count  INTEGER DEFAULT 0,
    last_used_at INTEGER DEFAULT 0,
    created_at  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_stickers_scope ON stickers(scope_type, scope_id, enabled);

-- 还记着的事：约定、没做完的计划、对方提过的话题
CREATE TABLE IF NOT EXISTS open_loops (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type     TEXT    NOT NULL,
    scope_id       INTEGER NOT NULL,
    subject_qq     INTEGER DEFAULT 0,
    kind           TEXT    DEFAULT 'topic',   -- topic|promise|plan|question
    content        TEXT    NOT NULL,
    status         TEXT    DEFAULT 'open',    -- open|resolved|dropped
    due_ts         INTEGER DEFAULT 0,
    source_msg_ids TEXT    DEFAULT '',
    created_at     INTEGER DEFAULT 0,
    updated_at     INTEGER DEFAULT 0,
    resolved_at    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_loops_scope ON open_loops(scope_type, scope_id, status);
CREATE INDEX IF NOT EXISTS idx_loops_subject ON open_loops(subject_qq, status);

-- 会话元数据（群名、开关、专属人设文件）
CREATE TABLE IF NOT EXISTS chats (
    scope_key         TEXT PRIMARY KEY,
    scope_type        TEXT NOT NULL,
    scope_id          INTEGER NOT NULL,
    title             TEXT DEFAULT '',
    enabled           INTEGER DEFAULT 1,
    proactive_enabled INTEGER DEFAULT 1,
    persona_path      TEXT DEFAULT '',
    created_at        INTEGER DEFAULT 0,
    updated_at        INTEGER DEFAULT 0
);

-- 运行时设置（面板热改项）
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

-- 可选的语义向量（本地 embedding；provider 关闭时这张表是空的）
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id  INTEGER PRIMARY KEY,
    model      TEXT    DEFAULT '',
    dim        INTEGER DEFAULT 0,
    vector     BLOB,
    updated_at INTEGER DEFAULT 0
);

-- 内部状态（上次抽取到第几条、上次摘要时间等）
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

-- LLM 用量（成本观测）
CREATE TABLE IF NOT EXISTS llm_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                INTEGER DEFAULT 0,
    model             TEXT    DEFAULT '',
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    purpose           TEXT    DEFAULT '',
    scope_key         TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts);

-- 操作审计
CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     INTEGER DEFAULT 0,
    actor  TEXT DEFAULT '',
    action TEXT DEFAULT '',
    detail TEXT DEFAULT ''
);
