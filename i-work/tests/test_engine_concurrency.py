"""Stage 1 · 独立并发修复的回归测试（§9.12.9 #1 / #4 / #5 / #8）。

覆盖四件事：
- is_evictable() 只在「无当前消息 + 队列排空 + IDLE」时为真；
- _evict_idle() 只驱逐空闲、按插入序取最老、不动 protect、不动在跑的，且被驱逐
  的引擎其 run() 协程真的被 cancel；
- shutdown_tree() 递归 cancel 子孙的 run() 协程；cancel_tree() 只置取消位、不杀协程；
- _run_read_segment() 的并发度受 _read_semaphore 限制。
"""
import asyncio
import time
from uuid import uuid4

import pytest

from server.config import settings
from server.engine.query_loop import EngineManager, QueryLoopEngine, _PendingTool
from server.llm.client import LLMChunk
from server.models.message import Message
from server.models.session import Session
from server.storage.memory import InMemoryMessageRepo, InMemorySessionRepo


def _bare_engine(session_repo, message_repo, llm, session):
    """直接构造引擎：不经 get_or_create，因此不会起后台 run() 协程。"""
    return QueryLoopEngine(
        session=session,
        session_repo=session_repo,
        message_repo=message_repo,
        llm_client=llm,
    )


async def _make_session(session_repo, *, parent_id=None, user_id="u1"):
    s = Session(user_id=user_id, mode="ask", workspace="/tmp/test",
                model="claude-sonnet-4-6", parent_id=parent_id)
    await session_repo.create(s)
    return s


# ═══════════════════════════════════════════════════════════════
# is_evictable
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_is_evictable_only_when_fully_idle(
    session_repo, message_repo, fake_llm, active_session,
):
    engine = _bare_engine(session_repo, message_repo, fake_llm, active_session)

    assert engine.is_evictable() is True

    # 有在跑的消息 → 不可驱逐
    engine.state = "PROCESSING"
    assert engine.is_evictable() is False
    engine.state = "IDLE"

    # 有当前消息 → 不可驱逐
    engine._current_msg = Message(
        session_id=active_session.id, user_id="u1", content="x",
        scene_mode="office", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="ask",
    )
    assert engine.is_evictable() is False
    engine._current_msg = None

    # 输出队列没排空 → 不可驱逐（客户端还没读完，驱逐会丢 chunk）
    engine._chunk_queue.put_nowait({"type": "agent.text", "delta": "x"})
    assert engine.is_evictable() is False
    engine._chunk_queue.get_nowait()
    assert engine.is_evictable() is True


# ═══════════════════════════════════════════════════════════════
# _evict_idle
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_evict_idle_evicts_oldest_and_cancels_run_task(
    engine_manager, session_repo, monkeypatch,
):
    monkeypatch.setattr(settings, "max_engines", 2)

    sa = await _make_session(session_repo, user_id="a")
    sb = await _make_session(session_repo, user_id="b")
    sc = await _make_session(session_repo, user_id="c")

    eng_a = await engine_manager.get_or_create(sa)
    eng_b = await engine_manager.get_or_create(sb)
    assert len(engine_manager._engines) == 2

    # 第 3 个触发驱逐：最老的 A 出局，B / C 留下
    eng_c = await engine_manager.get_or_create(sc)
    await asyncio.sleep(0)

    assert sa.id not in engine_manager._engines
    assert sb.id in engine_manager._engines
    assert sc.id in engine_manager._engines

    # 被驱逐者的 run() 协程必须真的被 cancel，否则「引擎已摘、协程还在跑」
    assert eng_a._run_task.cancelled() is True

    # 收尾：剩下两个引擎的协程也一并停掉，避免测试尾部挂着的任务告警
    for eng in (eng_b, eng_c):
        eng._run_task.cancel()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_evict_idle_skips_protected_and_running(
    engine_manager, session_repo, monkeypatch,
):
    monkeypatch.setattr(settings, "max_engines", 1)

    sa = await _make_session(session_repo, user_id="a")
    eng_a = await engine_manager.get_or_create(sa)
    assert len(engine_manager._engines) == 1

    # 容量已满，但保护对象是 A 自己 → 一个都不驱逐
    engine_manager._evict_idle(protect=sa.id)
    assert sa.id in engine_manager._engines
    assert eng_a._run_task.done() is False

    # 换成保护别人，A 在跑（PROCESSING）→ 仍然不驱逐，宁可短暂超限
    eng_a.state = "PROCESSING"
    engine_manager._evict_idle(protect=uuid4())
    assert sa.id in engine_manager._engines
    assert eng_a._run_task.done() is False

    # A 回到空闲 → 这次真的驱逐
    eng_a.state = "IDLE"
    engine_manager._evict_idle(protect=uuid4())
    await asyncio.sleep(0)
    assert sa.id not in engine_manager._engines
    assert eng_a._run_task.cancelled() is True


