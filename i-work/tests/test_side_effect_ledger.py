"""阶段 D · 副作用账本。

覆盖文档 17.D 验收：
  - side_effect 标注随工具分类派生（read-only 标 false，写类标 true）；
  - repo.list_invocations 全量返回该会话受账调用：不限 state、不限 side_effect，
    四态与只读都入账本，读/写由行上 side_effect 分辨；
  - 引擎为每条工具账盖章 attempt（D 运行序号）：首跑/regenerate 递增开新块，continue 复用当前；
  - 不再随流推 message.effects——前端在消息终态/会话加载时 GET /effects，按 attempt 分块渲染。
"""
import asyncio
from uuid import uuid4

import pytest

from server.llm.client import LLMChunk
from server.models.message import MessageCreate
from server.models.tool_invocation import (
    ToolInvocation, InvocationState, normalize_client_result,
)
from server.storage.memory import InMemoryToolInvocationRepo
from server.tools.idempotency import tool_side_effect


# ---- 纯分类单元 ----

def test_tool_side_effect_classifier():
    # read-only 永不落账
    assert tool_side_effect("read_file") is False
    assert tool_side_effect("glob") is False
    assert tool_side_effect("grep") is False
    assert tool_side_effect("recall") is False
    assert tool_side_effect("load_memory") is False
    # 写类（即使幂等可重放的整文件覆盖写）都构成副作用
    assert tool_side_effect("write_file") is True
    assert tool_side_effect("bash") is True
    assert tool_side_effect("write_memory") is True
    assert tool_side_effect("edit_file") is True
    # MCP 未知 → 默认写类保守
    assert tool_side_effect("some_server_do_write") is True


# ---- 客户端结果归一：status → success（账本/审计/UI 的统一契约） ----

def test_normalize_client_result_maps_status_to_success():
    """客户端用 status 表达成败，账本契约读 success —— 入口必须补齐，否则失败被读成成功。"""
    # 客户端 ToolResult 形状
    assert normalize_client_result({"status": "error", "error": "boom"})["success"] is False
    assert normalize_client_result({"status": "success", "output": "ok"})["success"] is True
    # 服务端内部形状已有 success → 原样，不臆造 status
    assert normalize_client_result({"success": False})["success"] is False
    assert "status" not in normalize_client_result({"success": True})
    # 非 dict 透传
    assert normalize_client_result(None) is None
    assert normalize_client_result("boom") == "boom"
    # reconcile 应答信封 → 递归下钻到信封里的 result
    env = normalize_client_result({
        "reconcile": True, "state": "executed",
        "result": {"status": "error", "exit_code": 1},
    })
    assert env["result"]["success"] is False
    assert env["state"] == "executed"
    assert env["reconcile"] is True


# ---- repo.list_invocations 全量账本 ----

@pytest.mark.asyncio
async def test_list_invocations_returns_all_states_and_read_only():
    repo = InMemoryToolInvocationRepo()
    sid, mid = uuid4(), uuid4()
    other_mid = uuid4()

    async def seeded(name, *, side=True, state="completed", message=mid):
        inv = ToolInvocation(
            invocation_id=uuid4(), session_id=sid, message_id=message,
            tool_name=name, location="client", side_effect=side,
            idempotency="non-idempotent", state=InvocationState.ISSUED,
        )
        await repo.create(inv)
        if state != "issued":
            await repo.mark(
                sid, inv.invocation_id, state,
                result={"success": True} if state == "completed" else None,
                error=None if state == "completed" else "boom",
            )
        return inv

    await seeded("bash", side=True, state="completed")
    await seeded("write_file", side=True, state="completed")
    await seeded("read_file", side=False, state="completed")       # 只读 → 也入账本
    await seeded("bash_skipped", side=True, state="skipped")       # skipped → 也入账本
    await seeded("bash_sup", side=True, state="superseded")        # superseded → 也入账本
    await seeded("bash_issued", side=True, state="issued")         # issued → 也入账本
    await seeded("bash_other_msg", side=True, state="completed", message=other_mid)

    all_rows = await repo.list_invocations(sid)
    by_name = {e.tool_name: e for e in all_rows}
    assert set(by_name) == {
        "bash", "write_file", "read_file",
        "bash_skipped", "bash_sup", "bash_issued", "bash_other_msg",
    }
    assert by_name["read_file"].side_effect is False
    assert by_name["bash"].side_effect is True
    assert by_name["bash_skipped"].state == InvocationState.SKIPPED
    assert by_name["bash_sup"].state == InvocationState.SUPERSEDED
    assert by_name["bash_issued"].state == InvocationState.ISSUED

    # message_id 过滤仍然生效：只排除挂在别的消息上的那条
    mid_rows = await repo.list_invocations(sid, mid)
    assert {e.tool_name for e in mid_rows} == set(by_name) - {"bash_other_msg"}


# ---- 引擎集成：终态 message.effects 如实交代 ----

def _read_chunk(path):
    return LLMChunk(type="tool_use", tool_name="read_file", tool_call_id="tc",
                    tool_input={"path": path})


def _bash_chunk(command):
    return LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc",
                    tool_input={"command": command})


async def _mk_engine(engine_manager, active_session, fake_llm, responses):
    active_session.mode = "build"
    fake_llm.responses = responses
    engine = await engine_manager.get_or_create(active_session)
    # 放大首段等待窗，避免"下一条工具请求"轮询期撞进 50ms 对账窗造成竞态。
    engine._tool_wait_timeout = 2.0
    engine._tool_reconcile_timeout = 0.05
    return engine


