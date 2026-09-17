"""
Session lifecycle tests — CRUD + client_tools registration.
"""
import pytest
from uuid import uuid4
from server.models.session import Session, SessionCreate, ClientTool
from server.storage.memory import InMemorySessionRepo


# ═══════════════════════════════════════════════════════════════
# Repository-level tests
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_session_with_client_tools():
    repo = InMemorySessionRepo()
    tools = [
        ClientTool(name="bash", description="Run shell", input_schema={"type": "object"}),
        ClientTool(name="read_file", description="Read file", input_schema={"type": "object"}),
    ]
    s = Session(user_id="u1", client_tools=tools, model="claude-opus-4-7")
    await repo.create(s)

    got = await repo.get(s.id)
    assert got is not None
    assert len(got.client_tools) == 2
    assert got.client_tools[0].name == "bash"
    assert got.client_tools[1].name == "read_file"
    assert got.model == "claude-opus-4-7"
    assert got.title == "新建任务"


@pytest.mark.asyncio
async def test_session_list_by_user():
    repo = InMemorySessionRepo()
    s1 = Session(user_id="u1", model="x")
    s2 = Session(user_id="u1", model="y")
    s3 = Session(user_id="u2", model="z")
    for s in [s1, s2, s3]:
        await repo.create(s)

    u1_sessions, total = await repo.list_by_user("u1")
    assert total == 2
    assert len(u1_sessions) == 2

    u2_sessions, total = await repo.list_by_user("u2")
    assert total == 1
    assert u2_sessions[0].user_id == "u2"


@pytest.mark.asyncio
async def test_session_list_filter_by_status():
    repo = InMemorySessionRepo()
    s1 = Session(user_id="u1")
    s2 = Session(user_id="u1")
    await repo.create(s1)
    await repo.create(s2)
    await repo.archive(s2.id)

    active, total_active = await repo.list_by_user("u1", status="active")
    assert total_active == 1

    archived, total_archived = await repo.list_by_user("u1", status="archived")
    assert total_archived == 1

    all_s, total_all = await repo.list_by_user("u1", status="all")
    assert total_all == 2


@pytest.mark.asyncio
async def test_session_archive():
    repo = InMemorySessionRepo()
    s = Session(user_id="u1")
    await repo.create(s)
    assert s.status == "active"

    await repo.archive(s.id)
    assert s.status == "archived"


# ═══════════════════════════════════════════════════════════════
# Model-level tests
# ═══════════════════════════════════════════════════════════════

def test_session_create_model_validation():
    tools = [ClientTool(name="bash", description="desc", input_schema={})]
    sc = SessionCreate(
        id=uuid4(),
        mode="build",
        scene_mode="code",
        workspace="/tmp/project",
        model="claude-opus-4-7",
        client_tools=tools,
    )
    assert sc.mode == "build"
    assert len(sc.client_tools) == 1
    assert sc.client_tools[0].name == "bash"


def test_session_create_defaults():
    sc = SessionCreate()
    assert sc.mode == "build"
    assert sc.scene_mode == "office"
    assert sc.client_tools == []


def test_client_tool_empty_schema_is_valid():
    """input_schema 可以为空 dict（某些工具无参数）"""
    tool = ClientTool(name="noop", description="No-op tool", input_schema={})
    assert tool.input_schema == {}


# ═══════════════════════════════════════════════════════════════
# Integration: client_tools flow into engine context
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_client_tools_appear_in_context(engine_manager, active_session, sample_client_tools):
    """验证会话注册的 client_tools 出现在 context.build() 的返回结果中。"""
    engine = await engine_manager.get_or_create(active_session)

    # session_client_tools() 返回 dict 列表
    tool_dicts = engine._session_client_tools()
    assert len(tool_dicts) == 2
    assert tool_dicts[0]["name"] == "bash"
    assert tool_dicts[1]["name"] == "read_file"
    assert "input_schema" in tool_dicts[0]
    assert "description" in tool_dicts[0]


@pytest.mark.asyncio
async def test_context_includes_client_tools(engine_manager, active_session):
    """验证 context.build() 在非 ask 模式下包含客户端工具。"""
    engine = await engine_manager.get_or_create(active_session)

    ctx = await engine.context_mgr.build(
        active_session.id, turn=1, mode="build", scene_mode="code",
        client_tools=engine._session_client_tools(),
    )
    assert ctx.available_tools is not None
    tool_names = [t["name"] for t in ctx.available_tools]
    assert "bash" in tool_names
    assert "read_file" in tool_names


@pytest.mark.asyncio
async def test_context_ask_mode_excludes_tools(engine_manager, active_session):
    """Ask 模式 available_tools 为 None，即使注册了 client_tools。"""
    engine = await engine_manager.get_or_create(active_session)

    ctx = await engine.context_mgr.build(
        active_session.id, turn=1, mode="ask", scene_mode="office",
        client_tools=engine._session_client_tools(),
    )
    assert ctx.available_tools is None
