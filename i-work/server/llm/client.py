from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger("iwork.llm")

# client.py 用裸 stdlib logger，structlog 的 colorize_event 对这里不生效，
# 颜色需直接内嵌在消息串中（色号沿用 observability/logging.py）
_C = "\033[1;36m"   # 青，常规事件
_R = "\033[1;31m"   # 红，异常/丢弃
_Z = "\033[0m"

# DeepSeek 请求的输出上限；兜底推断截断时也以此为阈值
_DEEPSEEK_MAX_TOKENS = 20000


@dataclass
class LLMChunk:
    """LLM 流式响应中的数据块。统一屏蔽不同厂商的差异。"""
    type: str  # "thinking", "text", "tool_use", "end_turn", "retry"
    delta: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_input: dict | None = None
    stop_reason: str | None = None
    usage: dict | None = None
    retry_code: str | None = None
    retry_attempt: int | None = None
    retry_max: int | None = None


class LLMClient(ABC):
    """LLM 客户端抽象。后续可扩展 OpenAI、DeepSeek 等实现。"""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[LLMChunk]: ...


class FakeLLMClient(LLMClient):
    """可预设响应的假 LLM 客户端，用于测试。按调用顺序依次回放预设的 chunk 列表。"""

    def __init__(self, responses: list[list[LLMChunk]] | None = None):
        self.responses = responses or []
        self._call_index = 0
        self.calls: list[dict] = []

    async def stream(self, messages, system, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if self._call_index < len(self.responses):
            chunks = self.responses[self._call_index]
            self._call_index += 1
            for e in chunks:
                yield e
        else:
            yield LLMChunk(type="text", delta="假回复。")
            yield LLMChunk(type="end_turn", stop_reason="end_turn")


class AnthropicLLMClient(LLMClient):
    """基于 Anthropic SDK 的真实 LLM 客户端。"""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic
        from server.config import settings
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key
        )
        self._model = model or settings.default_model

    async def stream(self, messages, system, tools=None, tool_choice=None):
        kwargs = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": messages,
            "system": system,
            "thinking": {"type": "enabled", "budget_tokens": 4096},
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = {"type": tool_choice}

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield LLMChunk(type="text", delta=event.delta.text)
                    elif event.delta.type == "thinking_delta":
                        yield LLMChunk(type="thinking", delta=event.delta.thinking)

            final = await stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    yield LLMChunk(
                        type="tool_use",
                        tool_name=block.name,
                        tool_call_id=block.id,
                        tool_input=block.input,
                    )
            yield LLMChunk(
                type="end_turn",
                stop_reason=final.stop_reason,
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                } if hasattr(final, "usage") else None,
            )


