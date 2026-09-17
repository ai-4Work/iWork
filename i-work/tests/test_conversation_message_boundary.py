"""M2 · conversation_history 消息边界 + 历史读取。

验证：
  - ContextManager.set_active_message 盖印：append_* 新增行带 message_id/turn。
  - repo.list_rows 返回全字段元数据行，按 sequence 升序。
  - repo.truncate_after 删除 boundary 之后的行（供 M4 截断重跑）。
  - 引擎真实跑一条 ask 消息后，历史行归属该消息 id / turn。

（本环境无 HTTP TestClient，端点逻辑以 repo/纯函数级测试兜底；迁移以离线渲染校验。）
"""
import pytest
from types import SimpleNamespace
from uuid import uuid4

from server.api.routes import _group_history_rows
from server.storage.memory import InMemorySessionRepo, InMemoryMessageRepo
from server.engine.context import ContextManager
from server.models.session import Session
from server.models.message import MessageCreate
from server.engine.query_loop import EngineManager
from server.llm.client import LLMChunk


@pytest.mark.asyncio
async def test_append_rows_are_stamped_with_active_message():
    sid = uuid4()
    mid = uuid4()
    mr = InMemoryMessageRepo()
    ctx = ContextManager(mr)

    ctx.set_active_message(mid, 0)
    await ctx.append_text(sid, "user", "hello")
    await ctx.append_text(sid, "assistant", "hi there")
    ctx.set_active_message(mid, 1)
    await ctx.append_text(sid, "assistant", "more")

    rows = await mr.list_rows(sid)
    assert len(rows) == 3
    for r in rows:
        assert r["message_id"] == str(mid)
    assert rows[0]["turn"] == 0
    assert rows[1]["turn"] == 0
    assert rows[2]["turn"] == 1
    # sequence 单调
    seqs = [r["sequence"] for r in rows]
    assert seqs == sorted(seqs)


@pytest.mark.asyncio
async def test_truncate_after_deletes_only_after_boundary():
    sid = uuid4()
    mid = uuid4()
    mr = InMemoryMessageRepo()
    ctx = ContextManager(mr)

    ctx.set_active_message(mid, 0)
    await ctx.append_text(sid, "user", "boundary-row")      # seq 1
    await ctx.append_text(sid, "assistant", "partial reply")  # seq 2
    ctx.set_active_message(uuid4(), 0)
    await ctx.append_text(sid, "user", "next message")        # seq 3

    rows = await mr.list_rows(sid)
    boundary = next(r["sequence"] for r in rows if r["content"] == "boundary-row")

    await mr.truncate_after(sid, boundary)

    remaining = await mr.list_rows(sid)
    assert [r["content"] for r in remaining] == ["boundary-row"]


@pytest.mark.asyncio
async def test_engine_ask_run_tags_history_rows():
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()

    class OneShotLLM:
        async def stream(self, messages, system, tools=None, tool_choice=None):
            yield LLMChunk(type="text", delta="answered.")
            yield LLMChunk(type="end_turn", stop_reason="end_turn")

    mgr = EngineManager(session_repo, message_repo, OneShotLLM())
    session = Session(user_id="test-user", mode="ask", workspace="/tmp/test")
    await session_repo.create(session)
    engine = await mgr.get_or_create(session)
    msg = await engine.enqueue("test-user", MessageCreate(
        content="hi", scene_mode="office", workspace="/tmp/test",
        model="claude-sonnet-4-6", mode="ask",
    ))

    import asyncio
    for _ in range(100):
        await asyncio.sleep(0.01)
        if engine.state == "IDLE":
            break

    rows = await message_repo.list_rows(session.id)
    assert len(rows) >= 2  # user + assistant
    for r in rows:
        assert r["message_id"] == str(msg.id), r
    # 首行为 user，turn=0
    assert rows[0]["role"] == "user"
    assert rows[0]["turn"] == 0


def _row(seq, role, mid=None, content=None):
    return SimpleNamespace(
        sequence=seq, role=role, content=content,
        reasoning_content=None, tool_call_id=None, turn=0, message_id=mid,
    )


def test_group_history_rows_buckets_by_message_and_legacy_attach():
    m1 = str(uuid4())
    m2 = str(uuid4())
    rows = [
        _row(1, "user", m1, "hello"),
        _row(2, "assistant", None, "partial"),   # 老行：归属最近 m1
        _row(3, "assistant", m1, "reply"),
        _row(4, "user", m2, "second"),           # 新消息边界
        _row(5, "assistant", m2, "done"),
    ]
    buckets = _group_history_rows(rows)
    assert list(buckets.keys()) == [m1, m2]
    assert [r["content"] for r in buckets[m1]] == ["hello", "partial", "reply"]
    assert [r["content"] for r in buckets[m2]] == ["second", "done"]


def test_group_history_rows_skips_orphan_before_any_message():
    rows = [_row(1, "user", None, "orphan-prelude")]
    assert _group_history_rows(rows) == {}
