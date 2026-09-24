"""协议一致性：同一段对话在不同 OpenAI 兼容形态下必须产出**同一个**归一化序列。

引擎只认 `chunk.type`（thinking / text / tool_use / end_turn）与三个 `stop_reason`
（`content_filter` / `max_tokens` / `end_turn`）。各家"半兼容"实现的差异 —— 思考字段
叫 `reasoning_content` 还是 `reasoning`、tool_calls 带不带 `index`/`id`、`arguments`
是分片字符串还是已解析 dict、有没有 `finish_reason`、usage 在不在尾块 —— **全部在
客户端内消化**。

本模块把每种形态录成 `fixtures/*.json` 的原始 SSE 流，断言归一化产出。这是防止引擎
重新长出 `if provider ==` 分支的唯一有效手段：新增一种兼容形态时，加一份 fixture 即可，
不必碰 `query_loop.py`。反过来，如果哪天有人在引擎里按模型分叉，这里的序列断言会先散架。

fixture 形状：

* `turns`     —— 逐行的原始 SSE 文本（客户端收到的样子）。
* `expected.chunks` —— 期望的 `LLMChunk` 序列；`tool_use` 项省略 `tool_call_id` 表示
  "id 由客户端合成"，只断言形状（那份 fixture 没给 id，给不了确定值）。

`fixtures/` 下的每份 json 都会被收进来跑，没有"注册表"要维护。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server.llm.client import STOP_CONTENT_FILTER, STOP_END_TURN, STOP_MAX_TOKENS
from server.llm.client import OpenAICompatLLMClient

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_FILES = sorted(FIXTURE_DIR.glob("*.json"))

# 合成的 id 形状：`call_{index}_{8位十六进制}`（见 client.py `_drain_tool_calls`）。
_SYNTHETIC_ID = re.compile(r"^call_\d+_[0-9a-f]{8}$")

NORMALIZED_STOP_REASONS = {STOP_CONTENT_FILTER, STOP_MAX_TOKENS, STOP_END_TURN}


# ── 假 httpx：与 test_llm_truncation.py 同一套手法 ──────────────

class _FakeResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _client(lines: list[str]) -> OpenAICompatLLMClient:
    """一个不会发真实请求的客户端。模型名 / 端点取定值，与 fixture 内容无关。"""
    client = OpenAICompatLLMClient(
        api_key="k", model="m", base_url="http://127.0.0.1:1",
        supports_thinking=True, display_name="conformance",
    )
    client._client = _FakeHttpClient(lines)
    return client


async def _collect(lines: list[str]) -> list:
    client = _client(lines)
    return [c async for c in client.stream(messages=[{"role": "user", "content": "hi"}],
                                           system="s")]


def _normalize(chunk) -> dict:
    """把 `LLMChunk` 压成 fixture 能声明的形状 —— 只留断言关心的字段。"""
    if chunk.type in ("thinking", "text"):
        return {"type": chunk.type, "delta": chunk.delta}
    if chunk.type == "tool_use":
        out = {
            "type": "tool_use",
            "tool_name": chunk.tool_name,
            "tool_input": chunk.tool_input,
        }
        if chunk.tool_call_id:
            out["tool_call_id"] = chunk.tool_call_id
        return out
    if chunk.type == "end_turn":
        return {"type": "end_turn", "stop_reason": chunk.stop_reason, "usage": chunk.usage}
    return {"type": chunk.type}


def _assert_matches(actual: list[dict], expected: list[dict], where: str) -> None:
    """逐项比对。fixture 声明了哪些键就只比哪些键 —— 多出来的字段是有意为之。"""
    assert len(actual) == len(expected), (
        f"{where}: chunk 个数不符\n期望 {expected}\n实际 {actual}"
    )
    for i, (got, want) in enumerate(zip(actual, expected)):
        for key, value in want.items():
            assert got.get(key) == value, (
                f"{where}: 第 {i} 个 chunk 的 {key} 不符\n期望 {want}\n实际 {got}"
            )
        if want["type"] == "tool_use" and "tool_call_id" not in want:
            # 声明省略 = 该 fixture 没给 id，客户端应合成一个能配对回投的稳定 id。
            assert _SYNTHETIC_ID.match(got.get("tool_call_id") or ""), (
                f"{where}: 第 {i} 个 tool_use 未合成合法 id：{got.get('tool_call_id')!r}"
            )


# ── fixture 驱动 ────────────────────────────────────────────────

def test_fixture_dir_is_not_empty():
    """目录空了会让下面的参数化用例"零用例通过" —— 那是假的绿灯。"""
    assert FIXTURE_FILES, f"{FIXTURE_DIR} 下没有 fixture"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_recorded_stream_normalizes_to_expected(path: Path):
    """录制的原始流 → 归一化序列，逐项与 fixture 的 `expected.chunks` 对齐。"""
    fixture = _read(path)
    actual = [_normalize(c) for c in await _collect(fixture["turns"])]
    _assert_matches(actual, fixture["expected"]["chunks"], path.stem)


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixture_only_declares_normalized_stop_reasons(path: Path):
    """fixture 自身不得声明厂商词表 —— 归一化是客户端的事，写进 fixture 就把口径弄混了。"""
    for chunk in _read(path)["expected"]["chunks"]:
        if chunk["type"] == "end_turn":
            assert chunk["stop_reason"] in NORMALIZED_STOP_REASONS, (
                f"{path.stem}: stop_reason={chunk['stop_reason']!r} 不是归一值"
            )


@pytest.mark.asyncio
async def test_same_conversation_across_vendors_yields_identical_sequence():
    """两个厂商形态录的是**同一段对话** —— 归一化后必须一模一样。

    这是本模块的核心断言。它一旦失败，就说明有个厂商差异漏过了客户端、渗到了
    调用方；那种情况下引擎会开始需要知道"现在是谁在回话"。
    """
    deepseek = [_normalize(c) for c in await _collect(
        _read(FIXTURE_DIR / "openai_deepseek_tool_round.json")["turns"])]
    vllm = [_normalize(c) for c in await _collect(
        _read(FIXTURE_DIR / "openai_vllm_tool_round.json")["turns"])]

    # 只有工具调用 id 允许不同（一方给了真 id、一方由客户端合成）。
    def _strip_id(chunks):
        return [{k: v for k, v in c.items() if k != "tool_call_id"} for c in chunks]

    assert _strip_id(deepseek) == _strip_id(vllm)
    # 顺带固定下来：两条都是工具轮，不给 end_turn（引擎按 tool_use 续跑）。
    assert not any(c["type"] == "end_turn" for c in deepseek + vllm)


# ── finish_reason 词表收敛（L2）─────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("stop", STOP_END_TURN),
    ("length", STOP_MAX_TOKENS),
    ("max_tokens", STOP_MAX_TOKENS),      # 少数实现用这个写法代替 length
    ("content_filter", STOP_CONTENT_FILTER),
    ("abort", STOP_END_TURN),             # 服务端主动中止，按正常收尾
    ("error", STOP_END_TURN),
    ("a_reason_nobody_has_heard_of", STOP_END_TURN),   # 未知 → 告警 + 回落
])
@pytest.mark.asyncio
async def test_finish_reason_is_mapped_into_three_values(raw: str, expected: str):
    """各家词表全部映射到三个归一值，未知值回落而不是把整轮信号丢掉。"""
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "x"},
                                            "finish_reason": None}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": raw}]}),
        "data: [DONE]",
    ]
    ends = [c for c in await _collect(lines) if c.type == "end_turn"]
    assert len(ends) == 1, f"finish_reason={raw!r} 未产出唯一终止信号"
    assert ends[0].stop_reason == expected


@pytest.mark.asyncio
async def test_missing_finish_reason_without_usage_does_not_fabricate_max_tokens():
    """既没 finish_reason 也没 usage 时**不得**臆断成 max_tokens。

    臆断成截断会给每条消息都注入一次"接着写"，对从不报 usage 的后端等于白跑一轮 ——
    比漏判一次截断糟得多。宁可回 end_turn 并告警（`llm.unconfirmed_termination`）。
    """
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "x"},
                                            "finish_reason": None}]}),
        "data: [DONE]",
    ]
    ends = [c for c in await _collect(lines) if c.type == "end_turn"]
    assert len(ends) == 1
    assert ends[0].stop_reason == STOP_END_TURN
    assert ends[0].usage is None
