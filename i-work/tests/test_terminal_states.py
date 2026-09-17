"""M1 · 真回合取消 + 终态分流。

验证 run() finally 不再无条件置 COMPLETED：
  - 正常完成 → completed（含 message.complete 事件）
  - 引擎内部异常 → error 且 error_message 落库（不再补 message.complete）
  - 软取消（request_cancel）→ cancelled + message.error(code=cancelled) 已推 + 无 message.complete
  - 历史遗留 processing 消息不因引擎启动被扫描改写

注意：message.start / message.complete 经 EventBus 推流，测试环境（engine 无 event_bus）
不会进入 chunk 队列；因此终态断言以 message_repo 状态为准，事件断言走显式挂载的 EventBus。
"""
import asyncio

import pytest

from server.models.session import Session
from server.models.message import MessageCreate, MessageStatus
from server.storage.memory import InMemorySessionRepo, InMemoryMessageRepo
from server.config import settings
from server.llm.client import LLMChunk
from server.engine.query_loop import EngineManager, _TRUNCATION_CONTINUE_PROMPT
from server.observability.event_bus import EventBus
from server.observability.events import AgentEventType


class RaisingLLMClient:
    """stream 首块即抛异常，模拟引擎内部 API/认证错误。"""

    async def stream(self, messages, system, tools=None, tool_choice=None):
        raise RuntimeError("llm boom")
        yield  # pragma: no cover — 使函数成为 async generator


class SlowStreamLLMClient:
    """按固定间隔逐块吐字，便于在流式输出中触发取消。"""

    def __init__(self, delay: float = 0.02, chunks: int = 100):
        self.delay = delay
        self.chunks = chunks

    async def stream(self, messages, system, tools=None, tool_choice=None):
        for i in range(self.chunks):
            await asyncio.sleep(self.delay)
            yield LLMChunk(type="text", delta=f"chunk-{i}")
        yield LLMChunk(type="end_turn", stop_reason="end_turn")


def _make_engine(llm, session_mode="ask"):
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()
    mgr = EngineManager(session_repo, message_repo, llm)
    session = Session(
        user_id="test-user", mode=session_mode, workspace="/tmp/test",
        model="claude-sonnet-4-6",
    )
    return session_repo, message_repo, mgr, session


