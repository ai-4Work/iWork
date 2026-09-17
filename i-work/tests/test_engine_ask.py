import asyncio
import pytest
from server.models.message import MessageCreate
from server.llm.client import LLMChunk


@pytest.mark.asyncio
async def test_ask_mode_single_turn(engine_manager, active_session, fake_llm):
    fake_llm.responses = [[
        LLMChunk(type="text", delta="A closure is a function that remembers..."),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="What is a closure?",
        scene_mode="code",
        workspace="/tmp/test",
        model="claude-sonnet-4-6",
        mode="ask",
    ))

    # 等待引擎处理完成
    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    assert engine.state == "IDLE"
    chunks = engine.stream_buffer.drain()
    chunk_types = [c["type"] for c in chunks]

    assert "message.start" in chunk_types
    assert "agent.text" in chunk_types
    assert "message.complete" in chunk_types
