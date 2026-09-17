import pytest
from uuid import uuid4
from server.engine.context import ContextManager
from server.storage.memory import InMemoryMessageRepo


@pytest.mark.asyncio
async def test_ask_mode_no_tools():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    ctx = await ctx_mgr.build(uuid4(), 1, "ask", "office")
    assert ctx.available_tools is None


@pytest.mark.asyncio
async def test_plan_mode_turn1_has_question_tool():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    ctx = await ctx_mgr.build(uuid4(), 1, "plan", "code")
    assert ctx.available_tools is not None
    assert ctx.available_tools[0]["name"] == "plan.question"


@pytest.mark.asyncio
async def test_plan_mode_turn2_no_tools():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    ctx = await ctx_mgr.build(uuid4(), 2, "plan", "code")
    assert ctx.available_tools == []  # turn 2 不带 plan.question


@pytest.mark.asyncio
async def test_build_mode_has_empty_tools():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    ctx = await ctx_mgr.build(uuid4(), 1, "build", "code")
    assert ctx.available_tools == []


@pytest.mark.asyncio
async def test_append_and_build():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    sid = uuid4()
    await ctx_mgr.append_text(sid, "user", "Hello")
    ctx = await ctx_mgr.build(sid, 1, "ask", "office")
    assert len(ctx.messages) == 1
    assert ctx.messages[0]["content"] == "Hello"
