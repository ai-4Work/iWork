"""C-3 · 幂等档标注与重放边界。

覆盖 M7 验收：
  - 标注随包下发：client.tool_request 携带 idempotency hint；
  - 账本按档落标：read-only / idempotent / non-idempotent；
  - 只读工具结果不确定 → 原调用 superseded，LLM 重试走全新 issued（安全重放边界）；
  - non-idempotent 客户端工具失败不会被服务端自动补发重跑（客户端回执为准）。
"""
import asyncio
import pytest

from server.models.message import MessageCreate
from server.llm.client import LLMChunk
from server.models.tool_invocation import InvocationState
from server.tools.idempotency import tool_idempotency


async def _wait_chunk(engine, ctype, loops=400):
    for _ in range(loops):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            if c["type"] == ctype:
                return c
    return None


async def _wait_idle(engine, loops=400):
    for _ in range(loops):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


async def _wait_new_request(engine, exclude):
    """轮询直到出现 request_id 不在 exclude 的 client.tool_request。"""
    for _ in range(400):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            if c["type"] == "client.tool_request" and c["request_id"] not in exclude:
                return c
    return None


def _read_chunk(path):
    return LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc",
                    tool_input={"path": path})


# ---- 纯分类单元 ----

def test_tool_idempotency_classifier():
    assert tool_idempotency("read_file") == "read-only"
    assert tool_idempotency("glob") == "read-only"
    assert tool_idempotency("grep") == "read-only"
    assert tool_idempotency("recall") == "read-only"
    assert tool_idempotency("memory_search") == "read-only"
    assert tool_idempotency("write_file") == "idempotent"
    # 默认保守
    assert tool_idempotency("bash") == "non-idempotent"
    assert tool_idempotency("edit_file") == "non-idempotent"
    assert tool_idempotency("skill") == "non-idempotent"
    # MCP 命名空间工具名未知 → 默认 non-idempotent
    assert tool_idempotency("some_server_do_write") == "non-idempotent"


# ---- 引擎行为 ----

async def _mk_engine(engine_manager, active_session, fake_llm, responses):
    active_session.mode = "build"
    fake_llm.responses = responses
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 0.05
    return engine


@pytest.mark.asyncio
async def test_tool_request_carries_idempotency_hint_and_ledger(engine_manager, active_session, fake_llm):
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_read_chunk("/tmp/test/a.txt")],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    msg = await engine.enqueue("test-user", MessageCreate(
        content="read", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    assert req["idempotency"] == "read-only"  # 标注随包下发

    out = await engine.submit_client_tool_result(req["request_id"], {"success": True, "output": "A"})
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, req["request_id"])
    assert inv.idempotency == "read-only"
    assert inv.state == InvocationState.COMPLETED


@pytest.mark.asyncio
async def test_readonly_uncertain_superseded_then_retry_succeeds(
    engine_manager, active_session, fake_llm,
):
    """只读工具结果不确定 → 原 invocation superseded；LLM 重试走全新 issued，收口 completed。"""
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_read_chunk("/tmp/test/a.txt")],
        [_read_chunk("/tmp/test/b.txt")],  # 上一条不确定后 LLM 换参重试
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    msg = await engine.enqueue("test-user", MessageCreate(
        content="read files", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req1 = await _wait_chunk(engine, "client.tool_request")
    assert req1 is not None and req1["idempotency"] == "read-only"
    rid1 = req1["request_id"]

    # 无客户端应答 → 不确定 → superseded；引擎继续，LLM 发出第二条 read → 新 request
    req2 = await _wait_new_request(engine, exclude={rid1})
    assert req2 is not None, "未收到 LLM 重试后的新 client.tool_request"
    assert req2["request_id"] != rid1
    assert req2["idempotency"] == "read-only"

    out = await engine.submit_client_tool_result(req2["request_id"], {"success": True, "output": "B"})
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv1 = await engine._invocation_repo.get(engine.session.id, rid1)
    assert inv1 is not None and inv1.state == InvocationState.SUPERSEDED  # 原调用作废
    inv2 = await engine._invocation_repo.get(engine.session.id, req2["request_id"])
    assert inv2.state == InvocationState.COMPLETED
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"


@pytest.mark.asyncio
async def test_non_idempotent_failure_not_auto_reissued_by_server(
    engine_manager, active_session, fake_llm,
):
    """non-idempotent（bash）客户端工具失败 → 服务端不自动补发重跑，仅客户端可再次执行。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc",
                  tool_input={"command": "echo hi"})],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]
    engine = await engine_manager.get_or_create(active_session)
    engine._tool_wait_timeout = 0.05
    engine._tool_reconcile_timeout = 0.05

    msg = await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))
    req = await _wait_chunk(engine, "client.tool_request")
    assert req is not None
    assert req["idempotency"] == "non-idempotent"
    rid = req["request_id"]

    # 客户端回执"已执行但失败" → 正常收口为 completed(error)
    out = await engine.submit_client_tool_result(
        rid, {"success": False, "error": "exit 1", "exit_code": 1},
    )
    assert out.get("duplicate") is None
    assert await _wait_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED  # 客户端确已执行（以回执为准，不是 issued/needs_confirm）

    # 服务端全程只下发了这一次 client.tool_request（未擅自补发重跑）
    requests = [c for c in engine.stream_buffer.drain() if c["type"] == "client.tool_request"]
    assert len(requests) == 1
    assert (await engine._message_repo.get(msg.id)).status.value == "completed"
