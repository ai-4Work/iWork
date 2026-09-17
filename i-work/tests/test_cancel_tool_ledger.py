"""取消（方案 X）→ 在途工具自然落地 + 副作用如实 + 继续不重跑。

覆盖用户实测缺陷的修复（服务端收敛账本，前端 / DB schema 不改）：
  - 缺陷 1：工具实已成功执行，但 D 副作用清单那条显示「失败」。
    根因 mark() 转 completed 不清 error 残留；修复 = completed 分支清 error=None。
  - 缺陷 2：取消后点「继续」，同一工具又执行一遍（副作用重复）。
    根因 abort 打断在途工具等待 → 结果没写进上下文 → 续跑时 LLM 重发同一 tool_use。
    修复 = /cancel 不再 resolve tool key（方案 X）：在途客户端工具保持阻塞等待真实
    回投 → 正常落地（completed + 注入上下文）→ 取消只停文本与后续工具，继续绝不重跑。
"""
import asyncio

import pytest

from server.llm.client import LLMChunk
from server.models.message import MessageCreate, MessageStatus
from server.models.tool_invocation import ToolInvocation, InvocationState
from server.storage.memory import InMemoryToolInvocationRepo
from server.tools.idempotency import tool_side_effect


def _bash_chunk(command):
    return LLMChunk(type="tool_use", tool_name="bash", tool_call_id="tc",
                    tool_input={"command": command})


def _text_turn(text="done."):
    return [LLMChunk(type="text", delta=text),
            LLMChunk(type="end_turn", stop_reason="end_turn")]


async def _mk_engine(engine_manager, active_session, fake_llm, responses):
    active_session.mode = "build"
    fake_llm.responses = responses
    engine = await engine_manager.get_or_create(active_session)
    # 放大首段等待窗，避免下一条工具请求轮询期撞进对账窗造成竞态
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


async def _wait_idle(engine, rounds=600):
    for _ in range(rounds):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


@pytest.mark.asyncio
async def test_mark_completed_clears_stale_error():
    """缺陷 1 根因：mark() 转 completed 必须清 error，completed 行永不带状态机异常标记。

    回归：旧 abort 模型给 superseded(error=cancel_aborted) 的行迟到升级 completed 时，
    error 残留 → GET /effects 返回 error → 前端 effectTone 判失败。
    """
    from uuid import uuid4

    repo = InMemoryToolInvocationRepo()
    sid, mid, iid = uuid4(), uuid4(), uuid4()
    await repo.create(ToolInvocation(
        invocation_id=iid, session_id=sid, message_id=mid,
        tool_name="bash", location="client", state=InvocationState.ISSUED,
        side_effect=tool_side_effect("bash"), idempotency="non-idempotent",
    ))
    # 先落一个异常态（带 error），再升级 completed
    await repo.mark(sid, iid, "superseded", error="cancel_aborted")
    inv = await repo.get(sid, iid)
    assert inv.state == InvocationState.SUPERSEDED
    assert inv.error == "cancel_aborted"
    await repo.mark(sid, iid, "completed", result={"success": True})
    inv = await repo.get(sid, iid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.error is None  # 修复点：completed 不残留状态机异常
    effects = await repo.list_invocations(sid, mid)
    assert [e.tool_name for e in effects] == ["bash"]
    assert all(e.error is None for e in effects)


@pytest.mark.asyncio
async def test_cancel_running_tool_lands_then_stops_continue_no_rerun(
    engine_manager, active_session, fake_llm,
):
    """方案 X 核心语义：工具执行中取消 → 工具落地 completed → 消息 cancelled →
    副作用如实（无 error）→ 点「继续」直通且不重跑该工具。

    回归缺陷 1（effect 误报失败）+ 缺陷 2（继续重跑工具）+ 原「取消后继续无反应」。
    """
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_bash_chunk("echo hi")],          # 首跑：只发工具，不 end_turn
        _text_turn(),                       # 续跑（continue）：收尾文本
    ])
    msg = await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_tool_request(engine)
    assert req is not None and req["tool_name"] == "bash"
    rid = req["request_id"]

    # 方案 X：取消只置 _cancel_event，不 resolve_all("abort") —— 不打断在途工具等待
    engine.request_cancel()
    # 客户端本地工具跑完回投真实结果 → 正常 resolve → 工具落地（completed + 注入上下文）
    res = await engine.submit_client_tool_result(rid, {"success": True, "output": "hi"})
    assert res.get("reconciled_late") is not True  # 走引擎正常落地路径，非迟到收口

    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.CANCELLED  # 工具落地后才停（回归场景成立）

    # 缺陷 1 修复：工具 completed 且不带 error → 副作用如实、前端不判失败
    repo = engine._invocation_repo
    inv = await repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.error is None
    effects = await repo.list_invocations(engine.session.id, msg.id)
    assert [e.tool_name for e in effects] == ["bash"]
    assert all(e.error is None for e in effects)

    # 原「取消后继续无反应」：取消不再留 issued 尾巴 → continue 直通（不 needs_confirm）
    out = await engine.reprocess(msg.id, "continue")
    assert out["status"] == "scheduled"
    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.COMPLETED

    # 缺陷 2 修复：继续未重跑该工具 —— 副作用清单仍只有 1 条 bash（无重复执行）
    effects = await repo.list_invocations(engine.session.id, msg.id)
    assert [e.tool_name for e in effects] == ["bash"]


@pytest.mark.asyncio
async def test_ambiguous_leftover_late_result_upgrades(
    engine_manager, active_session, fake_llm,
):
    """取消后客户端迟到真实结果：真·ambiguous 尾巴保留 → 迟到回投升 completed 如实入账。

    对账窗判定不出且引擎已 cancelled 时，非幂等写类的 issued 尾巴不被强收口（C-1
    needs_confirm 门槛保留）；工具实已执行时，迟到真实回投（submit_client_tool_result
    无等待者分支）把它从 issued 升回 completed，副作用清单如实包含。
    """
    engine = await _mk_engine(engine_manager, active_session, fake_llm, [
        [_bash_chunk("echo hi")],          # 只发工具，不 end_turn
    ])
    # 收窄等待窗，让"客户端静默"在对账窗内自然收敛，避免长 wall-time
    engine._tool_wait_timeout = 0.5
    engine._tool_reconcile_timeout = 0.05
    msg = await engine.enqueue("test-user", MessageCreate(
        content="run", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    req = await _wait_tool_request(engine)
    assert req is not None and req["tool_name"] == "bash"
    rid = req["request_id"]

    # 方案 X 取消：只置标志、不打断等待；客户端也静默（无回投）→ 首段超时进对账窗
    engine.request_cancel()
    # 对账窗内客户端仍无应答 → bash(非幂等) 走 ambiguous：账留 issued、本轮 needs_confirm
    assert await _wait_idle(engine)
    assert msg.status == MessageStatus.CANCELLED

    repo = engine._invocation_repo
    inv = await repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.ISSUED  # 真 ambiguous 不被强收口（C-1 门槛保留）

    # 客户端本地其实执行完 → 迟到真实结果经无等待者分支升 completed，副作用如实入账
    res = await engine.submit_client_tool_result(rid, {"success": True, "output": "hi"})
    assert res.get("reconciled_late") is True

    inv = await repo.get(engine.session.id, rid)
    assert inv.state == InvocationState.COMPLETED
    assert inv.error is None
    effects = await repo.list_invocations(engine.session.id, msg.id)
    assert [e.tool_name for e in effects] == ["bash"]
