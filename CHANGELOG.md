# 更新日志

## 0.3.1（2026-10-26）

### 修复

- Windows 精简版 Python 没有 IANA `tzdata` 时，自动回退到固定 UTC+8，
  避免启动时 `ZoneInfoNotFoundError: Asia/Shanghai`。
- 补上 Windows CI 的验证，确保一键脚本在干净 Python 环境可用。

## 0.3.0（2026-10-26）

### 开源

- 首个公开版本：仓库只保留代码、通用配置、素材、文档和安装脚本。
- 个人 QQ/群号/白名单、API Key、聊天数据库、NapCat、二维码、日志、
  个人群人设和「后来学到的设定」全部留在本地，不进入仓库和发行包。
- 新增面向零基础用户的安装章节和 `使用说明.txt`。
- 新增 `scripts/build_release.py`，生成可直接分发的 `ricefish-v0.3.0.zip`；
  首次启动仍然走 `start.bat` 一键安装（下载 NapCat、填配置、扫码）。

### 陪伴

- 新增私聊主动陪伴：空闲/补话/冷却/每日配额/静默时段，管理员可全局和按会话开关。
- 主动消息会参考关系、亲密度、喜好、备注、长期记忆、情节、open loops 和当前心情。
- 新增「还记着的事」（open loops）：约定、计划、没聊完的话题；面板可完成/删除，45 天自动过期。
- 新增关系时间线：第一次、约定、冲突、和好、里程碑、梗。
- 新增情节记忆 episodes：每段对话的完整摘要 + 情绪 + 话题。
- 事实记忆增加 confidence、最后确认时间；检索加入置信度和衰减。
- 新增 ChatSession 视图，dispatcher 和面板共用同一套上下文口径。
- 新增私聊人设 `config/personas/private/<QQ>.md`。
- 会话详情改成 成员/情节/关系/记忆/消息 tabs；支持导出 JSON、导入 JSON（消息去重）。

### 识图

- 修复视觉调用被思考链吃光输出、返回空内容的问题。
- 图片卡：画面/OCR/梗/情绪/可接话/安全；按 sha256 持久化缓存。
- 尝试 NapCat `ocr_image` 先读字；多图最多 4 张。
- 新增 `look_closer` 工具：按问题放大/再看一眼。
- 新增零手工识图评测：`scripts/build_vision_eval.py` + `scripts/eval_vision.py`。

### 表情

- 新增表情库：hash 去重、来源/许可、情绪/标签、向量、使用统计、启用/停用。
- 内置表情和群友收件箱自动入库；面板可改标签、分池（全局/群/私聊）、自动打标、NSFW 停用。
- 发送时按当前话题做轻量向量检索，并避开同会话上一张。

### 流式

- 过滤纯标点/省略号碎片；一轮最多 2 条；首条等完整句。
- 慢首 token：表情回应/短确认+撤回；可选「想了 N 秒…」提示。
- 分条停顿模拟打字；首 token 看门狗。

### 工程

- git/CI（Windows+Ubuntu、3.10/3.12、ruff、docker build）。
- Dockerfile / docker-compose，面板 `/healthz`、`/metrics`。
- SQLite 在线备份脚本；轻量列迁移（`user_version`）。
- 文档：ARCHITECTURE / DEPLOY / PANEL / VISION_EVAL / SECURITY / CONTRIBUTING。
- 256 个离线测试。

## 0.2.0

- 本地 QQ 群聊/私聊机器人、三层记忆、面板、语音/表情/梗图、插件、游戏。