class DeepSeekLLMClient(LLMClient):
    """基于 DeepSeek API (OpenAI 兼容) 的 LLM 客户端，使用 httpx 流式调用。"""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        import httpx
        from server.config import settings
        self._api_key = api_key or settings.deepseek_api_key
        self._model = model or settings.default_model
        self._base_url = settings.deepseek_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), trust_env=True)

    @staticmethod
    def _normalize_schema(schema: dict) -> dict:
        """确保 schema 是一个合法的 JSON Schema（顶层必须包含 type: object）。"""
        if not schema:
            return {"type": "object", "properties": {}}
        if "type" not in schema:
            return {"type": "object", "properties": schema}
        return schema

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """将 Anthropic 风格的工具定义转为 OpenAI 风格。"""
        if not tools:
            return None
        openai_tools = []
        for t in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": self._normalize_schema(t.get("input_schema", {})),
                },
            })
        return openai_tools

    async def stream(self, messages, system, tools=None, tool_choice=None):
        import httpx
        import json
        import asyncio

        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body = {
            "model": self._model,
            "messages": api_messages,
            "stream": True,
            "max_tokens": _DEEPSEEK_MAX_TOKENS,
            "thinking": {"type": "enabled"},
        }
        if tools:
            body["tools"] = self._convert_tools(tools)
        if tool_choice:
            body["tool_choice"] = "none" if tool_choice == "none" else "auto"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                tool_call_buffers: dict[int, dict] = {}
                usage_data: dict | None = None
                terminal_sent = False
                async with self._client.stream(
                    "POST",
                    f"{self._base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        text = await response.aread()
                        raise httpx.HTTPStatusError(
                            f"DeepSeek {response.status_code}: {text.decode()[:500]}",
                            request=response.request,
                            response=response,
                        )

                    finish_reason = None
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if data.get("usage"):
                            u = data["usage"]
                            usage_data = {
                                "input_tokens": u.get("prompt_tokens", 0),
                                "output_tokens": u.get("completion_tokens", 0),
                            }

                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason")

                        if finish_reason is not None:
                            logger.info(
                                "%sdeepseek.finish_reason%s  value=%s  has_content=%s  has_rc=%s  completion_tokens=%s",
                                _C, _Z, finish_reason, bool(delta.get("content")),
                                bool(delta.get("reasoning_content")),
                                (usage_data or {}).get("output_tokens"),
                            )

                        if delta.get("reasoning_content"):
                            _rc = delta["reasoning_content"]
                            # logger.info("deepseek.delta  type=thinking  len=%d  preview=%.80s", len(_rc), _rc)
                            yield LLMChunk(type="thinking", delta=_rc)

                        if delta.get("content"):
                            _c = delta["content"]
                            # logger.info("deepseek.delta  type=text  len=%d  preview=%.80s", len(_c), _c)
                            yield LLMChunk(type="text", delta=_c)

                        tc_deltas = delta.get("tool_calls", [])
                        for tc in tc_deltas:
                            idx = tc.get("index", 0)
                            if idx not in tool_call_buffers:
                                tool_call_buffers[idx] = {"id": "", "name": "", "arguments": ""}
                            buf = tool_call_buffers[idx]
                            if tc.get("id"):
                                buf["id"] = tc["id"]
                            func = tc.get("function", {})
                            if func.get("name"):
                                buf["name"] = func["name"]
                            if func.get("arguments"):
                                buf["arguments"] += func["arguments"]

                        if finish_reason == "tool_calls":
                            terminal_sent = True
                            for idx in sorted(tool_call_buffers.keys()):
                                buf = tool_call_buffers[idx]
                                try:
                                    args = json.loads(buf["arguments"])
                                except json.JSONDecodeError as e:
                                    logger.warning(
                                        "llm.tool_parse_error  tool=%s  raw=%.200s  error=%s",
                                        buf["name"], buf["arguments"], e,
                                    )
                                    args = {
                                        "_parse_error": True,
                                        "_raw_arguments": buf["arguments"],
                                        "_error": str(e),
                                    }
                                yield LLMChunk(
                                    type="tool_use",
                                    tool_name=buf["name"],
                                    tool_call_id=buf["id"],
                                    tool_input=args,
                                )
                            tool_call_buffers.clear()

                        if finish_reason == "length" and not terminal_sent:
                            # 达到 max_tokens 上限被截断：归一化为 max_tokens，
                            # 交由引擎注入续写提示并重跑
                            logger.warning(
                                "%sdeepseek.truncated%s  finish_reason=length  completion_tokens=%s",
                                _R, _Z, (usage_data or {}).get("output_tokens"),
                            )
                            terminal_sent = True
                            yield LLMChunk(type="end_turn", stop_reason="max_tokens", usage=usage_data)

                        if finish_reason == "stop" and not terminal_sent:
                            terminal_sent = True
                            yield LLMChunk(type="end_turn", stop_reason="end_turn", usage=usage_data)

                        if finish_reason == "content_filter" and not terminal_sent:
                            logger.warning("llm.content_filter  deepseek")
                            terminal_sent = True
                            yield LLMChunk(type="end_turn", stop_reason="content_filter", usage=usage_data)

                    if tool_call_buffers:
                        for idx in sorted(tool_call_buffers.keys()):
                            buf = tool_call_buffers[idx]
                            logger.warning(
                                "%sdeepseek.tool_call_dropped%s  finish_reason=%s  name=%s  args_len=%d  preview=%.200s",
                                _R, _Z, finish_reason, buf["name"],
                                len(buf["arguments"]), buf["arguments"],
                            )

                    if not terminal_sent:
                        # 流结束仍未收到任何终止信号（例如 [DONE] 先到）：
                        # 依据已消耗的 output token 推断是否截断，避免引擎收不到任何终止信号
                        out_tokens = (usage_data or {}).get("output_tokens") or 0
                        inferred = "max_tokens" if out_tokens >= _DEEPSEEK_MAX_TOKENS else "end_turn"
                        logger.warning(
                            "deepseek.no_finish_reason  inferred=%s  completion_tokens=%s",
                            inferred, out_tokens,
                        )
                        yield LLMChunk(type="end_turn", stop_reason=inferred, usage=usage_data)

                return  # 成功则退出重试循环

            except (httpx.NetworkError, httpx.TimeoutException) as e:
                if attempt < max_retries - 1:
                    logger.warning("llm.retry  attempt=%d/%d  reason=%s", attempt + 1, max_retries, type(e).__name__)
                    yield LLMChunk(type="retry", delta=f"网络异常，正在重试 ({attempt + 1}/{max_retries})...",
                                   retry_code="llm_retrying", retry_attempt=attempt + 1, retry_max=max_retries)
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("llm.failed  reason=%s", type(e).__name__)
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503) and attempt < max_retries - 1:
                    logger.warning("llm.retry  attempt=%d/%d  status=%d", attempt + 1, max_retries, e.response.status_code)
                    yield LLMChunk(type="retry", delta=f"服务繁忙(HTTP {e.response.status_code})，正在重试 ({attempt + 1}/{max_retries})...",
                                   retry_code="llm_rate_limited", retry_attempt=attempt + 1, retry_max=max_retries)
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("llm.failed  status=%d  body=%.200s", e.response.status_code, str(e))
                raise
