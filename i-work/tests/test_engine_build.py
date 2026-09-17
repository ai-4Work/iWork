import asyncio
import pytest
from server.models.message import MessageCreate
from server.llm.client import LLMChunk


@pytest.mark.asyncio
async def test_build_mode_step_by_step(engine_manager, active_session, fake_llm):
    active_session.mode = "build"
    fake_llm.responses = [
        # turn 1: LLM 发出 tool_use
        [
            LLMChunk(type="tool_use", tool_name="read_file",
                     tool_call_id="tc1",
                     tool_input={"path": "/tmp/test/src/utils.ts"}),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        # turn 2: 获取结果后完成
        [
            LLMChunk(type="text", delta="File read. Task done."),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
    ]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="Refactor src/utils.ts",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    # 等待 build.step_pending
    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "build.step_pending" for c in chunks):
            break

    chunks = engine.stream_buffer.drain()
    assert "build.step_pending" in [c["type"] for c in chunks]

    # 用户确认
    engine.sync_waiter.resolve_all(active_session.id, "confirm")

    # 等待工具执行进入第二次等待（等待客户端工具结果）
    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "client.tool_request" for c in chunks):
            break

    # 提供工具结果
    engine.sync_waiter.resolve_all(active_session.id, {"success": True})

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    final_types = [c["type"] for c in engine.stream_buffer.drain()]
    assert "message.complete" in final_types


@pytest.mark.asyncio
async def test_tool_result_network_approvals_emit(engine_manager, active_session, fake_llm):
    """阶段2：/tool-result body 带 network_approvals → 服务端 emit tool.network_approval 审计事件。"""
    from server.observability.event_bus import EventBus
    from server.observability.events import AgentEventType

    active_session.mode = "build"
    fake_llm.responses = [
        [
            LLMChunk(type="tool_use", tool_name="bash",
                     tool_call_id="tc1",
                     tool_input={"command": "npm install"}),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        [
            LLMChunk(type="text", delta="Done."),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
    ]

    engine = await engine_manager.get_or_create(active_session)
    bus = EventBus()
    engine._event_bus = bus
    seen = []

    async def record(ev):
        seen.append(ev)

    bus.subscribe(record)

    await engine.enqueue("test-user", MessageCreate(
        content="Install deps",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "build.step_pending" for c in chunks):
            break

    engine.sync_waiter.resolve_all(active_session.id, "confirm")

    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "client.tool_request" for c in chunks):
            break

    engine.sync_waiter.resolve_all(active_session.id, {
        "success": True,
        "network_approvals": [
            {"host": "registry.npmjs.org", "protocol": "https",
             "decision": "allow", "approved": True},
        ],
    })

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    network_events = [ev for ev in seen if ev.type == AgentEventType.NETWORK_APPROVAL]
    assert len(network_events) == 1
    assert network_events[0].data["host"] == "registry.npmjs.org"
    assert network_events[0].data["approved"] is True


@pytest.mark.asyncio
async def test_build_mode_skip(engine_manager, active_session, fake_llm):
    active_session.mode = "build"
    fake_llm.responses = [
        [
            LLMChunk(type="tool_use", tool_name="bash",
                     tool_call_id="tc1",
                     tool_input={"command": "rm -rf /"}),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        [
            LLMChunk(type="text", delta="Step skipped. Done."),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
    ]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="Delete everything",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(c["type"] == "build.step_pending" for c in engine.stream_buffer.drain()):
            break

    engine.sync_waiter.resolve_all(active_session.id, "skip")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    final_types = [c["type"] for c in engine.stream_buffer.drain()]
    assert "message.complete" in final_types


@pytest.mark.asyncio
async def test_build_mode_abort(engine_manager, active_session, fake_llm):
    active_session.mode = "build"
    fake_llm.responses = [[
        LLMChunk(type="tool_use", tool_name="bash",
                 tool_call_id="tc1",
                 tool_input={"command": "ls"}),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="List files",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(c["type"] == "build.step_pending" for c in engine.stream_buffer.drain()):
            break

    engine.sync_waiter.resolve_all(active_session.id, "abort")

    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    # abort 后消息停止处理，不应有 message.complete
    final_types = [c["type"] for c in engine.stream_buffer.drain()]
    assert "message.complete" not in final_types


@pytest.mark.asyncio
async def test_client_tool_request_carries_policy(engine_manager, active_session, fake_llm):
    """阶段1：client.tool_request chunk 随包下发 policy（filesystem/network/sandbox）。"""
    active_session.mode = "build"
    fake_llm.responses = [
        [
            LLMChunk(type="tool_use", tool_name="read_file",
                     tool_call_id="tc1",
                     tool_input={"path": "/tmp/test/src/utils.ts"}),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        [
            LLMChunk(type="text", delta="File read. Task done."),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
    ]

    engine = await engine_manager.get_or_create(active_session)
    await engine.enqueue("test-user", MessageCreate(
        content="Refactor src/utils.ts",
        scene_mode="code", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="build",
    ))

    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        if any(c["type"] == "build.step_pending" for c in chunks):
            break

    engine.sync_waiter.resolve_all(active_session.id, "confirm")

    req = None
    for _ in range(50):
        await asyncio.sleep(0.01)
        chunks = engine.stream_buffer.drain()
        req = next((c for c in chunks if c["type"] == "client.tool_request"), None)
        if req is not None:
            break

    assert req is not None, "未收到 client.tool_request chunk"
    policy = req.get("policy")
    assert isinstance(policy, dict)
    assert {"filesystem", "network", "sandbox"} <= policy.keys()
    assert isinstance(policy["filesystem"], dict)

    engine.sync_waiter.resolve_all(active_session.id, {"success": True})
    for _ in range(50):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    final_types = [c["type"] for c in engine.stream_buffer.drain()]
    assert "message.complete" in final_types
