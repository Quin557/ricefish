"""OneBot 11 动作（API）封装。

所有方法都通过 `self.call(action, params)` 发请求，由 `OneBotClient` 负责
收发与 echo 匹配。返回值统一是 OneBot 的 `data` 字段。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class OneBotError(Exception):
    """OneBot 返回了非 0 retcode。"""

    def __init__(self, retcode: int, message: str = "", action: str = "") -> None:
        self.retcode = retcode
        self.message = message
        self.action = action
        super().__init__(f"[{action}] retcode={retcode} {message}".strip())


class NotConnected(OneBotError):
    """当前没有连接到协议端。"""

    def __init__(self) -> None:
        super().__init__(-1, "onebot 未连接")


CallFn = Callable[[str, dict[str, Any], float], Awaitable[Any]]


class ActionsMixin:
    """假定宿主类提供 `call(action, params, timeout)`。"""

    call: CallFn  # type: ignore[assignment]

    # -- 消息 --------------------------------------------------------
    async def send_group_msg(
        self, group_id: int, message: list[dict[str, Any]], *, timeout: float = 15
    ) -> Any:
        return await self.call(
            "send_group_msg", {"group_id": group_id, "message": message}, timeout
        )

    async def send_private_msg(
        self, user_id: int, message: list[dict[str, Any]], *, timeout: float = 15
    ) -> Any:
        return await self.call(
            "send_private_msg", {"user_id": user_id, "message": message}, timeout
        )

    async def delete_msg(self, message_id: int, *, timeout: float = 10) -> Any:
        return await self.call("delete_msg", {"message_id": message_id}, timeout)

    # -- 信息查询 ----------------------------------------------------
    async def get_login_info(self, *, timeout: float = 10) -> Any:
        return await self.call("get_login_info", {}, timeout)

    async def get_group_list(self, *, timeout: float = 15) -> Any:
        return await self.call("get_group_list", {}, timeout)

    async def get_group_member_list(self, group_id: int, *, timeout: float = 20) -> Any:
        return await self.call("get_group_member_list", {"group_id": group_id}, timeout)

    async def get_group_member_info(
        self, group_id: int, user_id: int, *, timeout: float = 15
    ) -> Any:
        return await self.call(
            "get_group_member_info",
            {"group_id": group_id, "user_id": user_id, "no_cache": False},
            timeout,
        )

    async def get_friend_list(self, *, timeout: float = 15) -> Any:
        return await self.call("get_friend_list", {}, timeout)

    async def get_stranger_info(self, user_id: int, *, timeout: float = 15) -> Any:
        return await self.call("get_stranger_info", {"user_id": user_id}, timeout)

    async def get_msg(self, message_id: int, *, timeout: float = 10) -> Any:
        return await self.call("get_msg", {"message_id": message_id}, timeout)

    # -- 互动 --------------------------------------------------------
    async def group_poke(self, group_id: int, user_id: int, *, timeout: float = 10) -> Any:
        return await self.call(
            "group_poke", {"group_id": group_id, "user_id": user_id}, timeout
        )

    async def friend_poke(self, user_id: int, *, timeout: float = 10) -> Any:
        return await self.call("friend_poke", {"user_id": user_id}, timeout)

    async def set_msg_emoji_like(
        self,
        message_id: int,
        emoji_id: str = "128077",
        *,
        set: bool = True,
        timeout: float = 10,
    ) -> Any:
        return await self.call(
            "set_msg_emoji_like",
            {"message_id": message_id, "emoji_id": emoji_id, "set": bool(set)},
            timeout,
        )

    async def get_image(self, file: str, *, timeout: float = 15) -> Any:
        return await self.call("get_image", {"file": file}, timeout)

    async def ocr_image(self, image: str, *, timeout: float = 20) -> Any:
        """NapCat 扩展动作：识别图片里的文字。不支持时会抛 OneBotError。"""
        return await self.call("ocr_image", {"image": image}, timeout)

    async def get_record(
        self, file: str, out_format: str = "mp3", *, timeout: float = 30
    ) -> Any:
        return await self.call(
            "get_record", {"file": file, "out_format": out_format}, timeout
        )

    async def get_file(self, file_id: str, *, timeout: float = 30) -> Any:
        return await self.call("get_file", {"file_id": file_id}, timeout)

    # -- 群管理 ------------------------------------------------------
    async def set_group_add_request(
        self, flag: str, approve: bool = True, reason: str = "", *, timeout: float = 10
    ) -> Any:
        return await self.call(
            "set_group_add_request",
            {"flag": flag, "type": "add", "approve": approve, "reason": reason},
            timeout,
        )

    async def set_friend_add_request(
        self, flag: str, approve: bool = True, *, timeout: float = 10
    ) -> Any:
        return await self.call(
            "set_friend_add_request", {"flag": flag, "approve": approve}, timeout
        )
