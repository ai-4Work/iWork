"""Stage 8 · 异步派发 + WAITING_CHILDREN（§9.11.8 / §9.11.12 #8 #9 #10 #11）。

这个特性的产出不是某个函数的返回值，而是**父那条消息的形状**：子回来后它必须在
同一个 message_id、同一条消息的 turn 序列上续跑，且全程只发一条 message.complete。
所以这里跑真实的父子三引擎，断言 chunk 序列与历史行，而不是断言中间变量。
"""
import asyncio
import time
from uuid import uuid4

import pytest

from server.engine.query_loop import EngineManager
from server.engine.task_handler import TaskToolHandler
from server.llm.client import LLMChunk
from server.models.message import MessageCreate, MessageStatus
from server.models.session import Session
from server.observability.event_bus import EventBus
from server.observability.stream import StreamSubscriber

CHILD_OUTPUT = "子任务完成"


def _wired_manager(session_repo, message_repo, fake_llm) -> EngineManager:
    bus = EventBus()
    sub = StreamSubscriber()
    mgr = EngineManager(session_repo, message_repo, fake_llm, event_bus=bus)
    sub.set_engine_manager(mgr)
    bus.subscribe(sub.handle)
    return mgr


def _plugin_dir(tmp_path, *member_ids):
    (tmp_path / "agents").mkdir()
    for mid in member_ids:
        (tmp_path / "agents" / f"{mid}.md").write_text(f"你是 {mid}。", encoding="utf-8")
    return str(tmp_path)


def _task_call(tc_id, path, prompt):
    return LLMChunk(type="tool_use", tool_name="task", tool_call_id=tc_id,
                    tool_input={"agent_path": path, "prompt": prompt, "mode": "spawn"})


