"""M4 · truncate + 显式重跑：regenerate（截断重跑）/ continue（原地续跑）。

覆盖 M4 验收：
  - regenerate 后 conversation_history 无重复 user 行、消息行不新增、终态 completed 且内容替换；
  - 尾巴带非 read-only issued 工具账时返回 needs_confirm、不自动入队；
  - continue 从已落历史续跑（assistant 行新增一轮，user 行保持唯一）。
"""
import asyncio

import pytest

from server.llm.client import LLMChunk
from server.models.message import MessageStatus
from server.models.tool_invocation import ToolInvocation, InvocationState


def _ask_msg(content="hello"):
    from server.models.message import MessageCreate
    return MessageCreate(
        content=content, scene_mode="office", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="ask",
    )


async def _wait_idle(engine, rounds=300):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


def _user_rows(rows, mid: str) -> list[dict]:
    return [r for r in rows if r.get("message_id") == mid and r.get("role") == "user"]


def _assistant_rows(rows, mid: str) -> list[dict]:
    return [r for r in rows if r.get("message_id") == mid and r.get("role") == "assistant"]


def _assistant_texts(rows, mid: str) -> list[str]:
    return [r.get("content") or "" for r in _assistant_rows(rows, mid)]


async def _history(engine) -> list[dict]:
    return await engine.context_mgr.list_rows(engine.session.id)


@pytest.mark.asyncio
async def test_regenerate_replaces_content_no_dup_user_row(
    engine_manager, active_session, fake_llm,
):
    fake_llm.responses = [
        [LLMChunk(type="text", delta="first reply."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.COMPLETED

    rows = await _history(engine)
    assert len(_user_rows(rows, str(msg.id))) == 1
    assert "first reply." in _assistant_texts(rows, str(msg.id))

    # regenerate：截断该消息旧回复 → 同 Message 行重跑（无新消息行）
    out = await engine.reprocess(msg.id, "regenerate")
    assert out["status"] == "scheduled"
    assert await _wait_idle(engine)

    # 消息行不新增：同一条 Message 行重跑，仅终态重置为 completed
    assert msg.status == MessageStatus.COMPLETED

    rows = await _history(engine)
    # user 行不重复（截断只删该消息自身边界后，user 锚点行保留）
    assert len(_user_rows(rows, str(msg.id))) == 1
    # 旧回复已被替换：FakeLLMClient 无后续 response 时回退"假回复。"
    texts = _assistant_texts(rows, str(msg.id))
    assert texts, "regenerate 后应存在新 assistant 行"
    assert "first reply." not in texts
    assert "假回复。" in texts


@pytest.mark.asyncio
async def test_regenerate_invalid_and_not_found(engine_manager, active_session, fake_llm):
    fake_llm.responses = [
        [LLMChunk(type="text", delta="ok"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    assert (await engine.reprocess(__import__("uuid").uuid4(), "regenerate"))["status"] == "not_found"
    # 任意已存在消息 + 非法 mode
    rows = await _history(engine)
    mid = rows[0]["message_id"]
    assert (await engine.reprocess(mid, "nonsense"))["status"] == "invalid_mode"


@pytest.mark.asyncio
async def test_regenerate_blocked_by_non_readonly_issued_tail(
    engine_manager, active_session, fake_llm,
):
    """C-1 账尾检查：尾巴存在非 read-only 的 issued 工具账 → needs_confirm，不自动重放。"""
    fake_llm.responses = [
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    # 模拟该消息尾巴残留一条结果未定的写类工具账
    await engine._invocation_repo.create(ToolInvocation(
        session_id=engine.session.id, message_id=msg.id, tool_name="bash",
        location="client", state=InvocationState.ISSUED, idempotency="non-idempotent",
        input={"command": "rm -rf /tmp/x"},
    ))

    out = await engine.reprocess(msg.id, "regenerate")
    assert out["status"] == "needs_confirm"
    assert any(t["invocation_id"] and t["tool_name"] == "bash" for t in out["ambiguous_tools"])
    # 未入队：_reprocess_pending 空，不会被执行
    assert not engine._reprocess_pending

    # read-only 尾巴不拦：同工具改标 read-only → 放行
    await engine._invocation_repo.mark(
        engine.session.id, out["ambiguous_tools"][0]["invocation_id"],
        "superseded",
    )
    await engine._invocation_repo.create(ToolInvocation(
        session_id=engine.session.id, message_id=msg.id, tool_name="read_file",
        location="client", state=InvocationState.ISSUED, idempotency="read-only",
    ))
    out2 = await engine.reprocess(msg.id, "regenerate")
    assert out2["status"] == "scheduled"
    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.COMPLETED


@pytest.mark.asyncio
async def test_continue_resumes_from_persisted_history(
    engine_manager, active_session, fake_llm,
):
    fake_llm.responses = [
        [LLMChunk(type="text", delta="turn-zero."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    msg = await engine.enqueue("test-user", _ask_msg())
    assert await _wait_idle(engine)

    rows = await _history(engine)
    assert len(_assistant_rows(rows, str(msg.id))) == 1

    out = await engine.reprocess(msg.id, "continue")
    assert out["status"] == "scheduled"
    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.COMPLETED

    rows = await _history(engine)
    # 不截断、不重复 user 行；续跑追加一轮 assistant（fallback 假回复）
    assert len(_user_rows(rows, str(msg.id))) == 1
    assert len(_assistant_rows(rows, str(msg.id))) >= 2
