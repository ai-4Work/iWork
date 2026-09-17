"""Stage 9 · 子 agent 并发上限（§9.12.5②）。

超限要**拒绝**而不是排队：排队的子任务挂在父的在途清单里，父会一直等，
而队列深度不可观察——用户看到的现象只是"卡住"。所以断言的形状是
"第 N+1 个派发立刻拿到失败句柄"，不是"它稍后被处理了"。

名额的生命周期是**跨模块**的：`TaskToolHandler.execute` 取、子引擎终态时
在自己的 `_report_to_parent` 里还。下面四条分别钉住取、还、取完就炸的兜底，
以及在途清单的可观察性。
"""
import asyncio
import time
from uuid import uuid4

import pytest

from server.api.routes import session_state
from server.config import settings
from server.engine.query_loop import EngineManager, QueryLoopEngine
from server.engine.task_handler import TaskToolHandler
from server.llm.client import LLMChunk
from server.models.session import Session
from server.observability.event_bus import EventBus
from server.observability.stream import StreamSubscriber


def _wired_manager(session_repo, message_repo, fake_llm) -> EngineManager:
    """带事件总线的 manager。子的 message.complete 只有经 Stream 订阅者才会进
    _chunk_queue，而"子终态 → 回投结果"这条链正是靠它推进的。"""
    bus = EventBus()
    sub = StreamSubscriber()
    mgr = EngineManager(session_repo, message_repo, fake_llm, event_bus=bus)
    sub.set_engine_manager(mgr)
    bus.subscribe(sub.handle)
    return mgr


def _plugin_dir(tmp_path, *member_ids):
    (tmp_path / "agents").mkdir(exist_ok=True)
    for mid in member_ids:
        (tmp_path / "agents" / f"{mid}.md").write_text(f"你是 {mid}。", encoding="utf-8")
    return str(tmp_path)


MEMBERS = [
    {"id": "dev", "role": "expert", "name": {"zh": "开发"}},
    {"id": "qa", "role": "expert", "name": {"zh": "测试"}},
    {"id": "ops", "role": "expert", "name": {"zh": "运维"}},
]


async def _lead(engine_manager, session_repo, tmp_path, members):
    sid = uuid4()
    s = Session(id=sid, user_id="u1", mode="build", workspace="G:/ws", model="m",
                agent_path="/root", root_session_id=sid, client_tools=[])
    await session_repo.create(s)
    engine = await engine_manager.get_or_create(s)
    engine._team_members = members
    engine._team_plugin_path = _plugin_dir(tmp_path, *[m["id"] for m in members])
    engine._disable_task_tool = False
    return s, engine


async def _dispatch(handler, parent_engine, agent_path, prompt, members):
    return await handler.execute(
        parent_session_id=parent_engine.session.id,
        parent_user_id="u1",
        parent_agent_path="/root",
        agent_path=agent_path,
        prompt=prompt,
        team_plugin_path=parent_engine._team_plugin_path,
        team_members=members,
        parent_chunk_queue=asyncio.Queue(),
        mode="spawn",
        parent_context_mgr=parent_engine.context_mgr,
        parent_root_session_id=parent_engine.session.id,
    )