async def _drain_until(chunks: asyncio.Queue, kind: str, timeout=15) -> list[dict]:
    """收 chunk 直到出现 kind（含）；顺带把之前的都收下来。"""
    seen: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(chunks.get(), timeout=deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
        seen.append(chunk)
        if chunk.get("type") == kind:
            return seen
    raise AssertionError(f"没等到 {kind}，只收到 {[c.get('type') for c in seen]}")


async def _lead(engine_manager, session_repo, tmp_path, members):
    """一个团队 lead 会话：跑在 /root，root 指向自身（§9.11.3）。"""
    sid = uuid4()
    s = Session(id=sid, user_id="u1", mode="build", workspace="G:/ws", model="m",
                agent_path="/root", root_session_id=sid, client_tools=[])
    await session_repo.create(s)
    engine = await engine_manager.get_or_create(s)
    engine._team_members = members
    engine._team_plugin_path = _plugin_dir(tmp_path, *[m["id"] for m in members])
    engine._disable_task_tool = False
    return s, engine


MEMBERS = [
    {"id": "dev", "role": "expert", "name": {"zh": "开发"}},
    {"id": "qa", "role": "expert", "name": {"zh": "测试"}},
]


# ═══════════════════════════════════════════════════════════════
# 验收：一轮派 2 子 → 挂起 → 批量注入 → 同一 message_id 续跑
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_agent_status_chunks_match_the_client_contract(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """`agent.status` 的字段方向与取值是**客户端的硬契约**：

    `agent_id` = lead 那一列、`to` = 成员的扁平 id（客户端靠 `a.id === agent_id`
    找列、再靠 `delegation.to === to` 找卡片收尾），status 只能取客户端穷举过的值。
    方向填反或塞个路径，卡片就永远停在"执行中"——服务端单测全绿也照样看不见。
    """
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    fake_llm.responses = [
        [LLMChunk(type="text", delta="派一个"), _task_call("t1", "/root/dev", "做 A"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta=CHILD_OUTPUT),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="收尾"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    await parent_engine.enqueue("u1", MessageCreate(
        content="派一个子任务", scene_mode="office",
        workspace="G:/ws", model="m", mode="build", agent_id="lead",
    ))
    chunks = await _drain_until(parent_engine._chunk_queue, "message.complete")

    statuses = [c for c in chunks if c["type"] == "agent.status"]
    assert statuses, f"没有 agent.status：{[c['type'] for c in chunks]}"
    for chunk in statuses:
        assert chunk["agent_id"] == "lead", "agent_id 必须是 lead 那一列"
        assert chunk["to"] == "dev", "to 必须是成员的扁平 id"
        assert "/" not in chunk["to"], "路径填进来客户端就查不到那张卡片"
        assert chunk["status"] in ("idle", "thinking", "running", "done", "waiting", "failed")

    assert [c["status"] for c in statuses] == ["running", "done"], \
        "派发时 running、子回来时 done"
    assert CHILD_OUTPUT in (statuses[-1]["output_preview"] or ""), \
        "收尾要带 output_preview，否则卡片收尾后是空的"


@pytest.mark.asyncio
async def test_suspend_then_resume_on_the_same_message(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS)

    fake_llm.responses = [
        # 父 turn 0：派两个子任务，然后收尾
        [
            LLMChunk(type="text", delta="我来分派"),
            _task_call("t1", "/root/dev", "做 A"),
            _task_call("t2", "/root/qa", "验 B"),
            LLMChunk(type="end_turn", stop_reason="end_turn"),
        ],
        # 子（两个子共用同一段脚本，谁先跑都一样）
        [LLMChunk(type="text", delta=CHILD_OUTPUT),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta=CHILD_OUTPUT),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        # 父 turn 1（子结果注入后）：收尾
        [LLMChunk(type="text", delta="都做完了"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    msg = await parent_engine.enqueue("u1", MessageCreate(
        content="帮我把这件事拆开做", scene_mode="office",
        workspace="G:/ws", model="m", mode="build",
    ))

    chunks = await _drain_until(parent_engine._chunk_queue, "message.complete")

    kinds = [c["type"] for c in chunks]
    # 1. 挂起中推了在途清单
    waiting = [c for c in chunks if c["type"] == "task.waiting"]
    assert waiting, f"没有 task.waiting：{kinds}"
    assert {i["agent_path"] for i in waiting[0]["in_flight"]} == {"/root/dev", "/root/qa"}
    assert {i["agent_id"] for i in waiting[0]["in_flight"]} == {"dev", "qa"}, "展示层用扁平 id"
    # 2. 挂起期间**不**发 message.complete：等待 chunk 必须排在它前面
    assert kinds.index("task.waiting") < kinds.index("message.complete")
    # 3. 全程恰好一条 message.complete
    assert kinds.count("message.complete") == 1

    # 4. state 落过库（可观察性）；终态回到正常
    stored = await message_repo.get(msg.id)
    assert stored.status == MessageStatus.COMPLETED
    assert stored.id == msg.id, "必须还是同一条消息，不能新建 Message 行"
    assert not parent_engine._in_flight, "子都回来了，在途清单应清空"

    # 5. 两个子的产出都以 user 行注入，且带结构化前缀
    rows = await parent_engine.context_mgr.list_rows(parent.id)
    injected = [r for r in rows if r.get("role") == "user" and "[子 agent" in (r.get("content") or "")]
    assert len(injected) == 2, [r.get("content") for r in rows]
    assert all(r.get("message_id") == str(msg.id) for r in injected), \
        "注入行属于父这条消息（同一 message_id，不新起消息）"
    assert all(CHILD_OUTPUT in r["content"] for r in injected)
    # 结构化前缀：没有它，注入行与用户手敲的文字在历史里不可区分
    assert all("task_id=" in r["content"] for r in injected)
    assert {r["content"].split(" 的产出")[0] for r in injected} == {
        "[子 agent /root/dev", "[子 agent /root/qa",
    }

    # 6. turn 在同一序列上继续（子结果那一轮之后还有父的收尾轮）
    assert stored.turn_count >= 2

    # 7. 结果信封已从邮箱取走
    assert await message_repo.list_pending_results(parent.id, "/root") == []


@pytest.mark.asyncio
async def test_children_chunks_reach_the_parent_stream_but_not_terminal_ones(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """展示中继（设计决定 D）：子的 agent.text 到父流；子的 message.complete 绝不能到，
    否则父的 NDJSON 会被误判结束（routes.py 的 chunk_generator 以此 break）。"""
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    fake_llm.responses = [
        [LLMChunk(type="text", delta="派一个"), _task_call("t1", "/root/dev", "做 A"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta=CHILD_OUTPUT),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="收尾"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    await parent_engine.enqueue("u1", MessageCreate(
        content="派一个子任务", scene_mode="office",
        workspace="G:/ws", model="m", mode="build",
    ))

    chunks = await _drain_until(parent_engine._chunk_queue, "message.complete")
    child_text = [c for c in chunks if c["type"] == "agent.text" and c.get("agent_id") == "dev"]
    assert child_text, "子的流式文本应中继到父流（否则前端在子任务期间一片空白）"
    assert any(CHILD_OUTPUT in c.get("delta", "") for c in child_text)
    # 子的 message.complete 没有混进来：父流里那条是父自己的
    assert len([c for c in chunks if c["type"] == "message.complete"]) == 1


@pytest.mark.asyncio
async def test_child_error_is_injected_and_parent_still_finishes(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """子报错不能让父永远挂着：注入 error 结果，父照常终结。"""
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    class _Boom(Exception):
        pass

    calls = {"n": 0}
    original_stream = fake_llm.stream

    async def flaky_stream(messages, system, tools=None, tool_choice=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise _Boom("子引擎炸了")
        async for c in original_stream(messages, system, tools, tool_choice):
            yield c

    fake_llm.stream = flaky_stream
    fake_llm.responses = [
        [LLMChunk(type="text", delta="派一个"), _task_call("t1", "/root/dev", "做 A"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="正常"), LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    await parent_engine.enqueue("u1", MessageCreate(
        content="派个子任务", scene_mode="office",
        workspace="G:/ws", model="m", mode="build",
    ))

    chunks = await _drain_until(parent_engine._chunk_queue, "message.complete")
    rows = await parent_engine.context_mgr.list_rows(parent.id)
    injected = [r["content"] for r in rows
                if r.get("role") == "user" and "[子 agent" in (r.get("content") or "")]
    assert len(injected) == 1, rows
    assert "子任务未正常完成" in injected[0]
    assert not parent_engine._in_flight


# ═══════════════════════════════════════════════════════════════
# 派发句柄 / 取消 / 可观察性
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dispatch_returns_self_describing_handle(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """句柄必须自描述：模型很容易把 dispatched 读成失败然后重派一遍。"""
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    _parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    fake_llm.responses = [[LLMChunk(type="text", delta="子产出"),
                           LLMChunk(type="end_turn", stop_reason="end_turn")]]

    handle = await TaskToolHandler(mgr, parent_engine._plugin_loader).execute(
        parent_session_id=parent_engine.session.id,
        parent_user_id="u1",
        parent_agent_path="/root",
        agent_path="/root/dev",
        prompt="做 A",
        team_plugin_path=parent_engine._team_plugin_path,
        team_members=MEMBERS[:1],
        parent_chunk_queue=asyncio.Queue(),
        mode="spawn",
        parent_context_mgr=parent_engine.context_mgr,
        parent_root_session_id=parent_engine.session.id,
    )

    assert handle["success"] is True
    assert handle["status"] == "dispatched"
    assert handle["task_id"]
    assert handle["agent_path"] == "/root/dev"
    assert "勿" in handle["note"] or "不要" in handle["note"]
    assert "dispatched" in handle["note"]
    # 新语义的钉子：句柄必须明说"可以继续"，否则模型会空等（这句是喂给模型的 prompt）
    assert "继续" in handle["note"]


@pytest.mark.asyncio
async def test_cancel_wakes_a_suspended_parent(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """挂起中的父不在任何软取消检查点上，只有 request_cancel 同时 set
    _children_event 才唤得醒它——否则 POST /cancel 会一直没反应。"""
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    _parent, parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    # 手写一个在途子任务：不真派子，只验证"唤醒 + 收口"这条路径
    parent_engine._in_flight["fake-task"] = {
        "agent_path": "/root/dev", "dispatched_at": time.monotonic(),
    }
    msg = await parent_engine.enqueue("u1", MessageCreate(
        content="等一下", scene_mode="office", workspace="G:/ws", model="m", mode="build",
    ))
    await asyncio.sleep(0.05)
    assert parent_engine.state == "WAITING_CHILDREN", parent_engine.state

    parent_engine.request_cancel()
    chunks = await _drain_until(parent_engine._chunk_queue, "message.error")
    assert any(c.get("code") == "cancelled" for c in chunks), chunks
    assert parent_engine.state != "WAITING_CHILDREN", "取消后不能停在 WAITING_CHILDREN"
    assert (await message_repo.get(msg.id)).status == MessageStatus.CANCELLED


@pytest.mark.asyncio
async def test_child_session_inherits_parent_tool_surface(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """子的客户端工具面 / workspace / model 必须继承父。

    工具是**同一个客户端**执行的，与"哪个 agent 发请求"无关：不继承则子的上下文里
    只有 server tools，写文件、跑命令连工具都看不见，只能把整篇产物当纯文本吐回来；
    空 workspace 还会让写操作被判越界、降级成 needs_approval。
    """
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    sid = uuid4()
    tools = [{"name": "bash", "description": "跑命令", "input_schema": {"type": "object"}}]
    lead = Session(id=sid, user_id="u1", mode="build", workspace="G:/inherited",
                   model="m-lead", agent_path="/root", root_session_id=sid,
                   client_tools=tools)
    await session_repo.create(lead)
    engine = await mgr.get_or_create(lead)
    engine._team_members = MEMBERS[:1]
    engine._team_plugin_path = _plugin_dir(tmp_path, "dev")
    engine._disable_task_tool = False

    fake_llm.responses = [
        [LLMChunk(type="text", delta="派一个"), _task_call("t1", "/root/dev", "做 A"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta=CHILD_OUTPUT),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
        [LLMChunk(type="text", delta="收尾"),
         LLMChunk(type="end_turn", stop_reason="end_turn")],
    ]

    await engine.enqueue("u1", MessageCreate(
        content="派一个子任务", scene_mode="office",
        workspace="G:/inherited", model="m-lead", mode="build",
    ))
    await _drain_until(engine._chunk_queue, "message.complete")

    child = await session_repo.get_by_path(sid, "/root/dev")
    assert child is not None, "子会话没建出来"
    assert [t.name for t in child.client_tools] == ["bash"], \
        "子的客户端工具面必须继承父——否则子看不见 write_file/bash"
    assert child.workspace == "G:/inherited", "空 workspace 会让写操作被判越界"
    assert child.model == "m-lead", "空 model 会落到硬编码兜底模型"


@pytest.mark.asyncio
async def test_relayed_child_chunk_carries_child_session_id(
    engine_manager, session_repo, message_repo, fake_llm, tmp_path,
):
    """中继出的子 chunk 必须同时自报扁平 agent_id 与**子会话 id**。

    没有 session_id，客户端只能拿 lead 会话回投子的工具结果 → 服务端匹配不到
    invocation → 结果被当重复吞掉 → 子干等满 330s。只钉寻址契约，不跑真实工具往返
    （否则要等超时）。
    """
    mgr = _wired_manager(session_repo, message_repo, fake_llm)
    parent, _parent_engine = await _lead(mgr, session_repo, tmp_path, MEMBERS[:1])

    child = Session(id=uuid4(), user_id="u1", mode="build", scene_mode="office",
                    workspace="G:/ws", model="m", parent_id=parent.id,
                    agent_path="/root/dev", root_session_id=parent.id, client_tools=[])
    await session_repo.create(child)
    child_engine = await mgr.get_or_create(child)
    relayed_q: asyncio.Queue = asyncio.Queue()
    child_engine._parent_chunk_queue = relayed_q

    await child_engine._tee_to_parent({
        "type": "client.tool_request", "request_id": "r1",
        "tool_name": "bash", "input": {"command": "echo hi"},
    })
    relay = relayed_q.get_nowait()
    assert relay["agent_id"] == "dev", "agent_id 必须是扁平成员 id，不是路径"
    assert relay["session_id"] == str(child.id), \
        "缺 session_id 客户端就只会打到 lead，子的结果会被吞掉"

    # 终态绝不中继：父的 NDJSON 以此 break，转发一条等于提前关掉父的流
    await child_engine._tee_to_parent({"type": "message.complete"})
    assert relayed_q.empty(), "message.complete 不能进父流"

    # reconcile 两类必须放行：子等结果时唯一能解除死等的路径
    await child_engine._tee_to_parent({"type": "client.tool_reconcile", "request_id": "r1"})
    await child_engine._tee_to_parent({"type": "tool.reconcile_needs_confirm", "request_id": "r1"})
    assert [relayed_q.get_nowait()["type"] for _ in range(2)] == [
        "client.tool_reconcile", "tool.reconcile_needs_confirm",
    ]


@pytest.mark.asyncio
async def test_waiting_engine_is_not_evictable(engine_manager, session_repo):
    """驱逐会 cancel run()，把父那条消息永久留在 processing。"""
    s = Session(user_id="u1", mode="build", workspace="G:/ws", model="m")
    await session_repo.create(s)
    engine = await engine_manager.get_or_create(s)

    assert engine.is_evictable()
    engine.state = "WAITING_CHILDREN"
    assert not engine.is_evictable()
    engine.state = "IDLE"
    engine._in_flight["t"] = {"agent_path": "/root/dev", "dispatched_at": 0.0}
    assert not engine.is_evictable()


@pytest.mark.asyncio
async def test_reprocess_is_busy_while_waiting(engine_manager, session_repo, message_repo):
    """重跑要截断历史——绝不能把正等着子的父的历史挖掉。"""
    s = Session(user_id="u1", mode="build", workspace="G:/ws", model="m")
    await session_repo.create(s)
    engine = await engine_manager.get_or_create(s)
    msg = await engine.enqueue("u1", MessageCreate(
        content="x", scene_mode="office", workspace="G:/ws", model="m", mode="build",
    ))
    engine.state = "WAITING_CHILDREN"
    result = await engine.reprocess(msg.id, "regenerate")
    assert result["status"] == "busy"
