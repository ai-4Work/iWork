"""C-1 · tool_invocations 账本：状态机 + 引擎 issued→completed + 超时留 issued + 回投护栏。

覆盖 M3 验收：
  - issued→completed 不回退、重复 mark completed 幂等；
  - 未知 id 丢弃、completed 回投 duplicate、issued 才 resolve；
  - 客户端工具首段超时进 C-2 对账窗：写类判不出账留 issued（ambiguous）、推 needs_confirm。
"""
import asyncio
import pytest
from uuid import uuid4

from server.models.message import MessageCreate
from server.llm.client import LLMChunk
from server.models.tool_invocation import ToolInvocation, InvocationState
from server.storage.memory import InMemoryToolInvocationRepo


def _inv(session_id, **kw):
    return ToolInvocation(session_id=session_id, tool_name="bash", **kw)


@pytest.mark.asyncio
async def test_mark_transitions_and_immutability():
    repo = InMemoryToolInvocationRepo()
    sid = uuid4()
    inv = _inv(sid)
    await repo.create(inv)

    assert await repo.mark(sid, inv.invocation_id, "completed", result={"success": True}) is True
    got = await repo.get(sid, inv.invocation_id)
    assert got.state == InvocationState.COMPLETED
    assert got.result == {"success": True}

    # completed 不可回退
    assert await repo.mark(sid, inv.invocation_id, "skipped", error="x") is False
    assert await repo.mark(sid, inv.invocation_id, "superseded") is False
    # 重复 mark completed 幂等返回 True
    assert await repo.mark(sid, inv.invocation_id, "completed") is True
    assert (await repo.get(sid, inv.invocation_id)).state == InvocationState.COMPLETED


@pytest.mark.asyncio
async def test_unknown_and_duplicate_create():
    repo = InMemoryToolInvocationRepo()
    sid = uuid4()
    assert await repo.get(sid, uuid4()) is None
    assert await repo.mark(sid, uuid4(), "completed") is False
    inv = _inv(sid)
    await repo.create(inv)
    await repo.create(inv)  # 同键重复 create 幂等
    assert (await repo.get(sid, inv.invocation_id)).invocation_id == inv.invocation_id


@pytest.mark.asyncio
async def test_skipped_and_superseded_from_issued_not_from_completed():
    repo = InMemoryToolInvocationRepo()
    sid = uuid4()

    a = _inv(sid)
    await repo.create(a)
    assert await repo.mark(sid, a.invocation_id, "superseded", error="replaced") is True
    assert (await repo.get(sid, a.invocation_id)).state == InvocationState.SUPERSEDED
    assert await repo.mark(sid, a.invocation_id, "issued") is False  # 不可回到 issued

    b = _inv(sid)
    await repo.create(b)
    assert await repo.mark(sid, b.invocation_id, "skipped", error="boom") is True
    assert (await repo.get(sid, b.invocation_id)).error == "boom"


@pytest.mark.asyncio
async def test_list_issued_by_message_filters_state_and_message():
    repo = InMemoryToolInvocationRepo()
    sid = uuid4()
    m1, m2 = uuid4(), uuid4()
    i1 = _inv(sid, message_id=m1)
    i2 = _inv(sid, message_id=m1)
    i3 = _inv(sid, message_id=m2)
    for inv in (i1, i2, i3):
        await repo.create(inv)
    await repo.mark(sid, i1.invocation_id, "completed", result={"ok": True})
    got = await repo.list_issued_by_message(sid, m1)
    assert [g.invocation_id for g in got] == [i2.invocation_id]


async def _wait_chunk(engine, ctype, loops=300):
    for _ in range(loops):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            if c["type"] == ctype:
                return c
    return None


async def _wait_idle(engine, loops=300):
    for _ in range(loops):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


