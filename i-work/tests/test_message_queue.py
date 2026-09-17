import pytest
from server.models.message import MessageCreate
from server.engine.query_loop import QueueFullError


@pytest.mark.asyncio
async def test_enqueue_order(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    m1 = await engine.enqueue("u1", MessageCreate(
        content="first", scene_mode="code", workspace="/tmp", model="x", mode="ask"))
    m2 = await engine.enqueue("u1", MessageCreate(
        content="second", scene_mode="code", workspace="/tmp", model="x", mode="ask"))
    assert m1.queue_position == 1
    assert m2.queue_position == 2


@pytest.mark.asyncio
async def test_queue_full_raises(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    for i in range(10):
        await engine.enqueue("u1", MessageCreate(
            content=f"msg{i}", scene_mode="code", workspace="/tmp", model="x", mode="ask"))
    with pytest.raises(QueueFullError):
        await engine.enqueue("u1", MessageCreate(
            content="one too many", scene_mode="code", workspace="/tmp", model="x", mode="ask"))


@pytest.mark.asyncio
async def test_remove_from_queue(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    msg = await engine.enqueue("u1", MessageCreate(
        content="removable", scene_mode="code", workspace="/tmp", model="x", mode="ask"))
    assert await engine.remove_from_queue(msg.id) is True
    # 已移除，再次失败
    assert await engine.remove_from_queue(msg.id) is False
