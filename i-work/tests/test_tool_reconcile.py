"""C-2 · 客户端工具对账窗（client.tool_reconcile / tool.reconcile_needs_confirm）。

覆盖 M6 验收（服务端侧）：
  - 首段等待超时不再立即判失败，推 client.tool_reconcile 进入宽限窗；
  - 窗内 executed 应答 → mark completed 用真实结果收口（不重复执行/不误判失败）；
  - 窗内迟到普通回投（outbox 重放）→ 同样收口；
  - read-only/idempotent 判不出 → 原调用 superseded，可安全重放；
  - non-idempotent 判不出 → 账留 issued(ambiguous) + 推 needs_confirm（不自动重放）。
"""
import asyncio
import pytest

from server.models.message import MessageCreate
from server.llm.client import LLMChunk
from server.models.tool_invocation import InvocationState


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


async def _run_single_tool(engine, tool_name, tool_input, fake_llm, tail_text):
    """让引擎先输出一个工具调用、再在注入结果后输出收尾文本。返回 enqueue 的 msg。"""
    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name=tool_name, tool_call_id="tc1",
                  tool_input=tool_input)],
        [LLMChunk(type="text", delta=tail_text),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    return await engine.enqueue("test-user", MessageCreate(
        content="do it", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))


@pytest.mark.asyncio
async def test_reconcile_executed_reply_completes(engine_manager, active_session, fake_llm):
    """对账窗内客户端确认已执行并补投结果 → completed 收口，用真实结果喂 LLM。"""
    active_session.mode = "build"
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 5.0  # 窗口内留出客户端应答时间
    msg = await _run_single_tool(engine, "write_file",
                                 {"path": "/tmp/test/a.txt", "content": "hi"}, fake_llm, "done.")

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]
    rec = await _wait_chunk(engine, "client.tool_reconcile")
    assert rec is not None
    assert rec["request_id"] == rid
    assert rec["idempotency"] == "idempotent"  # 整内容覆盖写

    out = await engine.submit_client_tool_result(rid, {
        "reconcile": True, "state": "executed",
        "result": {"success": True, "output": "reconciled-write"},
    })
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.result == {"success": True, "output": "reconciled-write"}
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"


@pytest.mark.asyncio
async def test_reconcile_executed_failure_reply_keeps_success_false(
    engine_manager, active_session, fake_llm,
):
    """窗内客户端答"已执行"但结果是失败（客户端形状 status='error'）→ 账里 success=False。

    信封 {reconcile, state, result} 里的 result 是客户端 ToolResult，须递归归一，
    否则失败会被读成成功（副作用清单显绿）。
    """
    active_session.mode = "build"
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 5.0
    msg = await _run_single_tool(engine, "write_file",
                                 {"path": "/tmp/test/a.txt", "content": "hi"}, fake_llm, "done.")

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]
    assert await _wait_chunk(engine, "client.tool_reconcile") is not None

    out = await engine.submit_client_tool_result(rid, {
        "reconcile": True, "state": "executed",
        "result": {"status": "error", "error": "EACCES: permission denied", "exit_code": 1},
    })
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.result["success"] is False
    assert inv.result["status"] == "error"
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"


@pytest.mark.asyncio
async def test_reconcile_late_normal_result_accepted(engine_manager, active_session, fake_llm):
    """窗内迟到普通回投（outbox 重放，无 reconcile 标记）→ 同样收口 completed。"""
    active_session.mode = "build"
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 5.0
    msg = await _run_single_tool(engine, "read_file",
                                 {"path": "/tmp/test/a.txt"}, fake_llm, "done.")

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]
    assert await _wait_chunk(engine, "client.tool_reconcile") is not None

    out = await engine.submit_client_tool_result(
        rid, {"success": True, "output": "late-literal"},
    )
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.result == {"success": True, "output": "late-literal"}


@pytest.mark.asyncio
async def test_reconcile_uncertain_read_supersedes(engine_manager, active_session, fake_llm):
    """只读工具判不出 → superseded，不推 needs_confirm（可安全重试）。"""
    active_session.mode = "build"
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 0.05  # 超窗无应答
    msg = await _run_single_tool(engine, "read_file",
                                 {"path": "/tmp/test/a.txt"}, fake_llm, "done.")

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]
    # 只读不推需确认
    confirm = await _wait_chunk(engine, "tool.reconcile_needs_confirm", loops=20)
    assert confirm is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv is not None
    assert inv.idempotency == "read-only"
    assert inv.state == InvocationState.SUPERSEDED
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"


@pytest.mark.asyncio
async def test_reconcile_uncertain_write_pushes_confirm_and_keeps_issued(
    engine_manager, active_session, fake_llm,
):
    """写类判不出 → 不自动重放：账留 issued + 推 needs_confirm，拦自动重跑。"""
    active_session.mode = "build"
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 0.05
    msg = await _run_single_tool(engine, "bash",
                                 {"command": "echo hello > /tmp/out.txt"}, fake_llm, "done.")

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    rid = req["request_id"]

    confirm = await _wait_chunk(engine, "tool.reconcile_needs_confirm")
    assert confirm is not None
    assert confirm["request_id"] == rid and confirm["tool_name"] == "bash"
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv is not None
    assert inv.idempotency == "non-idempotent"
    # 判不出写类执行与否 → 账停在 issued（ambiguous）→ 该回合后续 regenerate 被 C-1 门槛拦截
    assert inv.state == InvocationState.ISSUED
    # 单回合仍正常收尾（LLM 被喂"结果不确定需确认"失败），消息终态 completed
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"
