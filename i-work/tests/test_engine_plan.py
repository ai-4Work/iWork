import asyncio
import pytest
from server.models.message import MessageCreate
from server.llm.client import LLMChunk


@pytest.mark.asyncio
async def test_plan_mode_generate_and_confirm(engine_manager, active_session, fake_llm):
    active_session.mode = "plan"
    fake_llm.responses = [
        # turn 1: LLM 输出计划文本
        [
            LLMChunk(type="text", delta="1. Create project structure\n"),
            LLMChunk(type="text", delta="2. Install dependencies\n"),
            LLMChunk(type="text", delta="3. Write main code\n"),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        # turn 2: 确认后执行
        [
            LLMChunk(type="text", delta="Executing the plan..."),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
    ]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="Build a React project",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="plan",
    ))

    # 等待 plan.generated
    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "plan.generated" for c in chunks):
            break

    chunks = engine.stream_buffer.drain()
    assert "plan.generated" in [c["type"] for c in chunks]

    # 用户确认计划
    engine.sync_waiter.resolve_all(active_session.id, "confirmed")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    chunks = engine.stream_buffer.drain()
    chunk_types = [c["type"] for c in chunks]
    assert "message.complete" in chunk_types


@pytest.mark.asyncio
async def test_plan_mode_reject(engine_manager, active_session, fake_llm):
    active_session.mode = "plan"
    fake_llm.responses = [[
        LLMChunk(type="text", delta="Plan: do X, then Y."),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="Do something",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="plan",
    ))

    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(c["type"] == "plan.generated" for c in engine.stream_buffer.drain()):
            break

    engine.sync_waiter.resolve_all(active_session.id, "rejected")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    chunk_types = [c["type"] for c in engine.stream_buffer.drain()]
    assert "message.error" not in chunk_types
