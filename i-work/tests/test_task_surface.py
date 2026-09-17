"""Stage 6 · task 工具面改成 AgentPath 寻址（§9.11.12 #5 / #7 / #9）。

两件事必须同时成立，否则 LLM 会从 XML 抄一个扁平 id 去填路径型 enum 而被 schema 拒：
工具定义与 `<available_agents>` 名单。子结果提取的收尾语义（§9.11.9）在 Stage 8
搬到子引擎自己的终态回调里，见 `test_async_dispatch.py`。
"""
import asyncio
import time
from uuid import uuid4

import pytest

from server.engine.query_loop import EngineManager
from server.engine.task_handler import TaskToolHandler
from server.llm.client import LLMChunk
from server.models.mail import NO_OUTPUT_PLACEHOLDER, agent_path_of
from server.models.session import Session
from server.observability.event_bus import EventBus
from server.observability.stream import StreamSubscriber


async def _wait_results(repo, session_id, path, n, timeout=10):
    """等 n 条结果信封落进邮箱（子在自己的终态回调里投）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await repo.list_pending_results(session_id, path)
        if len(rows) >= n:
            return rows
        await asyncio.sleep(0.01)
    raise AssertionError(f"只等到 {len(rows)}/{n} 条结果信封")


def _wired_manager(session_repo, message_repo, fake_llm) -> EngineManager:
    """带事件总线的 manager：子的 message.complete 只有经 Stream 订阅者才会进 _chunk_queue，
    而派发方正是靠这个 chunk 才知道子回合结束。"""
    bus = EventBus()
    sub = StreamSubscriber()
    mgr = EngineManager(session_repo, message_repo, fake_llm, event_bus=bus)
    sub.set_engine_manager(mgr)
    bus.subscribe(sub.handle)
    return mgr

TEAM_MEMBERS = [
    {"id": "lead", "role": "lead", "name": {"zh": "队长"}},
    {"id": "dev", "role": "expert", "name": {"zh": "开发"},
     "profession": {"zh": "写代码"}},
    {"id": "qa", "role": "expert", "name": {"zh": "测试"},
     "profession": {"zh": "找 bug"}},
]


async def _team_engine(engine_manager, session_repo):
    s = Session(user_id="u1", mode="build", workspace="/tmp/ws", model="m")
    await session_repo.create(s)
    engine = await engine_manager.get_or_create(s)
    engine._team_members = TEAM_MEMBERS
    engine._team_plugin_path = ""
    engine._disable_task_tool = False
    return engine


# ═══════════════════════════════════════════════════════════════
# 工具定义：enum 是路径，mode 必填
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_task_tool_enum_is_agent_paths(engine_manager, session_repo):
    engine = await _team_engine(engine_manager, session_repo)
    tool_def = engine._build_task_tool_definition()

    props = tool_def["input_schema"]["properties"]
    # lead 不进名单（自己不能派自己）
    assert props["agent_path"]["enum"] == [
        agent_path_of("dev"), agent_path_of("qa"),
    ] == ["/root/dev", "/root/qa"]
    assert props["mode"]["enum"] == ["spawn", "followup"]
    assert tool_def["input_schema"]["required"] == ["agent_path", "prompt", "mode"]


@pytest.mark.asyncio
async def test_flat_agent_name_is_no_longer_a_parameter(engine_manager, session_repo):
    """扁平 id 从工具面消失——它是路径型 enum 的反面，留着会让 LLM 抄错。"""
    engine = await _team_engine(engine_manager, session_repo)
    props = engine._build_task_tool_definition()["input_schema"]["properties"]

    assert "agent_name" not in props
    assert "agent_path" in props


@pytest.mark.asyncio
async def test_available_agents_xml_lists_paths_not_flat_ids(engine_manager, session_repo):
    """名单与 enum 必须同格式，否则 LLM 从 XML 抄来的值填不进 enum。"""
    engine = await _team_engine(engine_manager, session_repo)
    xml = engine._build_team_members_xml()

    assert "<name>/root/dev</name>" in xml
    assert "<name>/root/qa</name>" in xml
    assert "<name>dev</name>" not in xml
    assert "/root/lead" not in xml, "lead 不进名单"


@pytest.mark.asyncio
async def test_no_task_tool_when_disabled(engine_manager, session_repo):
    """子 agent 不能再派 task（循环防护），关掉时连定义都不给。"""
    engine = await _team_engine(engine_manager, session_repo)
    engine._disable_task_tool = True
    assert engine._build_task_tool_definition() is None


# ═══════════════════════════════════════════════════════════════
# 子结果提取：以工具调用收尾的回合不能返回空串（§9.11.9）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_last_assistant_text_skips_the_empty_tail(engine_manager, session_repo):
    """末行为空不是"没答案"——最后一段**非空**助手文本才是本回合的产出。"""
    engine = await _team_engine(engine_manager, session_repo)
    msg_id = uuid4()
    engine.context_mgr.set_active_message(msg_id, 0)
    await engine.context_mgr.append_text(engine.session.id, "user", "问")
    await engine.context_mgr.append_text(engine.session.id, "assistant", "分析完成，结论是 A")
    await engine.context_mgr.append_text(engine.session.id, "assistant", "")

    assert await engine._last_assistant_text(msg_id) == "分析完成，结论是 A"


@pytest.mark.asyncio
async def test_last_assistant_text_is_scoped_to_this_message(engine_manager, session_repo):
    """上一条消息的回复不能被当成本条的产出。"""
    engine = await _team_engine(engine_manager, session_repo)
    old, new = uuid4(), uuid4()
    engine.context_mgr.set_active_message(old, 0)
    await engine.context_mgr.append_text(engine.session.id, "assistant", "旧消息的结论")
    engine.context_mgr.set_active_message(new, 0)
    await engine.context_mgr.append_text(engine.session.id, "user", "新问题")

    assert await engine._last_assistant_text(new) == ""


def test_placeholder_is_not_an_empty_string():
    """空串会让父把"没结果"读成"成功但空"（§9.11.9）。"""
    assert NO_OUTPUT_PLACEHOLDER.strip() != ""


# ═══════════════════════════════════════════════════════════════
# 端到端：按路径派发 → 子会话带寻址列 → 结果回父
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_spawn_creates_addressed_child_then_reports_back(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """一次真实派发：寻址列落库、fork 用 .md 默认档、产出以结果信封回父。"""
    engine_manager = _wired_manager(session_repo, message_repo, fake_llm)
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "dev.md").write_text(
        "---\nname: 开发\ndescription: 写代码\nfork_turns: all\n---\n你是开发。",
        encoding="utf-8",
    )
    team_members = [{"id": "dev", "role": "expert", "name": {"zh": "开发"}}]

    parent_id = uuid4()
    parent = Session(
        id=parent_id, user_id="u1", mode="build", workspace="/tmp/ws", model="m",
        agent_path="/root", root_session_id=parent_id,
    )
    await session_repo.create(parent)
    parent_engine = await engine_manager.get_or_create(parent)
    parent_engine._team_members = team_members
    parent_engine._team_plugin_path = str(tmp_path)

    parent_engine.context_mgr.set_active_message(uuid4(), 0)
    await parent_engine.context_mgr.append_text(parent_id, "user", "父的问题")
    await parent_engine.context_mgr.append_text(parent_id, "assistant", "父的结论")

    fake_llm.responses = [[
        LLMChunk(type="text", delta="子任务产出"),
        LLMChunk(type="end_turn", stop_reason="end_turn"),
    ]]

    # 派发本身不再阻塞：句柄立刻回来，产出走邮箱
    result = await asyncio.wait_for(
        TaskToolHandler(engine_manager, parent_engine._plugin_loader).execute(
            parent_session_id=parent_id, parent_user_id="u1",
            parent_agent_path="/root",
            agent_path="/root/dev", prompt="帮我做这个",
            team_plugin_path=str(tmp_path), team_members=team_members,
            parent_chunk_queue=asyncio.Queue(),
            mode="spawn", fork_turns="",
            parent_context_mgr=parent_engine.context_mgr,
            parent_root_session_id=parent.root_session_id,
        ),
        timeout=15,
    )

    assert (result["success"], result["status"]) == (True, "dispatched")
    assert result["task_id"]

    # 子会话带寻址列；root 指向团队会话而非父自身之外的东西
    child = await session_repo.get_by_path(parent_id, "/root/dev")
    assert child is not None
    assert (child.agent_path, child.root_session_id) == ("/root/dev", parent_id)
    assert child.parent_id == parent_id

    # fork_turns 留空 → 走 .md frontmatter 的 all 档
    child_rows = await parent_engine.context_mgr.list_rows(child.id)
    assert [r["content"] for r in child_rows][:2] == ["父的问题", "父的结论"]
    assert all(r.get("message_id") is None for r in child_rows[:2]), "fork 段不盖印"

    # 子跑完后把产出投进父的邮箱（§9.11.7 结果方向）
    results = await _wait_results(message_repo, parent_id, "/root", 1)
    assert results[0].cid == result["task_id"], "cid 把 result 对回它那条 task"
    assert "子任务产出" in results[0].content


@pytest.mark.asyncio
async def test_followup_reuses_the_same_child_session(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """第二次派同一路径必须复用——所以 spawn 在路径已存在时退化为复用（唯一索引）。"""
    engine_manager = _wired_manager(session_repo, message_repo, fake_llm)
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "dev.md").write_text("你是开发。", encoding="utf-8")
    team_members = [{"id": "dev", "role": "expert", "name": {"zh": "开发"}}]

    parent_id = uuid4()
    parent = Session(id=parent_id, user_id="u1", mode="build", workspace="/tmp/ws",
                     model="m", agent_path="/root", root_session_id=parent_id)
    await session_repo.create(parent)
    parent_engine = await engine_manager.get_or_create(parent)
    parent_engine._team_members = team_members
    parent_engine._team_plugin_path = str(tmp_path)

    handler = TaskToolHandler(engine_manager, parent_engine._plugin_loader)
    kwargs = dict(
        parent_session_id=parent_id, parent_user_id="u1", parent_agent_path="/root",
        agent_path="/root/dev", team_plugin_path=str(tmp_path),
        team_members=team_members, parent_chunk_queue=asyncio.Queue(),
        parent_context_mgr=parent_engine.context_mgr,
        parent_root_session_id=parent_id,
    )

    fake_llm.responses = [
        [LLMChunk(type="text", delta="第一次"), LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="第二次"), LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    first = await handler.execute(prompt="第一问", mode="spawn", **kwargs)
    second = await handler.execute(prompt="第二问", mode="followup", **kwargs)
    assert first["task_id"] != second["task_id"], "每次派发新 mint task_id，否则第二次被幂等吞掉"

    # 两次派发各投一条结果，且都落在同一个子会话上
    results = await _wait_results(message_repo, parent_id, "/root", 2)
    assert {r.cid for r in results} == {first["task_id"], second["task_id"]}

    children = await session_repo.list_by_parent(parent_id)
    assert len(children) == 1, "同一路径只有一个子会话——spawn 在路径已存在时退化为复用"
    assert children[0].agent_path == "/root/dev"


