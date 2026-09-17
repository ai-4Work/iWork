import pytest
from server.llm.client import LLMChunk


@pytest.mark.asyncio
async def test_loop_detection_triggers(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)

    chunk = LLMChunk(type="tool_use", tool_name="bash", tool_input={"command": "ls"})
    assert not await engine._check_loop_detection(chunk)
    assert not await engine._check_loop_detection(chunk)
    assert await engine._check_loop_detection(chunk)  # 第3次触发


@pytest.mark.asyncio
async def test_loop_detection_resets_on_different_call(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)

    c1 = LLMChunk(type="tool_use", tool_name="bash", tool_input={"command": "ls"})
    c2 = LLMChunk(type="tool_use", tool_name="bash", tool_input={"command": "pwd"})

    assert not await engine._check_loop_detection(c1)
    assert not await engine._check_loop_detection(c1)
    assert not await engine._check_loop_detection(c2)  # 不同输入，重置