async def _wait_idle(engine, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


def _ask_msg(content="hello"):
    return MessageCreate(
        content=content, scene_mode="office", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="ask",
    )


async def _attach_bus(engine) -> list:
    """挂 EventBus，收集本测试关心的生命周期事件类型。"""
    bus = EventBus()
    engine._event_bus = bus
    seen: list[str] = []

    async def record(ev):
        seen.append(ev.type.value if hasattr(ev.type, "value") else str(ev.type))

    bus.subscribe(record)
    return seen


class _FakeLLM:
    def __init__(self, chunks):
        self.responses = [chunks]

    async def stream(self, messages, system, tools=None, tool_choice=None):
        for c in self.responses[0]:
            yield c


@pytest.mark.asyncio
async def test_normal_completion_is_completed():
    session_repo, message_repo, mgr, session = _make_engine(_FakeLLM([
        LLMChunk(type="text", delta="ok"),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]))
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    seen = await _attach_bus(engine)

    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    assert msg.status == MessageStatus.COMPLETED
    assert msg.error_message is None
    assert AgentEventType.MESSAGE_COMPLETE.value in seen
    assert AgentEventType.MESSAGE_ERROR.value not in seen


@pytest.mark.asyncio
async def test_internal_exception_is_error_not_completed():
    session_repo, message_repo, mgr, session = _make_engine(RaisingLLMClient())
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    seen = await _attach_bus(engine)

    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    assert msg.status == MessageStatus.ERROR
    assert msg.status is not MessageStatus.COMPLETED
    assert msg.error_message and "llm boom" in msg.error_message
    # 异常终态不再补发 message.complete
    assert AgentEventType.MESSAGE_COMPLETE.value not in seen
    assert AgentEventType.MESSAGE_ERROR.value in seen


@pytest.mark.asyncio
async def test_request_cancel_is_cancelled_no_complete():
    session_repo, message_repo, mgr, session = _make_engine(SlowStreamLLMClient(delay=0.02, chunks=100))
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    seen = await _attach_bus(engine)

    msg = await engine.enqueue("test-user", _ask_msg())

    # 等首个 agent.text 出现（不 drain，保留 buffer 用于终态断言）
    for _ in range(300):
        await asyncio.sleep(0.01)
        if engine.state == "PROCESSING":
            break

    # 触发取消
    engine.request_cancel()
    assert await _wait_idle(engine)

    assert msg.status == MessageStatus.CANCELLED
    assert AgentEventType.MESSAGE_COMPLETE.value not in seen

    final = engine.stream_buffer.drain()
    types = [c["type"] for c in final]
    assert "message.error" in types
    assert any(c.get("code") == "cancelled" for c in final)
    # 不应跑完全部 100 块
    assert types.count("agent.text") < 100


@pytest.mark.asyncio
async def test_leftover_processing_not_scanned_on_start():
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()
    mgr = EngineManager(session_repo, message_repo, _FakeLLM([
        LLMChunk(type="text", delta="ok"),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]))

    session = Session(user_id="test-user", mode="ask", workspace="/tmp/test")
    await session_repo.create(session)

    # 直接塞一条 processing（模拟上次崩溃遗留），不进 PENDING 队列
    from server.models.message import Message
    stuck = Message(
        session_id=session.id, user_id="test-user", content="legacy",
        scene_mode="office", workspace="/tmp/test", model="claude-sonnet-4-6",
        mode="ask", status=MessageStatus.PROCESSING,
    )
    await message_repo.create(stuck)

    engine = await mgr.get_or_create(session)
    # 引擎启动后跑一拍：不应扫描改写 processing 消息
    await asyncio.sleep(0.2)

    row = await message_repo.get(stuck.id)
    assert row is not None
    assert row.status == MessageStatus.PROCESSING
    assert engine.state == "IDLE"


class _ScriptedLLM:
    """按调用顺序回放每一轮的 chunks；调用次数超出后重复最后一轮。记录每次入参。"""

    def __init__(self, turns):
        self.turns = turns
        self.calls: list[dict] = []

    async def stream(self, messages, system, tools=None, tool_choice=None):
        self.calls.append({"messages": list(messages), "system": system})
        idx = min(len(self.calls) - 1, len(self.turns) - 1)
        for c in self.turns[idx]:
            yield c


@pytest.mark.asyncio
async def test_max_tokens_truncation_continues_then_completes():
    """截断一轮 → 注入续写提示重跑 → 下一轮正常结束，最终 completed。"""
    llm = _ScriptedLLM([
        [LLMChunk(type="text", delta="part1"),
         LLMChunk(type="end_turn", stop_reason="max_tokens")],
        [LLMChunk(type="text", delta="part2"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    session_repo, message_repo, mgr, session = _make_engine(llm)
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    seen = await _attach_bus(engine)

    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    assert msg.status == MessageStatus.COMPLETED
    assert msg.turn_count == 2
    assert len(llm.calls) == 2
    # 第二轮入参里应带上续写提示（assistant(part1) → user(继续) → LLM）
    assert any(
        _TRUNCATION_CONTINUE_PROMPT in str(m.get("content"))
        for m in llm.calls[1]["messages"]
    )
    # 续写通知经 EventBus 发出
    assert AgentEventType.SYSTEM_STATUS.value in seen
    # 未耗尽 → 不应有致命错误
    assert AgentEventType.MESSAGE_ERROR.value not in seen


@pytest.mark.asyncio
async def test_max_tokens_truncation_exhausted_is_error():
    """连续截断超过 N 次 → 终止为 error 并推 max_tokens_exceeded。"""
    llm = _ScriptedLLM([
        [LLMChunk(type="text", delta="part"),
         LLMChunk(type="end_turn", stop_reason="max_tokens")],
    ])
    session_repo, message_repo, mgr, session = _make_engine(llm)
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    seen = await _attach_bus(engine)

    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    assert msg.status == MessageStatus.ERROR
    assert msg.error_message and "长度上限" in msg.error_message
    # 默认 max_truncation_retries=3 → 第 4 次截断才耗尽
    assert len(llm.calls) == settings.max_truncation_retries + 1
    final = engine.stream_buffer.drain()
    assert any(c.get("code") == "max_tokens_exceeded" for c in final)
    assert AgentEventType.MESSAGE_COMPLETE.value not in seen
