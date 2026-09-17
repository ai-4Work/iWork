"""Stage 7 · 层2 工作区写锁（§9.12.2 / §9.12.9 #9）。

写-写互斥、读不排队、`task` 不死锁——这三条是这个特性存在的全部理由。
用真实的两个引擎（同一 EngineManager）跑并发写段，断言**进入/退出的交错顺序**，
而不是断言锁对象被创建了。
"""
import asyncio
from uuid import uuid4

import pytest

from server.engine.query_loop import ToolOutcome
from server.llm.client import LLMChunk
from server.models.session import Session


async def _engine(engine_manager, session_repo, workspace):
    s = Session(user_id="u1", mode="build", workspace=workspace, model="m")
    await session_repo.create(s)
    return await engine_manager.get_or_create(s)


def _install_instrumented_write(engine, log: list[str], name: str, delay=0.05):
    """把一次"写工具调用"换成一个可观测的临界区。"""
    async def fake_execute(msg, chunk, turn, step=None):
        log.append(f"{name}:enter")
        await asyncio.sleep(delay)
        log.append(f"{name}:exit")
        return ToolOutcome(
            chunk=chunk, kind="executed", tool_name=chunk.tool_name, tc_id="t1",
            tool_input={}, result={"success": True}, turn=turn, step=step,
        )

    async def noop_finalize(msg, outcome):
        return None

    engine._execute_tool = fake_execute
    engine._finalize_tool = noop_finalize


# ═══════════════════════════════════════════════════════════════
# 锁的归属：同工作区共享、空工作区各归各
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_same_workspace_shares_one_lock(engine_manager, session_repo):
    a = await _engine(engine_manager, session_repo, "G:/proj")
    b = await _engine(engine_manager, session_repo, "G:/proj")
    c = await _engine(engine_manager, session_repo, "G:/other")

    assert a._workspace_lock is b._workspace_lock, "同一工作区 = 同一把锁"
    assert a._workspace_lock is not c._workspace_lock, "不同工作区各有一把"


@pytest.mark.asyncio
async def test_empty_workspace_gets_a_per_session_lock(engine_manager, session_repo):
    """`""` 的语义是"没指定工作区"，不是一个叫空字符串的目录——归成一把锁会让
    所有空工作区会话无谓互相排队（子 session 默认就是 ""，§9.12.7 边界 4）。"""
    a = await _engine(engine_manager, session_repo, "")
    b = await _engine(engine_manager, session_repo, "")

    assert a._workspace_lock is not b._workspace_lock


# ═══════════════════════════════════════════════════════════════
# 写段并发行为
# ═══════════════════════════════════════════════════════════════

def _write_chunk(name="write_file"):
    return LLMChunk(type="tool_use", tool_name=name,
                    tool_call_id=f"c-{name}", tool_input={"path": "x.txt", "content": "y"})


@pytest.mark.asyncio
async def test_same_workspace_writes_do_not_interleave(engine_manager, session_repo):
    a = await _engine(engine_manager, session_repo, "G:/proj")
    b = await _engine(engine_manager, session_repo, "G:/proj")
    log: list[str] = []
    _install_instrumented_write(a, log, "a")
    _install_instrumented_write(b, log, "b")

    await asyncio.wait_for(
        asyncio.gather(
            a._run_segments(object(), [(_write_chunk(), None)], 0),
            b._run_segments(object(), [(_write_chunk(), None)], 0),
        ),
        timeout=10,
    )

    # 任一顺序都行，但必须一段完整包住另一段（不能 enter,enter,exit,exit）
    first = log[0][0]
    assert log == [f"{first}:enter", f"{first}:exit",
                   f"{'b' if first == 'a' else 'a'}:enter",
                   f"{'b' if first == 'a' else 'a'}:exit"], log


@pytest.mark.asyncio
async def test_different_workspaces_overlap(engine_manager, session_repo):
    """不同工作区没有共同资源，加锁只会白降并发。"""
    a = await _engine(engine_manager, session_repo, "G:/proj-a")
    b = await _engine(engine_manager, session_repo, "G:/proj-b")
    log: list[str] = []
    _install_instrumented_write(a, log, "a")
    _install_instrumented_write(b, log, "b")

    await asyncio.wait_for(
        asyncio.gather(
            a._run_segments(object(), [(_write_chunk(), None)], 0),
            b._run_segments(object(), [(_write_chunk(), None)], 0),
        ),
        timeout=10,
    )

    assert log == ["a:enter", "b:enter", "a:exit", "b:exit"] or \
           log == ["b:enter", "a:enter", "b:exit", "a:exit"], log


@pytest.mark.asyncio
async def test_task_dispatch_is_not_blocked_by_a_held_workspace_lock(
    engine_manager, session_repo,
):
    """父持锁等子 + 子来取同一把锁 = 死锁。所以 `task` 必须跳过取锁。

    这里直接把锁拿在手里再来跑 task 写段：若它试图取锁，就在自己手上死等。
    """
    engine = await _engine(engine_manager, session_repo, "")
    log: list[str] = []
    _install_instrumented_write(engine, log, "parent")

    async with engine._workspace_lock:
        await asyncio.wait_for(
            engine._run_segments(
                object(), [(LLMChunk(type="tool_use", tool_name="task",
                                     tool_call_id="t", tool_input={}), None)], 0,
            ),
            timeout=5,
        )
    assert log == ["parent:enter", "parent:exit"]


@pytest.mark.asyncio
async def test_a_real_write_is_blocked_by_held_lock(engine_manager, session_repo):
    """对照组：非 task 的写确实会等锁——否则上面的豁免测试可能是"锁根本没生效"。"""
    engine = await _engine(engine_manager, session_repo, "")
    log: list[str] = []
    _install_instrumented_write(engine, log, "w")

    async with engine._workspace_lock:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                engine._run_segments(object(), [(_write_chunk(), None)], 0),
                timeout=0.2,
            )
    assert log == []
