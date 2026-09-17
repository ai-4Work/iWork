import asyncio
import pytest

from server.models.session import Session
from server.models.message import MessageCreate
from server.storage.memory import InMemorySessionRepo, InMemoryMessageRepo
from server.llm.client import LLMChunk
from server.engine.query_loop import EngineManager


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


@pytest.mark.asyncio
async def test_request_cancel_stops_streaming():
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()
    llm = SlowStreamLLMClient(delay=0.02, chunks=100)
    mgr = EngineManager(session_repo, message_repo, llm)

    session = Session(
        user_id="test-user", mode="ask", workspace="/tmp/test",
        model="claude-sonnet-4-6",
    )
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)

    await engine.enqueue("test-user", MessageCreate(
        content="hello",
        scene_mode="office",
        workspace="/tmp/test",
        model="claude-sonnet-4-6",
        mode="ask",
    ))

    # 等待首个 agent.text 出现
    for _ in range(300):
        await asyncio.sleep(0.01)
        if any(c["type"] == "agent.text" for c in engine.stream_buffer.drain()):
            break

    engine.request_cancel()

    # 等待引擎回到 IDLE
    for _ in range(300):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    final = engine.stream_buffer.drain()
    types = [c["type"] for c in final]

    assert engine.state == "IDLE"
    assert "message.error" in types
    assert any(c.get("code") == "cancelled" for c in final)
    # 取消后不应跑完全部 100 块
    assert types.count("agent.text") < 100


@pytest.mark.asyncio
async def test_cancel_tree_cascades_to_children():
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()
    llm = SlowStreamLLMClient(delay=0.5, chunks=1)
    mgr = EngineManager(session_repo, message_repo, llm)

    parent = Session(user_id="test-user", mode="build", workspace="/tmp/test")
    await session_repo.create(parent)
    child = Session(
        user_id="test-user", mode="build", workspace="/tmp/test",
        parent_id=parent.id,
    )
    await session_repo.create(child)

    parent_engine = await mgr.get_or_create(parent)
    child_engine = await mgr.get_or_create(child)

    await mgr.cancel_tree(parent.id)

    assert parent_engine._cancel_event.is_set()
    assert child_engine._cancel_event.is_set()