# ═══════════════════════════════════════════════════════════════
# shutdown_tree vs cancel_tree
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_shutdown_tree_cancels_task_and_recurses_to_children(
    engine_manager, session_repo,
):
    parent = await _make_session(session_repo, user_id="p")
    child = await _make_session(session_repo, parent_id=parent.id, user_id="c")
    grandchild = await _make_session(session_repo, parent_id=child.id, user_id="g")

    eng_p = await engine_manager.get_or_create(parent)
    eng_c = await engine_manager.get_or_create(child)
    eng_g = await engine_manager.get_or_create(grandchild)

    await engine_manager.shutdown_tree(parent.id)
    await asyncio.sleep(0)

    # 递归到孙辈：三个引擎的协程全被终止，且都置了取消位
    for eng in (eng_p, eng_c, eng_g):
        assert eng._run_task.cancelled() is True
        assert eng._cancel_event.is_set() is True


@pytest.mark.asyncio
async def test_cancel_tree_leaves_engine_alive(engine_manager, session_repo):
    parent = await _make_session(session_repo, user_id="p")
    child = await _make_session(session_repo, parent_id=parent.id, user_id="c")

    eng_p = await engine_manager.get_or_create(parent)
    eng_c = await engine_manager.get_or_create(child)

    await engine_manager.cancel_tree(parent.id)
    await asyncio.sleep(0)

    # 用户点「停止」后引擎必须存活继续接新消息 → 只置位，不杀协程
    for eng in (eng_p, eng_c):
        assert eng._cancel_event.is_set() is True
        assert eng._run_task.done() is False

    for eng in (eng_p, eng_c):
        eng._run_task.cancel()
    await asyncio.sleep(0)


# ═══════════════════════════════════════════════════════════════
# 读段 fan-out 闸门
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_read_segment_concurrency_capped(
    session_repo, message_repo, fake_llm, active_session,
):
    engine = _bare_engine(session_repo, message_repo, fake_llm, active_session)
    engine._read_semaphore = asyncio.Semaphore(3)

    in_flight = 0
    peak = 0

    async def fake_dispatch(msg, chunk, turn, step=None):
        return _PendingTool(
            chunk=chunk,
            tool_name=chunk.tool_name or "read_file",
            tc_id=chunk.tool_call_id or "tc",
            tool_input={},
            turn=turn,
            step=step,
            location="client",
            start=time.monotonic(),
        )

    async def fake_await(msg, pending):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return pending.tc_id

    engine._dispatch_tool = fake_dispatch
    engine._await_tool = fake_await

    msg = Message(
        session_id=active_session.id, user_id="u1", content="go",
        scene_mode="office", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="ask",
    )
    pairs = [
        (LLMChunk(type="tool_use", tool_name="read_file",
                  tool_call_id=f"tc{i}", tool_input={}), None)
        for i in range(20)
    ]

    results = await engine._run_read_segment(msg, pairs, turn=1)

    # 20 个调用全部收口，且同时在飞的不超过闸门值 3
    assert len(results) == 20
    assert peak == 3
    assert in_flight == 0
