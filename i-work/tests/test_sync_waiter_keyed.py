"""C-1 · SyncWaiter 键控化：隔离、广播、残留清理。

覆盖 M3 验收：
  - 工具结果按 tool_wait_key 精确回投，不唤醒无关等待；
  - 过期/重复回投落在自己的 key 上，不串扰后续调用；
  - resolve_all 按 session 前缀广播（取消用），不影响其它 session；
  - 无等待者时的残留结果在下次 wait 前被清理。
"""
import asyncio
import pytest
from uuid import uuid4

from server.sync_waiter import SyncWaiter, user_wait_key, tool_wait_key


@pytest.mark.asyncio
async def test_resolve_only_wakes_its_own_key():
    w = SyncWaiter()
    sid = uuid4()
    key_a = tool_wait_key(sid, uuid4())
    key_b = tool_wait_key(sid, uuid4())
    results = {}

    async def waiter(key, tag):
        val = await w.wait(key, timeout=1.0)
        results[tag] = val
        return val

    ta = asyncio.create_task(waiter(key_a, "a"))
    tb = asyncio.create_task(waiter(key_b, "b"))
    await asyncio.sleep(0.05)
    w.resolve(key_b, "B-result")
    assert await asyncio.wait_for(tb, 1.0) == "B-result"
    assert "a" not in results  # key_a 未被误唤醒
    w.resolve(key_a, "A-result")
    assert await asyncio.wait_for(ta, 1.0) == "A-result"


@pytest.mark.asyncio
async def test_late_stale_resolve_does_not_cross_talk_into_next_call():
    """第一次工具等待超时（账留 issued）后，迟到回投只落在旧 key，不唤醒下一次调用。"""
    w = SyncWaiter()
    sid = uuid4()
    key_old = tool_wait_key(sid, uuid4())
    key_new = tool_wait_key(sid, uuid4())

    assert await w.wait(key_old, timeout=0.05) is None  # 第一次等待超时

    async def second():
        return await w.wait(key_new, timeout=0.3)

    task = asyncio.create_task(second())
    await asyncio.sleep(0.05)
    w.resolve(key_old, "stale-late-result")   # 过期回投（应只落在旧 key）
    await asyncio.sleep(0.05)
    assert not task.done()                     # key_new 未被误唤醒
    w.resolve(key_new, "fresh")
    assert await asyncio.wait_for(task, 0.5) == "fresh"


@pytest.mark.asyncio
async def test_resolve_all_broadcasts_session_prefix_only():
    w = SyncWaiter()
    sid = uuid4()
    other = uuid4()
    keys = [tool_wait_key(sid, uuid4()), user_wait_key(sid, uuid4())]
    foreign = tool_wait_key(other, uuid4())
    results = {}

    async def waiter(key, tag):
        val = await w.wait(key, timeout=1.0)
        results[tag] = val
        return val

    tasks = [asyncio.create_task(waiter(k, i)) for i, k in enumerate(keys)]
    tf = asyncio.create_task(waiter(foreign, "f"))
    await asyncio.sleep(0.05)

    w.resolve_all(sid, "__cancel__")
    for t in tasks:
        assert await asyncio.wait_for(t, 1.0) == "__cancel__"
    assert "f" not in results  # 其它 session 不被广播

    w.resolve_all(other, "stop")
    assert await asyncio.wait_for(tf, 1.0) == "stop"


@pytest.mark.asyncio
async def test_stale_residue_is_popped_by_next_wait():
    w = SyncWaiter()
    key = user_wait_key(uuid4(), uuid4())
    w.resolve(key, "stale")  # 无等待者时写入残留
    assert await w.wait(key, timeout=0.05) is None  # 新等待清理残留，不误读


@pytest.mark.asyncio
async def test_wait_timeout_returns_none():
    w = SyncWaiter()
    assert await w.wait(tool_wait_key(uuid4(), uuid4()), timeout=0.01) is None
