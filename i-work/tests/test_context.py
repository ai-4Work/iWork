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
async def test_stable_blocks_are_ordered_least_to_most_volatile():
    """system 末尾三段稳定注入的次序：记忆指南 < 画像 < 场景导航。

    提示词缓存按前缀命中，变化最频繁的必须排最后 —— 否则它一变，它后面（以及它自己）
    整段都失去复用。画像比导航变得少，所以排在导航前面。
    """
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    ctx = await ctx_mgr.build(
        uuid4(), 1, "ask", "office",
        memory_guide_xml="<memory-tools-guide>GUIDE</memory-tools-guide>",
        persona_xml="<user-persona>PERSONA</user-persona>",
        scene_nav_xml="<scene-navigation>NAV</scene-navigation>",
    )
    system = ctx.system_prompt
    assert system.index("GUIDE") < system.index("PERSONA") < system.index("NAV")


@pytest.mark.asyncio
async def test_append_and_build():
    ctx_mgr = ContextManager(InMemoryMessageRepo())
    sid = uuid4()
    await ctx_mgr.append_text(sid, "user", "Hello")
    ctx = await ctx_mgr.build(sid, 1, "ask", "office")
    assert len(ctx.messages) == 1
    assert ctx.messages[0]["content"] == "Hello"
