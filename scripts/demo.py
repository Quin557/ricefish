"""假装自己是 NapCat，给运行中的机器人发一条消息，看看它怎么回。

不需要 QQ，也不需要真的登录——用来验证链路、调触发规则、看人设效果。

    # 先启动机器人（另一个窗口）
    python run.py --echo

    # 再发一条群消息
    python scripts/demo.py --group 200 --user 300 --text "肥鱼在吗"

    # 连发几条，看它会不会插嘴
    python scripts/demo.py --group 200 --user 300 --text "今天好热" --text "有人吗"

注意：机器人那边要有白名单，否则它不会理你（这是故意的）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ricefish.onebot.wsclient import WsClient  # noqa: E402


class FakeNapCat:
    def __init__(self, ws: WsClient, own_qq: int) -> None:
        self.ws = ws
        self.own_qq = own_qq
        # 用时间做起点，避免重复运行脚本时被机器人的去重逻辑吃掉
        import time

        self.msg_id = int(time.time() * 1000) % 10**9
        self.running = True

    async def pump(self) -> None:
        while self.running:
            try:
                payload = await self.ws.recv_json(timeout=0.3)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return
            action = payload.get("action")
            if not action:
                continue
            if action in ("send_group_msg", "send_private_msg"):
                segments = payload.get("params", {}).get("message") or []
                text = "".join(
                    item.get("data", {}).get("text", "")
                    for item in segments
                    if item.get("type") == "text"
                )
                other = [item.get("type") for item in segments if item.get("type") != "text"]
                print(f"  ← {text}" + (f"  {other}" if other else ""))
            elif action == "get_login_info":
                data = {"user_id": self.own_qq, "nickname": "肥鱼"}
            elif action == "get_group_list":
                data = [{"group_id": 200, "group_name": "演示群"}]
            elif action == "get_friend_list":
                data = [{"user_id": 300, "nickname": "阿澈"}]
            elif action == "get_group_member_info":
                data = {"user_id": 300, "card": "澈哥", "nickname": "阿澈"}
            else:
                data = {"message_id": self.msg_id}
                self.msg_id += 1
            await self.ws.send_json(
                {"status": "ok", "retcode": 0, "data": data, "echo": payload.get("echo")}
            )

    async def say(self, text: str, group: int | None, user: int) -> None:
        self.msg_id += 1
        if group:
            payload = {
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "message_id": self.msg_id,
                "group_id": group,
                "user_id": user,
                "self_id": self.own_qq,
                "time": 1700000000,
                "message": [
                    {"type": "at", "data": {"qq": str(self.own_qq)}},
                    {"type": "text", "data": {"text": f" {text}"}},
                ],
                "sender": {"user_id": user, "nickname": "阿澈", "card": "澈哥"},
            }
        else:
            payload = {
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "message_id": self.msg_id,
                "user_id": user,
                "self_id": self.own_qq,
                "time": 1700000000,
                "message": text,
                "sender": {"user_id": user, "nickname": "阿澈"},
            }
        scope = f"群 {group}" if group else "私聊"
        print(f"  → [{scope}] {text}")
        await self.ws.send_json(payload)


async def run(args) -> int:
    try:
        ws = await WsClient.connect(
            host=args.host, port=args.port, path=args.path, token=args.token, timeout=5
        )
    except Exception as exc:
        print(f"连不上 {args.host}:{args.port}：{exc}")
        print("确认机器人已经启动（python run.py --echo），端口和 token 对得上。")
        return 1

    napcat = FakeNapCat(ws, args.own_qq)
    pump = asyncio.create_task(napcat.pump())
    print(f"已连上机器人（伪装成 NapCat，自己的号 {args.own_qq}）")
    try:
        for text in args.text:
            await napcat.say(text, args.group if not args.private else None, args.user)
            await asyncio.sleep(args.interval)
        print(f"等 {args.wait} 秒看回复……")
        await asyncio.sleep(args.wait)
    finally:
        napcat.running = False
        pump.cancel()
        try:
            await ws.close()
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="给运行中的 ricefish 发测试消息")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--path", default="/onebot/v11/ws")
    parser.add_argument("--token", default="")
    parser.add_argument("--group", type=int, default=200)
    parser.add_argument("--user", type=int, default=300)
    parser.add_argument("--own-qq", type=int, default=999, dest="own_qq")
    parser.add_argument("--private", action="store_true", help="发私聊而不是群聊")
    parser.add_argument("--text", action="append", default=[], help="要发的内容，可重复")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--wait", type=float, default=6)
    args = parser.parse_args()
    if not args.text:
        args.text = ["肥鱼在吗"]
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