async def _wait_tool_request(engine, loops=400):
    for _ in range(loops):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            if c["type"] == "client.tool_request":
                return c
    return None


async def _collect_until_idle(engine, loops=600):
    """收集全部 chunk 直到 IDLE（drain 不丢，可查 message.effects）。"""
    out = []
    for _ in range(loops):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            out.append(c)
        if engine.state == "IDLE":
            break
    return out


@pytest.mark.asyncio
async def test_read_only_completion_no_effects(engine_manager, active_session, fake_llm):
    """read-only 工具 completed → 账 side_effect=False，终态不发 message.effects。"""
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_read_chunk("/tmp/test/a.txt")],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    msg = await engine.enqueue("test-user", MessageCreate(
        content="read", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_tool_request(engine)
    assert req is not None
    rid = req["request_id"]
    await engine.submit_client_tool_result(rid, {"success": True, "output": "A"})

    chunks = await _collect_until_idle(engine)
    assert not any(c["type"] == "message.effects" for c in chunks)
    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.side_effect is False
    assert inv.idempotency == "read-only"
    assert inv.attempt == 1  # 首跑仍盖章运行序号（side_effect=False 不入 effects 清单）


@pytest.mark.asyncio
async def test_write_completion_records_effect_with_attempt(engine_manager, active_session, fake_llm):
    """bash（写类客户端工具）completed → 账 side_effect=True 且 attempt=1（首跑开新块）。

    D 改版后不再随流推 message.effects：副作用清单由前端在终态/加载时 GET /effects
    按 attempt 分块重建，引擎只负责把 attempt 盖章进 tool_invocations 账。
    """
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_bash_chunk("echo hi")],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    msg = await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_tool_request(engine)
    assert req is not None and req["tool_name"] == "bash"
    rid = req["request_id"]
    await engine.submit_client_tool_result(rid, {"success": True, "output": "hi"})

    chunks = await _collect_until_idle(engine)
    assert not any(c["type"] == "message.effects" for c in chunks)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.side_effect is True
    assert inv.state == InvocationState.COMPLETED
    assert inv.attempt == 1

    # 全会话聚合（GET /effects 同源）：这一轮只有这条写动作，带 attempt
    all_rows = await engine._invocation_repo.list_invocations(engine.session.id)
    assert [e.tool_name for e in all_rows] == ["bash"]
    assert [e.attempt for e in all_rows] == [1]


@pytest.mark.asyncio
async def test_client_tool_failure_lands_success_false(engine_manager, active_session, fake_llm):
    """真实客户端形状的失败结果（status='error'，无 success 字段）→ 账里 success=False。

    回归：客户端发 {"status":"error",...}，而账本/审计/UI 读 result.success。
    不归一 → 失败被读成成功，副作用清单显示绿色「完成」。
    """
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_bash_chunk('python -c "import pypdf"')],
        [LLMChunk(type="text", delta="done."),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ])
    await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_tool_request(engine)
    assert req is not None and req["tool_name"] == "bash"
    rid = req["request_id"]
    await engine.submit_client_tool_result(rid, {
        "status": "error",
        "error": "ModuleNotFoundError: No module named 'pypdf'",
        "output": "",
        "exit_code": 1,
        "duration_ms": 12,
    })

    await _collect_until_idle(engine)

    inv = await engine._invocation_repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.result["success"] is False       # ← 前端 effectTone 据此标红
    assert inv.result["status"] == "error"      # 客户端原字段保留

    all_rows = await engine._invocation_repo.list_invocations(engine.session.id)
    assert [e.tool_name for e in all_rows] == ["bash"]
    assert all_rows[0].result["success"] is False


@pytest.mark.asyncio
async def test_attempt_resolution_semantics(engine_manager, active_session):
    """D 分块规则（unit）：_resolve_attempt 依当前最大 attempt 决定本轮盖章。

      首跑/regenerate → max+1（开新块）；continue → max（并入当前块，最小 1）。
    """
    engine = await engine_manager.get_or_create(active_session)
    repo = engine._invocation_repo
    assert repo is not None
    sid = engine.session.id
    mid = uuid4()

    class _Msg:
        id = mid

    async def seed(attempt: int) -> None:
        await repo.create(ToolInvocation(
            invocation_id=uuid4(), session_id=sid, message_id=mid,
            attempt=attempt, tool_name="bash", location="client",
            side_effect=True, idempotency="non-idempotent",
            state=InvocationState.COMPLETED, result={"success": True},
        ))

    # 空消息首跑 → attempt 1
    assert await engine._resolve_attempt(_Msg(), None) == 1
    await seed(1)
    # 已有 attempt 1 时 continue → 复用 1（并入当前块，不新开）
    assert await engine._resolve_attempt(_Msg(), "continue") == 1
    # 已有 attempt 1 时 regenerate / 再次首跑（新 send）→ 2（开新块）
    assert await engine._resolve_attempt(_Msg(), "regenerate") == 2
    assert await engine._resolve_attempt(_Msg(), None) == 2
    # repo.max_attempt 与写入一致（跨消息隔离）
    assert await repo.max_attempt(sid, mid) == 1
    assert await repo.max_attempt(sid, uuid4()) == 0
