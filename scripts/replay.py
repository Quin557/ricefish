"""回放：把录制下来的 OneBot 事件重新喂给机器人，检查决策是否合理。

用法：
    python scripts/replay.py data/events.jsonl            # 每行一个 JSON 事件
    python scripts/replay.py data/events.jsonl --verbose  # 打印每条的决策

不会真的发消息（强制 dry_run），也不会调用模型（用 echo）。
适合改完触发规则后做回归。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)
os.environ["RICEFISH_DB"] = ":memory:"

from ricefish.app import BotApp  # noqa: E402
from ricefish.config import Config  # noqa: E402
from ricefish.llm.echo import EchoLLM  # noqa: E402
from ricefish.onebot import events as ev  # noqa: E402


async def run(path: Path, verbose: bool) -> int:
    config = Config.load()
    config.bot.dry_run = True
    app = BotApp(config, llm=EchoLLM())
    app.client.self_id = 999

    events = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"读到 {len(events)} 条事件")
    for index, raw in enumerate(events):
        event = ev.parse(raw, app.client.self_id)
        if event is None:
            continue
        await app.dispatcher.on_event(event)
        if verbose and isinstance(event, ev.MessageEvent):
            text = event.text[:40]
            print(f"  #{index} {event.scope_key} {event.sender_name}: {text}")

    stats = app.dispatcher.stats
    print("\n回放结果：")
    print(f"  消息 {stats['messages']}  回复 {stats['replies']}  主动 {stats['proactive']}  "
          f"忽略 {stats['ignored']}  模型错误 {stats['llm_errors']}")
    print(f"  发送计数 {app.sender.sent_count}（dry-run 只打日志）")
    await app.shutdown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="jsonl 事件文件")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"文件不存在：{path}")
        return 2
    return asyncio.run(run(path, args.verbose))


if __name__ == "__main__":
    sys.exit(main())
