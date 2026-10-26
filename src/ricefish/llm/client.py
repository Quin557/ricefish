"""DeepSeek 调用客户端（OpenAI 兼容协议，标准库 urllib 实现）。

不依赖 openai/httpx：只发一个 POST /chat/completions，够用且不引入版本风险。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, TYPE_CHECKING

from ..config import LLMConfig
from ..util.text import estimate_tokens

if TYPE_CHECKING:  # pragma: no cover
    from ..memory.store import MemoryStore

log = logging.getLogger("ricefish.llm")


class LLMError(Exception):
    """模型调用失败。"""


@dataclass
class ChatResult:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    #: stop / length / tool_calls …；length 说明输出被截断
    finish_reason: str = ""
    #: 思考链长度，只用于诊断，永不发到 QQ
    reasoning_chars: int = 0
    #: 模型要求调用的工具：[{id, type, function:{name, arguments}}]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.text or "").strip() and not self.tool_calls


class DeepSeekClient:
    def __init__(
        self,
        config: LLMConfig,
        store: "MemoryStore | None" = None,
        timeout: float | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.timeout = timeout or float(config.timeout_sec)
        self._disabled_reason = ""

    # -- 基础 --------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.config.api_key) and not self._disabled_reason

    def _endpoint(self) -> str:
        base = (self.config.base_url or "https://api.deepseek.com").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _payload(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        response_format: dict | None,
        stream: bool,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if response_format:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        thinking_value = self.config.thinking if thinking is None else thinking
        if thinking_value:
            payload["thinking"] = {"type": thinking_value}
        return payload

    def _post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise LLMError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"网络错误: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise LLMError(f"请求失败: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"响应不是 JSON: {raw[:200]}") from exc
        if isinstance(data, dict) and data.get("error"):
            message = data["error"]
            if isinstance(message, dict):
                message = message.get("message", "")
            raise LLMError(f"接口返回错误: {message}")
        if not isinstance(data, dict):
            raise LLMError("响应格式异常")
        return data

    # -- 对外 --------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        purpose: str = "chat",
        scope_key: str = "",
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        thinking: str | None = None,
    ) -> ChatResult:
        if not self.available:
            raise LLMError("未配置 DEEPSEEK_API_KEY" if not self.config.api_key else self._disabled_reason)

        model = model or self.config.model
        temperature = self.config.temperature if temperature is None else temperature
        max_tokens = max_tokens or self.config.max_output_tokens
        thinking_value = self.config.thinking_for(purpose) if thinking is None else thinking
        payload = self._payload(
            messages, model, temperature, max_tokens, response_format, stream=False,
            tools=tools, tool_choice=tool_choice, thinking=thinking_value,
        )

        attempts = max(1, int(self.config.retries) + 1)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                started = time.monotonic()
                data = await asyncio.to_thread(self._post, payload, self.timeout)
                elapsed = time.monotonic() - started
                if elapsed >= 6.0:
                    log.warning(
                        "模型响应偏慢 %.1fs（purpose=%s scope=%s in≈%s）",
                        elapsed,
                        purpose,
                        scope_key or "-",
                        sum(estimate_tokens(str(m.get("content") or "")) for m in messages),
                    )
                return self._parse(data, model, messages, purpose, scope_key)
            except LLMError as exc:
                last_error = exc
                text = str(exc)
                if "401" in text or "403" in text:
                    # 鉴权失败重试没意义，直接停用
                    self._disabled_reason = "API Key 无效或无权限"
                    raise
                if "HTTP 4" in text and "429" not in text:
                    raise
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(str(last_error) if last_error else "未知错误")

    def _parse(
        self,
        data: dict[str, Any],
        model: str,
        messages: list[dict[str, Any]],
        purpose: str,
        scope_key: str,
    ) -> ChatResult:
        choices = data.get("choices") or []
        text = ""
        tool_calls: list[dict[str, Any]] = []
        finish_reason = ""
        reasoning_chars = 0
        if choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            message = choice.get("message") or {}
            finish_reason = str(choice.get("finish_reason") or "")
            reasoning_chars = len(str(message.get("reasoning_content") or ""))
            content = message.get("content")
            if isinstance(content, list):  # 多模态返回
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                text = "".join(parts)
            else:
                text = str(content or "")
            raw_calls = message.get("tool_calls")
            if isinstance(raw_calls, list):
                tool_calls = [c for c in raw_calls if isinstance(c, dict)]
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        if not prompt_tokens:
            prompt_tokens = sum(
                estimate_tokens(str(m.get("content") or "")) for m in messages
            )
        result = ChatResult(
            text=text.strip(),
            model=str(data.get("model") or model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens or estimate_tokens(text),
            raw=data,
            finish_reason=finish_reason,
            reasoning_chars=reasoning_chars,
            tool_calls=tool_calls,
        )
        if self.store is not None:
            try:
                self.store.add_usage(
                    result.model,
                    result.prompt_tokens,
                    result.completion_tokens,
                    purpose=purpose,
                    scope_key=scope_key,
                )
            except Exception:  # pragma: no cover - 统计失败不影响主流程
                log.debug("写入用量失败", exc_info=True)
        return result

    # -- 便捷 --------------------------------------------------------
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        purpose: str = "chat",
        scope_key: str = "",
        thinking: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式对话。

        逐条产出 ``{"type": "delta", "text": "..."}``，
        结束时产出 ``{"type": "done", "result": ChatResult}``。
        出错抛 LLMError，调用方可以退回非流式。
        """
        if not self.available:
            raise LLMError("未配置 DEEPSEEK_API_KEY" if not self.config.api_key else self._disabled_reason)

        model = model or self.config.model
        temperature = self.config.temperature if temperature is None else temperature
        max_tokens = max_tokens or self.config.max_output_tokens
        thinking_value = self.config.thinking_for(purpose) if thinking is None else thinking
        payload = self._payload(
            messages, model, temperature, max_tokens, None, stream=True,
            tools=tools, thinking=thinking_value,
        )
        payload["stream_options"] = {"include_usage": True}

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        thread = asyncio.create_task(
            asyncio.to_thread(self._stream_worker, payload, queue, loop)
        )

        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        model_name = model
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = ""
        try:
            while True:
                item = await queue.get()
                kind = item.get("type")
                if kind == "eof":
                    break
                if kind == "error":
                    raise LLMError(str(item.get("error") or "流式请求失败"))
                if kind == "usage":
                    usage = item.get("usage") or {}
                    prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens)
                    completion_tokens = int(usage.get("completion_tokens") or completion_tokens)
                    continue
                if kind == "model":
                    model_name = str(item.get("model") or model_name)
                    continue
                if kind == "finish":
                    finish_reason = str(item.get("reason") or finish_reason)
                    continue
                if kind != "delta":
                    continue
                delta = item.get("delta") or {}
                chunk = delta.get("content")
                if chunk:
                    text_parts.append(str(chunk))
                    yield {"type": "delta", "text": str(chunk)}
                for call in delta.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    index = int(call.get("index") or 0)
                    slot = calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if call.get("id"):
                        slot["id"] = str(call["id"])
                    function = call.get("function") or {}
                    if function.get("name"):
                        slot["function"]["name"] += str(function["name"])
                    if function.get("arguments"):
                        slot["function"]["arguments"] += str(function["arguments"])
        finally:
            try:
                await thread
            except Exception:
                pass

        text = "".join(text_parts)
        result = ChatResult(
            text=text.strip(),
            model=model_name,
            prompt_tokens=prompt_tokens or sum(
                estimate_tokens(str(m.get("content") or "")) for m in messages
            ),
            completion_tokens=completion_tokens or estimate_tokens(text),
            finish_reason=finish_reason,
            tool_calls=[calls[key] for key in sorted(calls)],
        )
        if self.store is not None:
            try:
                self.store.add_usage(
                    result.model,
                    result.prompt_tokens,
                    result.completion_tokens,
                    purpose=purpose,
                    scope_key=scope_key,
                )
            except Exception:  # pragma: no cover
                log.debug("写入用量失败", exc_info=True)
        yield {"type": "done", "result": result}

    def _stream_worker(
        self, payload: dict[str, Any], queue: "asyncio.Queue", loop
    ) -> None:
        """在线程里读 SSE，把分片丢回事件循环。"""
        def push(item: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._endpoint(),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.config.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("model"):
                        push({"type": "model", "model": obj.get("model")})
                    if obj.get("usage"):
                        push({"type": "usage", "usage": obj.get("usage")})
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta:
                            push({"type": "delta", "delta": delta})
                        if choice.get("finish_reason"):
                            push(
                                {
                                    "type": "finish",
                                    "reason": str(choice.get("finish_reason")),
                                }
                            )
        except Exception as exc:  # noqa: BLE001
            push({"type": "error", "error": f"流式请求失败: {exc}"})
        finally:
            push({"type": "eof"})

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float | None = 0.3,
        max_tokens: int | None = 1000,
        purpose: str = "utility",
        scope_key: str = "",
        thinking: str | None = None,
    ) -> str:
        result = await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            purpose=purpose,
            scope_key=scope_key,
            thinking=thinking,
        )
        return result.text

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int | None = 1200,
        purpose: str = "extract",
        scope_key: str = "",
        thinking: str | None = None,
    ) -> dict[str, Any] | None:
        """要求模型返回 JSON 对象；解析失败返回 None。"""
        try:
            result = await self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
                temperature=0.2,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                purpose=purpose,
                scope_key=scope_key,
                thinking=thinking,
            )
        except LLMError:
            return None
        return load_json_object(result.text)

    async def describe_image(
        self,
        image_url: str,
        prompt: str = "看这张图，用一句话说明画面和可接的话。",
        *,
        scope_key: str = "",
    ) -> str:
        """视觉兼容接口：返回文本。新代码请用 vision.cards.analyze_image。"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        max_tokens = max(200, int(getattr(self.config, "vision_max_tokens", 700) or 700))
        result = await self.chat(
            messages,
            model=self.config.vision_model,
            temperature=0.2,
            max_tokens=max_tokens,
            purpose="vision",
            scope_key=scope_key,
            thinking="disabled",
        )
        if not result.text.strip() and result.finish_reason == "length":
            # 思考/截断把 content 吃光：再给一次更宽的预算
            result = await self.chat(
                messages,
                model=self.config.vision_model,
                temperature=0.2,
                max_tokens=min(2000, max_tokens * 2),
                purpose="vision",
                scope_key=scope_key,
                thinking="disabled",
            )
        return result.text


def load_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None
