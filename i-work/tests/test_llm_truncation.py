"""DeepSeek 客户端：max_tokens 截断信号的捕获。

覆盖三类 finish_reason：
  - length        → end_turn(stop_reason="max_tokens")，且半截 tool_call 不发射
  - stop          → end_turn(stop_reason="end_turn")（回归）
  - 缺失（只有 [DONE]）→ 依据 completion_tokens 兜底推断，仍产出 end_turn

httpx 流式响应以假 client 替换 _client 实现，不走真实网络。
"""
import json

import pytest

from server.llm.client import DeepSeekLLMClient


class _FakeResponse:
    def __init__(self, lines, status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.request = None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeHttpClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *args, **kwargs):
        return _FakeStreamCtx(_FakeResponse(self._lines))


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def _make_client(lines) -> DeepSeekLLMClient:
    client = DeepSeekLLMClient(api_key="k", model="deepseek-v4-pro")
    client._client = _FakeHttpClient(lines)
    return client


async def _collect(client) -> list:
    return [c async for c in client.stream(messages=[], system="")]


def _end_turns(chunks) -> list:
    return [c for c in chunks if c.type == "end_turn"]


@pytest.mark.asyncio
async def test_finish_reason_length_maps_to_max_tokens():
    chunks = await _collect(_make_client([
        _sse({"choices": [{"delta": {"content": "abc"}, "finish_reason": None}]}),
        _sse({
            "choices": [{"delta": {}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20000},
        }),
        "data: [DONE]",
    ]))

    assert "".join(c.delta for c in chunks if c.type == "text") == "abc"
    ends = _end_turns(chunks)
    assert len(ends) == 1
    assert ends[0].stop_reason == "max_tokens"
    assert ends[0].usage == {"input_tokens": 10, "output_tokens": 20000}


@pytest.mark.asyncio
async def test_finish_reason_length_drops_partial_tool_call():
    """截断时被切一半的 tool_call 不得执行，只产出 max_tokens 终止信号。"""
    chunks = await _collect(_make_client([
        _sse({"choices": [{
            "delta": {"tool_calls": [{
                "index": 0, "id": "call_1",
                "function": {"name": "bash", "arguments": "{\"cmd"},
            }]},
            "finish_reason": None,
        }]}),
        _sse({
            "choices": [{"delta": {}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 20000},
        }),
        "data: [DONE]",
    ]))

    assert not any(c.type == "tool_use" for c in chunks)
    ends = _end_turns(chunks)
    assert len(ends) == 1
    assert ends[0].stop_reason == "max_tokens"


@pytest.mark.asyncio
async def test_tool_calls_does_not_emit_extra_end_turn():
    """正常工具调用轮不得被兜底逻辑补出 end_turn，否则引擎执行完工具会提前终止。"""
    chunks = await _collect(_make_client([
        _sse({"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "bash", "arguments": "{\"cmd\": \"ls\"}"},
        }]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]))

    tool_uses = [c for c in chunks if c.type == "tool_use"]
    assert len(tool_uses) == 1
    assert tool_uses[0].tool_name == "bash"
    assert tool_uses[0].tool_input == {"cmd": "ls"}
    assert not _end_turns(chunks)


@pytest.mark.asyncio
async def test_finish_reason_stop_still_end_turn():
    chunks = await _collect(_make_client([
        _sse({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]))

    ends = _end_turns(chunks)
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_missing_finish_reason_fallback_infers_stop():
    """只有 [DONE] 无 finish_reason，且 token 未达上限 → 兜底为 end_turn。"""
    chunks = await _collect(_make_client([
        _sse({
            "choices": [{"delta": {"content": "x"}, "finish_reason": None}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 12},
        }),
        "data: [DONE]",
    ]))

    ends = _end_turns(chunks)
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_missing_finish_reason_fallback_infers_truncation():
    """只有 [DONE] 无 finish_reason，但 token 已达上限 → 兜底为 max_tokens。"""
    chunks = await _collect(_make_client([
        _sse({
            "choices": [{"delta": {"content": "x"}, "finish_reason": None}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 20000},
        }),
        "data: [DONE]",
    ]))

    ends = _end_turns(chunks)
    assert len(ends) == 1
    assert ends[0].stop_reason == "max_tokens"
