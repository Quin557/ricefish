# 参与贡献

## 开发环境

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
python -m unittest discover -s tests
```

不需要装依赖也能跑；`[dev]` 只是 pytest/ruff/mypy 等工具。

## 提交前

```bat
python -m unittest discover -s tests
ruff check src tests scripts
python run.py --check
```

- 改了行为就补测试；测试必须离线、不花钱、不连 QQ。
- 不提交 `.env`、`data/`、`vendor/`、`qr-now.png`、`setup.log`。
- 不提交真实群号/QQ 命名的 `config/personas/groups/*.md`、`config/personas/private/`、
  `config/personas/learned.md`，以及 `*.personal.*` 本地备份。
- 不在日志里打印 API Key、token、完整消息原文（需要时脱敏）。

## 代码风格

- Python 3.10+，标准库优先；新增运行时依赖要在 PR 里说明理由。
- 模块边界见 [docs/ARCHITECTURE.md](ARCHITECTURE.md)。
- 面向用户的中文文案放模板/配置，不要散落在逻辑里。
- 错误处理：聊天主链路失败不能把 bot 带崩；工具/视觉失败要降级。

## Commit / PR

- Conventional Commits：`feat: ...`、`fix: ...`、`docs: ...`、`test: ...`、`chore: ...`。
- PR 描述写：改了什么、为什么、怎么验证、对现有数据的影响。
- 涉及数据库改动必须说明迁移/回滚方式。
- 涉及素材必须附来源和许可。

## 发布

1. 更新 `CHANGELOG.md` 和 `pyproject.toml` 版本。
2. 跑全量测试 + `docker build`。
3. 本地生成发行包：`python scripts/build_release.py`（会校验并输出
   `dist/ricefish-vX.Y.Z.zip` 和 `.sha256`）。
4. 打 tag：`vX.Y.Z` 并推送；GitHub Actions 会重新构建并创建 Release。
5. 附带升级说明（是否需要备份、迁移、配置新增项）。
