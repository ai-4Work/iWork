from __future__ import annotations
import logging
import uuid as _uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

logger = logging.getLogger("iwork.llm")

# client.py 用裸 stdlib logger，structlog 的 colorize_event 对这里不生效，
# 颜色需直接内嵌在消息串中（色号沿用 observability/logging.py）
_C = "\033[1;36m"   # 青，常规事件
_R = "\033[1;31m"   # 红，异常/丢弃
_Z = "\033[0m"

# 各默认值只在"没给模型配置"时兜底（测试直接构造客户端、或 legacy 调用路径）。
# 正常运行时由 ModelResolver 从 llm_model 行里逐字段传入。
DEFAULT_MAX_OUTPUT_TOKENS = 20000
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_THINKING_BUDGET_TOKENS = 4096

# ── 归一化终止原因 ──
# 引擎只认这三个值（query_loop.py:2179/2192/2229 的 content_filter / max_tokens / end_turn）。
# 各家词表在客户端内映射到它们，引擎里绝不出现按模型的分支 —— 这是本模块存在的意义。
STOP_END_TURN = "end_turn"
STOP_MAX_TOKENS = "max_tokens"
STOP_CONTENT_FILTER = "content_filter"

# OpenAI 兼容各家的 finish_reason 词表 → 归一值。未列出的走"未知"分支（告警 + end_turn）。
_FINISH_REASON_MAP = {
    "stop": STOP_END_TURN,
    "length": STOP_MAX_TOKENS,
    "max_tokens": STOP_MAX_TOKENS,     # 少数实现用这个写法代替 length
    "content_filter": STOP_CONTENT_FILTER,
    "abort": STOP_END_TURN,            # 服务端主动中止；引擎没有对应状态，按正常收尾处理
    "error": STOP_END_TURN,
}

# 表示"这一轮以工具调用收尾"的 finish_reason 取值。
_FINISH_REASON_TOOL_CALLS = ("tool_calls", "function_call")


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
    """LLM 客户端抽象。每个协议一个实现，负责把自家流式格式归一成 `LLMChunk`。

    `model` 形参允许单次调用覆盖实例默认模型。解析器按 model_key 缓存客户端实例、
    构造时就把模型定好，所以正常路径传 None 即可；留着它是为了让"一个客户端服务
    同一协议下多个模型"的场景（以及测试）不必重建实例。
    """

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[LLMChunk]: ...


class FakeLLMClient(LLMClient):
    """可预设响应的假 LLM 客户端，用于测试。按调用顺序依次回放预设的 chunk 列表。"""

    def __init__(self, responses: list[list[LLMChunk]] | None = None):
        self.responses = responses or []
        self._call_index = 0
        self.calls: list[dict] = []

    async def stream(self, messages, system, tools=None, tool_choice=None, model=None):
        self.calls.append({"messages": messages, "system": system, "tools": tools,
                           "model": model})
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

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        supports_thinking: bool = True,
        thinking_budget_tokens: int = DEFAULT_THINKING_BUDGET_TOKENS,
        temperature: float | None = None,
        display_name: str = "anthropic",
    ):
        import anthropic
        from server.config import settings
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key if api_key is not None else settings.anthropic_api_key
        )
        self._model = model if model is not None else settings.default_model
        self._max_output_tokens = max_output_tokens
        self._supports_thinking = supports_thinking
        self._thinking_budget_tokens = thinking_budget_tokens
        self._temperature = temperature
        self._label = display_name

    async def stream(self, messages, system, tools=None, tool_choice=None, model=None):
        kwargs = {
            "model": model or self._model,
            "max_tokens": self._max_output_tokens,
            "messages": messages,
            "system": system,
        }
        if self._supports_thinking:
            kwargs["thinking"] = {
                "type": "enabled", "budget_tokens": self._thinking_budget_tokens,
            }
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature
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


