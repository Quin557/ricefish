# 架构

## 一句话

OneBot 11 事件 → 触发/权限 → 会话上下文（人设/关系/记忆/图片卡/表情）→ LLM →
分条/流式发送 → 记忆策展。

## 目录

```
src/ricefish/
├─ onebot/      WS 服务端、事件解析、OneBot 动作
├─ core/        触发、限流、并发闸门、发送、调度中心
├─ companion/   ChatSession：把一个人/群的档案、情节、关系、开放话题收在一起
├─ vision/      取图、OCR 提示、图片卡、look_closer
├─ memory/      SQLite、schema/轻量迁移、检索、策展、可选向量
├─ llm/         DeepSeek/OpenAI 兼容客户端、提示词、抽取器、标签解析
├─ features/    命令、游戏、表情库、主动陪伴、语音、工具、周报
├─ web/         标准库 HTTP 面板 + PWA + panel.js
└─ util/        文本、日志、轻量向量
```

## 数据流

1. **接入**：`onebot/wsserver.py` 收 NapCat 反向 WS，`events.py` 解析。
2. **入库与权限**：`Dispatcher.handle_message` 落 `messages`，查白名单/会话开关/静音。
3. **决策**：`core/trigger.py` 决定 @/回复/私聊/主动插话；`ratelimit.py` 限流。
4. **上下文**：
   - `ChatSession.load()` 取档案、最近情节、关系事件、open loops；
   - `vision` 把图变成图片卡（先查 `image_cards` hash 缓存）；
   - `PromptBuilder.build()` 按「稳定人设 → 动态档案/记忆/情节 → 历史 → 当前」拼提示词。
5. **生成**：流式优先；慢首 token 给表情回应/短确认；工具调用退回非流式并回填。
6. **发送**：`Sender` 拆条、打字延迟、引用、撤回、表情库选图。
7. **记忆**：`Curator` 定时跑 `Extractor`，写 facts/episodes/open loops/relationship events；
   维护任务清理过期话题、归档低重要度记忆、备份/VACUUM。

## 数据表

| 表 | 用途 |
|---|---|
| `messages` | 原始消息（短期上下文 + 回放） |
| `persons` / `profiles` | 跨群人物 + 每会话成员档案 |
| `summaries` / `episodes` | 滚动摘要 + 更完整的情节记忆 |
| `memories` | 原子事实，带 importance/confidence/来源/确认时间 |
| `open_loops` | 约定、计划、没聊完的话题 |
| `relationship_events` | 关系时间线 |
| `image_cards` | 按 sha256 缓存的图片理解结果 |
| `stickers` | 表情库：路径、来源、许可、标签、向量、使用统计 |
| `chats` / `settings` / `state` | 会话开关、热改设置、游标/配额 |
| `llm_usage` / `audit` | 成本账本、操作审计 |

迁移：`MemoryStore._init_schema()` 执行 `schema.sql`（全部 `IF NOT EXISTS`），
`_ensure_columns()` 给老库补列并写 `PRAGMA user_version`。新增表可直接上线；
改列必须走 `_ensure_columns`，不要 `DROP`。

## 配置优先级

`默认值 < config/bot.yaml < 环境变量 < 面板运行时设置（DB）`。
白名单是特例：第一次面板保存后以 `settings["access.groups"]/["access.friends"]` 为准，
`.env`/`bot.yaml` 只做种子。

## 扩展点

- **新工具**：`features/toolbox.py` 加 spec + `run` 分支。
- **新命令**：`features/commands.py` 的 `_table()` + `HELP_TEXT`。
- **新表情来源**：`MemoryStore.add_sticker()` + 面板。
- **新模型**：`config.llm.base_url/model`，兼容 OpenAI `/chat/completions`。
- **新面板页**：`web/templates.py` 加 `page_*`，`web/server.py` 加路由。
