"""单轮多工具的分段调度（docs/chapters/14 §2）：读段并发下发、写段串行、历史保序。

用 fake_llm 造"一轮里同时吐多个 tool_use"的流，断言：
  - 读段把整段的 client.tool_request 一口气推完才等回投（不再是"推一条等一条"）；
  - 写工具必须等前面的读全部回投才下发；
  - 回投乱序也不影响落库顺序：tool 行按模型发出顺序，sequence 连号。
"""
import asyncio

import pytest

from server.models.message import MessageCreate
from server.models.tool_invocation import InvocationState
from server.llm.client import LLMChunk


def _tool(name: str, tc_id: str, **tool_input) -> LLMChunk:
    return LLMChunk(type="tool_use", tool_name=name, tool_call_id=tc_id,
                    tool_input=tool_input)


def _tail(text: str = "done.") -> list[LLMChunk]:
    return [LLMChunk(type="text", delta=text),
            LLMChunk(type="end_turn", stop_reason="end_turn")]


async def _enqueue(engine) -> None:
    await engine.enqueue("test-user", MessageCreate(
        content="go", scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))


async def _new_requests(
    engine, seen: set, want: int, loops: int = 300,
) -> list[dict]:
    """收集至少 want 条**新**的 client.tool_request，按到达顺序。

    StreamBuffer.drain() 不清空（它是断线回放用的），所以按 request_id 去重。
    """
    got: list[dict] = []
    for _ in range(loops):
        await asyncio.sleep(0.01)
        for c in engine.stream_buffer.drain():
            rid = c.get("request_id")
            if c["type"] == "client.tool_request" and rid not in seen:
                seen.add(rid)
                got.append(c)
        if len(got) >= want:
            break
    return got


async def _wait_idle(engine, loops: int = 300) -> bool:
    for _ in range(loops):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            return True
    return False


@pytest.mark.asyncio
async def test_read_segment_dispatches_whole_segment_before_waiting(
    engine_manager, active_session, fake_llm,
):
    """两条只读必须一起下发：两条的账都已 issued 而无人回投 → 说明没在"推一条等一条"。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [_tool("read_file", "tc1", path="/tmp/test/a.txt"),
         _tool("read_file", "tc2", path="/tmp/test/b.txt")],
        _tail(),
    ]
    engine = await engine_manager.get_or_create(active_session)
    await _enqueue(engine)

    seen: set = set()
    reqs = await _new_requests(engine, seen, 2)
    assert [r["tool_name"] for r in reqs] == ["read_file", "read_file"]
    # 下发即落 issued 账；此刻两条都还没回投
    for r in reqs:
        inv = await engine._invocation_repo.get(engine.session.id, r["request_id"])
        assert inv is not None and inv.state == InvocationState.ISSUED

    for r in reqs:
        await engine.submit_client_tool_result(
            r["request_id"], {"success": True, "content": "x"},
        )
    assert await _wait_idle(engine)


@pytest.mark.asyncio
async def test_write_waits_until_read_segment_settles(
    engine_manager, active_session, fake_llm,
):
    """写段串行且排在读段之后：读没回投完，写工具不下发。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [_tool("read_file", "tc1", path="/tmp/test/a.txt"),
         _tool("read_file", "tc2", path="/tmp/test/b.txt"),
         _tool("write_file", "tc3", path="/tmp/test/out.txt", content="hi")],
        _tail(),
    ]
    engine = await engine_manager.get_or_create(active_session)
    await _enqueue(engine)

    seen: set = set()
    reads = await _new_requests(engine, seen, 2)
    assert [r["tool_name"] for r in reads] == ["read_file", "read_file"]

    await asyncio.sleep(0.1)
    assert await _new_requests(engine, seen, 1, loops=3) == [], "读还没回投，写不该下发"

    for r in reads:
        await engine.submit_client_tool_result(
            r["request_id"], {"success": True, "content": "x"},
        )
    writes = await _new_requests(engine, seen, 1)
    assert [r["tool_name"] for r in writes] == ["write_file"]

    await engine.submit_client_tool_result(writes[0]["request_id"], {"success": True})
    assert await _wait_idle(engine)


@pytest.mark.asyncio
async def test_history_follows_model_emit_order(
    engine_manager, active_session, fake_llm,
):
    """读段乱序回投也不乱落库：tool 行按模型发出顺序，sequence 连号不重。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [_tool("read_file", "tc1", path="/tmp/test/a.txt"),
         _tool("read_file", "tc2", path="/tmp/test/b.txt"),
         _tool("write_file", "tc3", path="/tmp/test/out.txt", content="hi")],
        _tail(),
    ]
    engine = await engine_manager.get_or_create(active_session)
    await _enqueue(engine)

    seen: set = set()
    reads = await _new_requests(engine, seen, 2)
    by_tc = {r["tool_call_id"]: r for r in reads}
    # 故意反序回投：先 tc2 再 tc1
    await engine.submit_client_tool_result(
        by_tc["tc2"]["request_id"], {"success": True, "content": "B"},
    )
    await engine.submit_client_tool_result(
        by_tc["tc1"]["request_id"], {"success": True, "content": "A"},
    )
    write = (await _new_requests(engine, seen, 1))[0]
    assert write["tool_call_id"] == "tc3"
    await engine.submit_client_tool_result(write["request_id"], {"success": True})
    assert await _wait_idle(engine)

    rows = await engine.context_mgr.list_rows(engine.session.id)
    tool_rows = [r for r in rows if r["role"] == "tool"]
    assert [r["tool_call_id"] for r in tool_rows] == ["tc1", "tc2", "tc3"]
    seqs = [r["sequence"] for r in tool_rows]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))

    # tool_calls 数组合并进同一条 assistant 行，顺序同样是模型发出顺序
    with_calls = [r for r in rows if r["role"] == "assistant" and r.get("tool_calls")]
    assert len(with_calls) == 1
    assert [t["id"] for t in with_calls[0]["tool_calls"]] == ["tc1", "tc2", "tc3"]
