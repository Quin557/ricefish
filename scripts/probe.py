"""探测 OneBot 反向 WS 是否能连上（NapCat 没配好时最快的排查手段）。

    python scripts/probe.py
    python scripts/probe.py --url ws://127.0.0.1:8080/onebot/v11/ws --token xxx

注意：本程序是"服务端"，NapCat 来连它。所以这个脚本其实是冒充 NapCat
连一下本机端口，验证监听与 token 是否正常。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ricefish.onebot.wsclient import WsClient  # noqa: E402


async def run(url: str, token: str) -> int:
    from urllib.parse import urlsplit

    split = urlsplit(url)
    try:
        client = await WsClient.connect(
            host=split.hostname or "127.0.0.1",
            port=split.port or 8080,
            path=split.path or "/",
            token=token,
            timeout=5,
        )
    except Exception as exc:
        print(f"连接失败：{exc}")
        print("检查：机器人是否已在运行？端口是否一致？token 是否一致？")
        return 1
    print("握手成功。等待 3 秒看有没有事件/心跳……")
    try:
        while True:
            raw = await client.recv_text(timeout=3)
            print(f"收到：{raw[:300]}")
    except asyncio.TimeoutError:
        print("3 秒内没有数据（正常，NapCat 一般 30 秒一次心跳）")
    finally:
        await client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8080/onebot/v11/ws")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.token))


if __name__ == "__main__":
    sys.exit(main())