@pytest.mark.asyncio
async def test_client_tool_ledger_issued_to_completed(engine_manager, active_session, fake_llm):
    active_session.mode = "build"
    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc1",
                  tool_input={"path": "/tmp/test/a.txt"})],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    msg = await engine.enqueue("test-user", MessageCreate(
        content="read a.txt", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None, "未收到 client.tool_request"
    rid = req["request_id"]
    # 执行前账为 issued
    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv is not None and inv.state == InvocationState.ISSUED

    out = await engine.submit_client_tool_result(rid, {"success": True, "content": "A"})
    assert out.get("duplicate") is None  # issued → 正常回投

    assert await _wait_idle(engine)
    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.result == {"success": True, "content": "A"}
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"

    # completed 后重复回投 → duplicate，不 resolve
    out2 = await engine.submit_client_tool_result(rid, {"success": True})
    assert out2.get("duplicate") is True


@pytest.mark.asyncio
async def test_client_tool_timeout_write_keeps_issued(engine_manager, active_session, fake_llm):
    """非幂等写工具首段超时 → C-2 对账窗；判不出时账留 issued + 推需确认（不自动重放）。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc1",
                  tool_input={"command": "echo hi"})],
        [LLMChunk(type="text", delta="handled."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 0.05  # 无客户端应答 → 对账窗超窗
    await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]
    # 写类 ambiguous：推 needs_confirm，不自动放行
    confirm = await _wait_chunk(engine, "tool.reconcile_needs_confirm")
    assert confirm is not None and confirm["request_id"] == rid
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv is not None
    # 判不出执行与否 → 账留 issued（ambiguous，拦截后续自动重跑）
    assert inv.state == InvocationState.ISSUED
    assert inv.idempotency == "non-idempotent"


@pytest.mark.asyncio
async def test_submit_guard_unknown_and_duplicate(engine_manager, active_session):
    """回投护栏：未知 id 丢弃、completed 返回 duplicate（不 resolve）。"""
    active_session.mode = "ask"
    engine = await engine_manager.get_or_create(active_session)
    sid = active_session.id

    # unknown → 丢弃
    out = await engine.submit_client_tool_result(str(uuid4()), {"success": True})
    assert out.get("duplicate") is True

    # 预置一条 completed 账 → duplicate
    inv = _inv(sid)
    await engine._invocation_repo.create(inv)
    await engine._invocation_repo.mark(
        sid, inv.invocation_id, "completed", result={"success": True},
    )
    out2 = await engine.submit_client_tool_result(str(inv.invocation_id), {"success": True})
    assert out2.get("duplicate") is True


@pytest.mark.asyncio
async def test_mark_invocation_warns_on_silent_false(
    engine_manager, active_session, monkeypatch,
):
    """mark 静默返回 False（行缺失 / 状态机守卫）不抛异常 → 必须显式 warning 记一笔。

    回归：_mark_invocation 曾丢弃 mark() 的布尔返回值，账停在 issued 却零日志，
    事后无法区分「mark 没被调用」与「调用被拒」。
    """
    engine = await engine_manager.get_or_create(active_session)
    calls: list[tuple[str, dict]] = []

    class _Spy:
        def bind(self, **_kw):
            return self

        def warning(self, event, **kw):
            calls.append((event, kw))

        def info(self, *_a, **_k):
            pass

        def exception(self, *_a, **_k):
            pass

    monkeypatch.setattr("server.engine.query_loop.logger", _Spy())

    # 行不存在 → mark 返回 False 且查不到行
    missing = str(uuid4())
    await engine._mark_invocation(missing, InvocationState.COMPLETED, result={"success": True})
    assert calls == [("invocation_mark_missed", {
        "invocation_id": missing, "want_state": "completed", "row_state": "missing",
    })]

    # 状态机守卫拒绝（completed 不可回退 → superseded）→ 行仍在，报当前状态
    calls.clear()
    sid = engine.session.id
    inv = _inv(sid)
    await engine._invocation_repo.create(inv)
    await engine._invocation_repo.mark(sid, inv.invocation_id, "completed", result={"success": True})
    await engine._mark_invocation(str(inv.invocation_id), InvocationState.SUPERSEDED)
    assert calls == [("invocation_mark_missed", {
        "invocation_id": str(inv.invocation_id),
        "want_state": "superseded",
        "row_state": "completed",
    })]