@pytest.mark.asyncio
async def test_dispatch_rejected_past_the_cap(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path, monkeypatch,
):
    """上限 2：头两个派出去，第 3 个必须**立刻**失败，而不是排队等名额。"""
    monkeypatch.setattr(settings, "max_concurrent_children", 2)
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    _parent, engine = await _lead(mgr, session_repo, tmp_path, MEMBERS)
    handler = TaskToolHandler(mgr, engine._plugin_loader)

    # 让子的 LLM 调用卡住不返回 —— 点名额度就一直被占着。
    # 用事件而不是"少给一条响应"：后者会被 FakeLLMClient 用默认响应兜底，子照样结束。
    release = asyncio.Event()
    original_stream = fake_llm.stream

    async def blocking_stream(messages, system, tools=None, tool_choice=None):
        await release.wait()
        async for c in original_stream(messages, system, tools, tool_choice):
            yield c

    fake_llm.stream = blocking_stream
    try:
        first, second = await asyncio.gather(
            _dispatch(handler, engine, "/root/dev", "A", MEMBERS[:2]),
            _dispatch(handler, engine, "/root/qa", "B", MEMBERS[:2]),
        )
        assert (first["success"], second["success"]) == (True, True)
        assert mgr._children_running == 2

        # 派一个**已存在**的成员：闸门排在成员查表**之后**，用一个不存在的路径
        # 会先撞上 "not found"，测不到上限这条分支。
        third = await asyncio.wait_for(
            _dispatch(handler, engine, "/root/dev", "C", MEMBERS[:2]), timeout=5,
        )
        assert third["success"] is False, "超限必须是拒绝，不能是等待"
        assert "上限" in third["error"]
        assert mgr._children_running == 2, "被拒的派发不该占名额"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_slot_is_returned_when_child_finishes(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path, monkeypatch,
):
    """子跑完 → 名额归位 → 下一次派发能成功。

    这条跨了 handler 与子引擎两处（取在 `task_handler`、还在 `_report_to_parent`），
    是"取/还不同文件"这个设计最容易漏的一环。
    """
    monkeypatch.setattr(settings, "max_concurrent_children", 1)
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, engine = await _lead(mgr, session_repo, tmp_path, MEMBERS)
    handler = TaskToolHandler(mgr, engine._plugin_loader)

    fake_llm.responses = [
        [LLMChunk(type="text", delta="第一个做完了"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="第二个做完了"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    first = await _dispatch(handler, engine, "/root/dev", "A", MEMBERS)
    assert first["success"] is True

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and mgr._children_running > 0:
        await asyncio.sleep(0.01)
    assert mgr._children_running == 0, "子终态没归还名额，名额泄漏"

    second = await _dispatch(handler, engine, "/root/qa", "B", MEMBERS)
    assert second["success"] is True
    assert second["task_id"] != first["task_id"]

    # 两次派发各投一条结果信封进父邮箱
    rows = await message_repo.list_pending_results(parent.id, "/root")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(rows) < 2:
        await asyncio.sleep(0.01)
        rows = await message_repo.list_pending_results(parent.id, "/root")
    assert len(rows) == 2, "两次派发各投一条结果"


@pytest.mark.asyncio
async def test_slot_does_not_leak_when_dispatch_raises(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path, monkeypatch,
):
    """取到名额之后、投递成功之前炸掉：名额必须当场收回。

    漏了这条兜底，一次失败就永久吃掉一个名额，几次之后再也派不出子任务。
    """
    monkeypatch.setattr(settings, "max_concurrent_children", 1)
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    _parent, engine = await _lead(mgr, session_repo, tmp_path, MEMBERS)
    handler = TaskToolHandler(mgr, engine._plugin_loader)

    async def _boom(self, *a, **kw):
        raise RuntimeError("投递时炸了")

    monkeypatch.setattr(QueryLoopEngine, "enqueue", _boom)

    with pytest.raises(RuntimeError):
        await _dispatch(handler, engine, "/root/dev", "A", MEMBERS)

    assert mgr._children_running == 0, "抛异常后名额没收回，泄漏了"


@pytest.mark.asyncio
async def test_state_endpoint_exposes_in_flight(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """/state 的在途清单：展示层要看到"在等谁"，且只拿扁平 id（§9.11.13）。"""
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, engine = await _lead(mgr, session_repo, tmp_path, MEMBERS)
    engine._in_flight["t-1"] = {"agent_path": "/root/dev", "dispatched_at": 1.0}

    class _EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class _StubDB:
        async def execute(self, *a, **kw):
            return _EmptyResult()

    state = await session_state(session_id=parent.id, engine_mgr=mgr, db=_StubDB())
    assert state["in_flight"] == [
        {"task_id": "t-1", "agent_path": "/root/dev",
         "agent_id": "dev", "dispatched_at": 1.0},
    ], state["in_flight"]
    assert "/" not in state["in_flight"][0]["agent_id"], "展示层不进路径"