class OpenAICompatLLMClient(LLMClient):
    """OpenAI 兼容协议的 LLM 客户端（DeepSeek / vLLM / Ollama / SGLang / TGI …）。

    **所有厂商差异都收敛在本类里**，引擎只看到归一化后的 `LLMChunk` 与三个终止原因：

    * 思考字段：`reasoning_content` → `thinking`（少数实现叫 `reasoning`，两个都认）。
    * 工具调用：增量 `tool_calls`（按 `index` 拼装）或整块返回，`arguments` 可能已经是 dict。
      `id` 缺失时**合成一个稳定 id** —— 否则下一轮回投 `role:"tool"` 时 id 对不上。
    * 终止原因：`finish_reason`（Ollama 叫 `done_reason`）映射到三个归一值，未识别的告警 + end_turn。
    * usage：DeepSeek / OpenAI 在流式下默认**不报** token 数，需显式请求
      `stream_options: {"include_usage": true}`；该参数在流末尾以 `choices: []` 的独立
      chunk 回传，所以读取顺序必须是"先取 usage、再跳过空 choices"。

    内网自建模型多数是"半兼容"，上面每一条都是为此准备的。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 3,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        supports_tools: bool = True,
        supports_thinking: bool = False,
        temperature: float | None = None,
        extra_body: dict | None = None,
        request_usage: bool = True,
        display_name: str = "openai-compatible",
    ):
        import httpx
        from server.config import settings
        # 用 `is not None` 而不是 `or`：解析器可能显式传空串表示"这个模型没有密钥"
        # （内网自建很常见），用 `or` 会把它悄悄换成 .env 里 DeepSeek 的 key。
        self._api_key = api_key if api_key is not None else settings.deepseek_api_key
        self._model = model if model is not None else settings.default_model
        self._base_url = (
            base_url if base_url is not None else settings.deepseek_base_url
        ).rstrip("/")
        self._label = display_name
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._max_output_tokens = max_output_tokens
        self._supports_tools = supports_tools
        self._supports_thinking = supports_thinking
        self._temperature = temperature
        self._extra_body = dict(extra_body or {})
        self._request_usage = request_usage
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout_seconds)), trust_env=True,
        )

    async def aclose(self) -> None:
        """释放 httpx 连接池。模型配置被改写/删除、客户端实例被淘汰时调用。"""
        await self._client.aclose()

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

    def _drain_tool_calls(self, tool_call_buffers: dict) -> Iterator[LLMChunk]:
        """把缓冲里的工具调用冲成 `tool_use` 块，并清空缓冲。

        无名的不发（执行不了，只告警）。`id` 缺失时**合成一个稳定 id** —— 下一轮把
        结果以 `role:"tool"` 回投时要靠它配对，缺了会让请求 400 或把结果配到别的调用上。
        """
        for idx in sorted(tool_call_buffers.keys()):
            buf = tool_call_buffers[idx]
            if not buf["name"]:
                logger.warning(
                    "%stool_call_dropped %s%s  无名工具调用  args=%.200s",
                    _R, _Z, self._label, buf["arguments"],
                )
                continue
            if not buf["id"]:
                buf["id"] = f"call_{idx}_{_uuid.uuid4().hex[:8]}"
            yield LLMChunk(
                type="tool_use",
                tool_name=buf["name"],
                tool_call_id=buf["id"],
                tool_input=self._parse_tool_arguments(buf["arguments"]),
            )
        tool_call_buffers.clear()

    @staticmethod
    def _parse_tool_arguments(raw) -> dict:
        """工具参数解析。少数实现直接回已解析的 dict，无需再 json.loads。"""
        import json
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "%sllm.tool_parse_error%s  raw=%.200s  error=%s", _R, _Z, raw, e,
            )
            return {"_parse_error": True, "_raw_arguments": raw, "_error": str(e)}
        return parsed if isinstance(parsed, dict) else {"_parse_error": True,
                                                        "_raw_arguments": raw,
                                                        "_error": "参数不是 JSON 对象"}

    def _build_body(self, messages, system, tools, tool_choice, model) -> dict:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        body: dict = {
            "model": model or self._model,
            "messages": api_messages,
            "stream": True,
            "max_tokens": self._max_output_tokens,
        }
        if self._request_usage:
            # DeepSeek/OpenAI 流式默认不报 usage，不请求就拿不到 token 数。
            # extra_body 在最后合并，所以管理员可以用它把这个字段覆盖掉。
            body["stream_options"] = {"include_usage": True}
        if self._supports_thinking:
            # DeepSeek 的非标准约定：`{"type": "enabled"}`，不带预算。
            body["thinking"] = {"type": "enabled"}
        if self._temperature is not None:
            body["temperature"] = self._temperature
        if tools and self._supports_tools:
            body["tools"] = self._convert_tools(tools)
            if tool_choice:
                body["tool_choice"] = "none" if tool_choice == "none" else "auto"
        body.update(self._extra_body)
        return body

    async def stream(self, messages, system, tools=None, tool_choice=None, model=None):
        """流式调用。**只 yield 归一化的 `LLMChunk`** —— 调用方不解析任何厂商格式。"""
        import httpx
        import json
        import asyncio

        body = self._build_body(messages, system, tools, tool_choice, model)

        for attempt in range(self._max_retries):
            try:
                tool_call_buffers: dict[int, dict] = {}
                usage_data: dict | None = None
                terminal_sent = False
                # 收到终止原因后暂存，等流读完（拿到尾部 usage）再发 end_turn。
                # None = 不补 end_turn（工具轮）或还没收到终止原因。
                pending_end_turn: str | None = None
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
                            f"{self._label} {response.status_code}: {text.decode()[:500]}",
                            request=response.request,
                            response=response,
                        )

                    finish_reason = None
                    async for line in response.aiter_lines():
                        # 只认 `data: ` 开头的行：`: keep-alive` 之类的注释行、空行直接跳过。
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        # ⚠️ 顺序要紧：请求了 include_usage 后，流末尾会来一个
                        # `choices: []` 的 usage-only chunk。必须先取 usage 再跳过空 choices。
                        if data.get("usage"):
                            u = data["usage"]
                            usage_data = {
                                "input_tokens": u.get("prompt_tokens", 0),
                                "output_tokens": u.get("completion_tokens", 0),
                            }

                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {}) or {}
                        # Ollama 的 OpenAI 兼容层用 done_reason 而不是 finish_reason。
                        chunk_finish = (
                            choices[0].get("finish_reason")
                            or choices[0].get("done_reason")
                            or data.get("done_reason")
                        )
                        if chunk_finish:
                            finish_reason = chunk_finish

                        if delta.get("reasoning_content"):
                            yield LLMChunk(type="thinking", delta=delta["reasoning_content"])
                        elif delta.get("reasoning"):
                            # 少数实现（含 OpenAI 官方）把思考放在 reasoning 字段。
                            yield LLMChunk(type="thinking", delta=delta["reasoning"])

                        if delta.get("content"):
                            yield LLMChunk(type="text", delta=delta["content"])

                        for tc in (delta.get("tool_calls") or []):
                            idx = tc.get("index", 0)
                            buf = tool_call_buffers.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""},
                            )
                            if tc.get("id"):
                                buf["id"] = tc["id"]
                            func = tc.get("function", {}) or {}
                            # 有的实现只在首个增量里给完整 function（name+arguments），
                            # 后续增量为空；这里用"非空才写/才拼"兼容两种形态。
                            if func.get("name"):
                                buf["name"] = func["name"]
                            args = func.get("arguments")
                            if args:
                                if isinstance(args, str):
                                    # 常规形态：增量字符串，累加。
                                    buf["arguments"] += args
                                else:
                                    # 少数实现（内网自建常见）直接把 arguments 解析好回传。
                                    # 它是**整块**值、不可能分片到达，所以是赋值不是累加 ——
                                    # 累加会 `str += dict` 直接抛 TypeError，整轮对话崩在解析上。
                                    buf["arguments"] = json.dumps(args, ensure_ascii=False)

                        if not finish_reason:
                            continue

                        # ── 收到终止原因：先把工具调用冲出去，再决定要不要补 end_turn ──
                        is_tool_finish = finish_reason in _FINISH_REASON_TOOL_CALLS
                        normalized = _FINISH_REASON_MAP.get(finish_reason)
                        if normalized is None and not is_tool_finish:
                            logger.warning(
                                "%sllm.unknown_finish_reason%s  model=%s  value=%s"
                                "  → 按 %s 处理",
                                _R, _Z, self._label, finish_reason, STOP_END_TURN,
                            )
                        if normalized is None:
                            # `tool_calls` / `function_call` 走下面"冲工具"的分支，
                            # 这里只是为了给别的路径留一个确定的 stop_reason。
                            normalized = STOP_END_TURN

                        if tool_call_buffers:
                            if normalized == STOP_MAX_TOKENS:
                                # 被 max_tokens 砍断时，缓冲里的 arguments 是半截 JSON，
                                # 拿去执行等于用非法入参调工具。丢掉，只发终止信号，
                                # 让引擎的"截断续写"分支接着写。
                                logger.warning(
                                    "%stool_call_dropped %s%s  被截断，丢弃 %d 个半截工具调用",
                                    _R, self._label, _Z, len(tool_call_buffers),
                                )
                                tool_call_buffers.clear()
                            else:
                                terminal_sent = True
                                for chunk in self._drain_tool_calls(tool_call_buffers):
                                    yield chunk
                                if is_tool_finish:
                                    # 工具调用轮不补 end_turn —— 引擎按 tool_use 续跑下一轮。
                                    continue
                        elif is_tool_finish:
                            # 声明是工具调用但缓冲是空的：只告警，让引擎按 end_turn 收尾。
                            logger.warning(
                                "%sfinish_reason=%s 但无工具调用增量的 %s%s",
                                _R, finish_reason, self._label, _Z,
                            )

                        # ⚠️ 终止信号**推迟到流结束才发**，不在这里 yield。
                        # 请求了 `include_usage` 后，usage 在 `[DONE]` 之前的**最后一个**
                        # 独立 chunk 里（`choices: []`），也就是在 finish_reason 之后才到。
                        # 就地 yield 会让这个 end_turn 永远带着 usage=None ——
                        # 于是 token 校准、计费、OTel 全都拿不到真实数字。
                        terminal_sent = True
                        pending_end_turn = normalized

                    if pending_end_turn is not None:
                        if pending_end_turn == STOP_MAX_TOKENS:
                            logger.warning(
                                "%sllm.truncated%s  model=%s  completion_tokens=%s",
                                _R, _Z, self._label,
                                (usage_data or {}).get("output_tokens"),
                            )
                        yield LLMChunk(type="end_turn", stop_reason=pending_end_turn,
                                       usage=usage_data)
                    elif not terminal_sent:
                        if tool_call_buffers:
                            # 有的后端（部分 vLLM / Ollama 兼容层）发完工具调用只给
                            # `[DONE]`，从不给 `finish_reason`。此时缓冲里是**完整**的
                            # 工具调用，丢掉等于静默吞掉一次工具执行 —— 比多补一个终止
                            # 信号糟得多。先冲出去，再按工具轮的口径收尾（不补 end_turn）。
                            logger.warning(
                                "%sllm.no_finish_reason_with_tool_calls%s  model=%s"
                                "  冲工具调用 %d 个（该后端未发 finish_reason）",
                                _R, _Z, self._label, len(tool_call_buffers),
                            )
                            for chunk in self._drain_tool_calls(tool_call_buffers):
                                yield chunk
                            return
                        # 流结束却没收到任何终止信号（例如 [DONE] 先到）。此时只能靠
                        # output token 数反推是否被截断。
                        out_tokens = (usage_data or {}).get("output_tokens")
                        if out_tokens is None:
                            # 既没 finish_reason 也没 usage：**无法判断**是否被截断。
                            # 刻意不臆断成 max_tokens —— 对从不报 usage 的后端，那会让
                            # 每条消息都被注入一次"接着写"、白跑一轮。宁可大声告警，
                            # 让运维发现后端不合规（多半是没接受 stream_options）。
                            logger.warning(
                                "%sllm.unconfirmed_termination%s  model=%s"
                                "  未收到 finish_reason 与 usage，按 end_turn 处理；"
                                "若该后端稳定截断，请检查它是否支持 stream_options",
                                _R, _Z, self._label,
                            )
                            inferred = STOP_END_TURN
                        else:
                            inferred = (
                                STOP_MAX_TOKENS if out_tokens >= self._max_output_tokens
                                else STOP_END_TURN
                            )
                            logger.warning(
                                "%sllm.no_finish_reason%s  model=%s  inferred=%s"
                                "  completion_tokens=%s",
                                _R, _Z, self._label, inferred, out_tokens,
                            )
                        yield LLMChunk(type="end_turn", stop_reason=inferred,
                                       usage=usage_data)

                return  # 成功则退出重试循环

            except (httpx.NetworkError, httpx.TimeoutException) as e:
                if attempt < self._max_retries - 1:
                    logger.warning("llm.retry  attempt=%d/%d  reason=%s",
                                   attempt + 1, self._max_retries, type(e).__name__)
                    yield LLMChunk(type="retry",
                                   delta=f"网络异常，正在重试 ({attempt + 1}/{self._max_retries})...",
                                   retry_code="llm_retrying", retry_attempt=attempt + 1,
                                   retry_max=self._max_retries)
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("llm.failed  reason=%s", type(e).__name__)
                raise
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 502, 503) and attempt < self._max_retries - 1:
                    logger.warning("llm.retry  attempt=%d/%d  status=%d",
                                   attempt + 1, self._max_retries,
                                   e.response.status_code)
                    yield LLMChunk(type="retry",
                                   delta=f"服务繁忙(HTTP {e.response.status_code})，"
                                         f"正在重试 ({attempt + 1}/{self._max_retries})...",
                                   retry_code="llm_rate_limited",
                                   retry_attempt=attempt + 1,
                                   retry_max=self._max_retries)
                    await asyncio.sleep(2 ** attempt)
                    continue
                logger.error("llm.failed  status=%d  body=%.200s",
                             e.response.status_code, str(e))
                raise
