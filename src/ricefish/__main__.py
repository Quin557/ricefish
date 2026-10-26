"""命令行入口：python -m ricefish 或 python run.py。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .app import BotApp
from .config import Config
from .util.logging import setup_logging

log = logging.getLogger("ricefish.main")

BANNER = """
  +-----------------------------------------+
  |  ricefish · 一条住在 QQ 里的白饭鱼       |
  +-----------------------------------------+
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ricefish", description="ricefish · 本地运行的 QQ 群聊/私聊 AI 机器人"
    )
    parser.add_argument("-c", "--config", default="config/bot.yaml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="强制试运行：不真的发消息")
    parser.add_argument("--no-web", action="store_true", help="不启动管理面板")
    parser.add_argument("--port", type=int, default=0, help="覆盖 OneBot 监听端口")
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING")
    parser.add_argument("--check", action="store_true", help="只做自检然后退出")
    parser.add_argument(
        "--echo", action="store_true", help="回声模式：不调用模型，用来验证 QQ 链路"
    )
    return parser


def self_check(config: Config) -> int:
    problems = []
    if not config.llm.api_key:
        problems.append("DEEPSEEK_API_KEY 没设置（.env 里配）")
    if not config.access.allowed_groups and not config.access.allowed_friends:
        problems.append("白名单为空，机器人不会回复任何人（可在面板「白名单」添加）")
    try:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t(x)")
        conn.close()
    except Exception as exc:
        problems.append(f"SQLite 不可用：{exc}")
    try:
        from .web import templates  # noqa: F401
        from .core.dispatcher import Dispatcher  # noqa: F401
    except Exception as exc:
        problems.append(f"模块导入失败：{exc}")

    print(BANNER)
    print(f"配置：{getattr(config, 'source_path', '')}")
    print(f"机器人：{config.bot.name} / 模式 {config.bot.mode} / dry_run={config.bot.dry_run}")
    print(f"模型：{config.llm.model} @ {config.llm.base_url}")
    print(f"OneBot：ws://{config.onebot.host}:{config.onebot.port}{config.onebot.path}")
    print(f"白名单：群 {config.access.allowed_groups}，好友 {config.access.allowed_friends}")

    try:
        from .features.emoji import EmojiBook
        from .features.memes import MemeBook
        from .features.voice import VoicePack

        emojis = EmojiBook.load()
        memes = MemeBook.load(config.tools.meme_pack)
        voices = VoicePack.load(config.tools.voice_pack)
        print(
            f"素材：表情包 {len(emojis)} 张，梗图 {len(memes)} 张，"
            f"语音 {len(voices.lines)} 句"
        )
        if config.tools.voice_pack and not voices.enabled:
            problems.append(f"语音包没加载：{config.tools.voice_pack}")
        if config.tools.meme_pack and not memes.enabled:
            problems.append(f"梗图包没加载：{config.tools.meme_pack}")
    except Exception as exc:  # pragma: no cover - 自检兜底
        problems.append(f"素材加载失败：{exc}")
    if problems:
        print("\n需要处理：")
        for item in problems:
            print(f"  ! {item}")
        return 1
    print("\n自检通过。")
    return 0


async def _run(config: Config, start_panel: bool, echo: bool = False) -> None:
    llm = None
    if echo:
        from .llm.echo import EchoLLM

        llm = EchoLLM()
        print("  ⚠ 回声模式：不会调用 DeepSeek，只回显你最后一句。\n")
    app = BotApp(config, llm=llm)
    panel_url = ""
    if start_panel and config.web.enabled:
        from .web.server import PanelServer

        panel = PanelServer(app, config.web.host, config.web.port, config.web.token)
        panel_url = panel.start()
        app.panel = panel
    await app.start()

    print(BANNER)
    print(f"  机器人：{config.bot.name}   模式：{config.bot.mode}   dry_run：{config.bot.dry_run}")
    print(f"  管理面板：{panel_url or '（未启用）'}")
    print(f"  协议端：ws://{config.onebot.host}:{config.onebot.port}{config.onebot.path}")
    print("  按 Ctrl+C 退出\n")

    try:
        await asyncio.Event().wait()
    finally:
        await app.shutdown()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(level=args.log_level)

    # 让 .env 也能生效（没有 python-dotenv，十几行自己实现）
    _load_dotenv(".env")

    try:
        config = Config.load(args.config)
    except Exception as exc:
        print(f"读取配置失败：{exc}")
        return 2

    if args.dry_run:
        config.bot.dry_run = True
    if args.port:
        config.onebot.port = args.port
    if args.no_web:
        config.web.enabled = False

    if args.check:
        return self_check(config)

    try:
        asyncio.run(_run(config, start_panel=not args.no_web, echo=args.echo))
    except KeyboardInterrupt:
        print("\n已退出。")
    except OSError as exc:
        text = str(exc)
        if getattr(exc, "errno", None) in (98, 10048) or "10048" in text:
            print(
                "\n端口被占用了：通常已经有一个 ricefish 在跑（8080/8721）。\n"
                "先关掉旧实例，或改 config/bot.yaml 的 onebot.port / web.port。"
            )
            return 3
        raise
    return 0


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
