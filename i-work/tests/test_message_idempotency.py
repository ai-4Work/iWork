import pytest
from server.models.message import MessageCreate


def _body(content: str, key: str | None):
    return MessageCreate(
        content=content, scene_mode="code", workspace="/tmp", model="x", mode="ask",
        client_message_id=key)


@pytest.mark.asyncio
async def test_duplicate_same_key_returns_existing(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    m1 = await engine.enqueue("u1", _body("hello", "cm-1"))
    m2 = await engine.enqueue("u1", _body("hello", "cm-1"))  # 同键重发 → 幂等返回已存在消息
    assert m2.id == m1.id


@pytest.mark.asyncio
async def test_duplicate_ignores_content(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    m1 = await engine.enqueue("u1", _body("A", "cm-x"))
    m2 = await engine.enqueue("u1", _body("B", "cm-x"))  # 同键不同内容 → 仍视为同一条，不新建、不覆盖
    assert m2.id == m1.id
    assert m2.content == "A"


@pytest.mark.asyncio
async def test_different_keys_insert_each(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    m1 = await engine.enqueue("u1", _body("same text", "cm-a"))
    m2 = await engine.enqueue("u1", _body("same text", "cm-b"))  # 新意图同内容 → 新键 → 正常入队
    assert m1.id != m2.id


@pytest.mark.asyncio
async def test_without_key_inserts_each(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    m1 = await engine.enqueue("u1", _body("one", None))
    m2 = await engine.enqueue("u1", _body("two", None))  # 无 key → 兼容老客户端，各入一条
    assert m1.id != m2.id
    assert m1.content == "one"
    assert m2.content == "two"


@pytest.mark.asyncio
async def test_find_by_client_message_id(engine_manager, active_session):
    engine = await engine_manager.get_or_create(active_session)
    assert await engine.find_by_client_message_id("cm-nope") is None
    msg = await engine.enqueue("u1", _body("hi", "cm-found"))
    found = await engine.find_by_client_message_id("cm-found")
    assert found is not None and found.id == msg.id
    # 空 key 不查询
    assert await engine.find_by_client_message_id("") is None
