import pytest
from uuid import uuid4
from server.storage.memory import InMemorySessionRepo, InMemoryMessageRepo
from server.models.session import Session
from server.models.message import Message, MessageStatus


@pytest.mark.asyncio
async def test_session_create_and_get():
    repo = InMemorySessionRepo()
    s = Session(user_id="u1")
    await repo.create(s)
    got = await repo.get(s.id)
    assert got is not None
    assert got.user_id == "u1"


@pytest.mark.asyncio
async def test_message_fifo_order():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    m1 = Message(session_id=sid, user_id="u1", content="first",
                 scene_mode="code", workspace="/tmp", model="x", mode="ask",
                 status=MessageStatus.PENDING, queue_position=1)
    m2 = Message(session_id=sid, user_id="u1", content="second",
                 scene_mode="code", workspace="/tmp", model="x", mode="ask",
                 status=MessageStatus.PENDING, queue_position=2)
    await repo.create(m1)
    await repo.create(m2)

    first = await repo.dequeue_next(sid)
    assert first.id == m1.id
    assert first.status == MessageStatus.PROCESSING

    second = await repo.dequeue_next(sid)
    assert second.id == m2.id

    third = await repo.dequeue_next(sid)
    assert third is None


@pytest.mark.asyncio
async def test_cancel_pending():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    m = Message(session_id=sid, user_id="u1", content="remove",
                scene_mode="code", workspace="/tmp", model="x", mode="ask",
                status=MessageStatus.PENDING, queue_position=1)
    await repo.create(m)

    ok = await repo.cancel_pending(m.id, sid)
    assert ok is True
    assert m.status == MessageStatus.CANCELLED
    assert m.queue_position is None

    # 已取消，再次取消返回 False
    ok2 = await repo.cancel_pending(m.id, sid)
    assert ok2 is False
