"""L1 调度：四种触发、冷启动跳过、级联、失败不推进游标（设计文档 L1-1.2 / L1-2.8）。"""
import json

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.dedup import ConflictResolver
from server.memory.extractor import MemoryExtractor
from server.memory.l0 import L0Reader
from server.memory.scheduler import L1Scheduler
from server.models.session import Session, SessionStatus
from server.storage.memory import (
    InMemoryL1MemoryRepo, InMemoryMessageRepo, InMemorySessionRepo,
)


def _extraction_json(content: str = "用户的时区是 UTC+8", scene: str = "我在和用户调时区"):
    return json.dumps([{
        "scene_name": scene,
        "memories": [{
            "content": content, "type": "episodic", "priority": 80,
            "source_message_ids": [], "metadata": {},
        }],
    }], ensure_ascii=False)


def _reply(payload: str) -> list[LLMChunk]:
    return [LLMChunk(type="text", delta=payload)]


def _build(*, batch_size=10, turn_threshold=5, idle_minutes=10, responses=None,
           llm=None, dedup_min_score=0.15):
    session_repo = InMemorySessionRepo()
    message_repo = InMemoryMessageRepo()
    memory_repo = InMemoryL1MemoryRepo()
    llm = llm or FakeLLMClient(responses=responses or [])
    reader = L0Reader(message_repo, batch_size=batch_size, background_size=5)
    scheduler = L1Scheduler(
        session_repo=session_repo,
        memory_repo=memory_repo,
        reader=reader,
        extractor=MemoryExtractor(llm),
        resolver=ConflictResolver(llm, min_score=dedup_min_score),
        turn_threshold=turn_threshold,
        idle_minutes=idle_minutes,
        batch_size=batch_size,
    )
    return scheduler, session_repo, message_repo, memory_repo, llm


async def _make_session(session_repo, *, status=SessionStatus.ACTIVE, **kw):
    s = Session(user_id="u1", mode="ask", workspace="/tmp/x", model="m", **kw)
    s.status = status
    await session_repo.create(s)
    return s


async def _say(message_repo, session, *contents):
    """交替写 user/assistant 历史行。传入的每条都当 user 消息，前面补一句助手回复。"""
    for text in contents:
        await message_repo.append_message(session.id, {"role": "user", "content": text})
        await message_repo.append_message(session.id, {"role": "assistant", "content": f"好，{text}"})


async def _only_user(message_repo, session, count):
    for i in range(count):
        await message_repo.append_message(
            session.id, {"role": "user", "content": f"第 {i} 个问题，关于时区设置"},
        )


@pytest.mark.asyncio
async def test_threshold_fires_at_five_user_messages():
    scheduler, session_repo, message_repo, memory_repo, llm = _build(
        responses=[_reply(_extraction_json())],
    )
    session = await _make_session(session_repo)
    await _only_user(message_repo, session, 5)

    assert await scheduler.sweep() == 1
    rows, total = await memory_repo.list_page("u1")
    assert total == 1 and rows[0].content == "用户的时区是 UTC+8"
    cp = await memory_repo.get_checkpoint(str(session.id))
    assert cp.last_cursor == 5           # 本批全部消化，游标推到最大 sequence
    assert cp.last_scene_name == "我在和用户调时区"


@pytest.mark.asyncio
async def test_threshold_does_not_fire_at_four():
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        responses=[_reply(_extraction_json())],
    )
    session = await _make_session(session_repo)
    await _only_user(message_repo, session, 4)

    assert await scheduler.sweep() == 0
    _, total = await memory_repo.list_page("u1")
    assert total == 0


@pytest.mark.asyncio
async def test_idle_fallback_fires_below_threshold():
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        turn_threshold=100, idle_minutes=10,
        responses=[_reply(_extraction_json())],
    )
    session = await _make_session(session_repo)
    # 先落一个冷启动 checkpoint，再把"上次抽取"挪到 20 分钟前
    await scheduler.sweep()
    cp = await memory_repo.get_checkpoint(str(session.id))
    from datetime import datetime, timedelta, timezone
    cp.last_extracted_at = datetime.now(timezone.utc) - timedelta(minutes=20)

    await _only_user(message_repo, session, 1)
    assert await scheduler.sweep() == 1


@pytest.mark.asyncio
async def test_archived_session_flushes_pending():
    """收官时把尾巴一次性抽掉：会话归档后不再活跃，靠游标表被重新枚举到。"""
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        turn_threshold=100, responses=[_reply(_extraction_json())],
    )
    session = await _make_session(session_repo)
    await _only_user(message_repo, session, 1)
    assert await scheduler.sweep() == 0      # 冷启动建游标，未达阈值不抽

    session.status = SessionStatus.ARCHIVED
    await _only_user(message_repo, session, 2)
    assert await scheduler.sweep() == 1

    _, total = await memory_repo.list_page("u1")
    assert total == 1


@pytest.mark.asyncio
async def test_archived_session_without_checkpoint_is_skipped():
    """非活跃会话只有在游标表里出现过才会被扫（否则会把所有历史归档会话全量重抽）。"""
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        responses=[_reply(_extraction_json())],
    )
    await _make_session(session_repo, status=SessionStatus.ARCHIVED)

    assert await scheduler.sweep() == 0
    assert await memory_repo.list_checkpoints() == []


@pytest.mark.asyncio
async def test_cold_start_skips_legacy_backlog():
    """开启 L1 前就存在的存量：积压超过一整批 → 游标直接推到末尾，不重抽。"""
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        batch_size=10, responses=[_reply(_extraction_json())],
    )
    session = await _make_session(session_repo)
    await _only_user(message_repo, session, 25)

    assert await scheduler.sweep() == 0
    _, total = await memory_repo.list_page("u1")
    assert total == 0
    cp = await memory_repo.get_checkpoint(str(session.id))
    assert cp.last_cursor == 25


@pytest.mark.asyncio
async def test_cascade_extracts_remaining_backlog():
    """抽完一轮后仍有一整批以上积压 → 立即续抽（不再等下一跳）。"""
    scheduler, session_repo, message_repo, memory_repo, llm = _build(
        batch_size=2, turn_threshold=3, dedup_min_score=0.99,
        responses=[_reply(_extraction_json(c)) for c in ("a 条", "b 条", "c 条", "d 条")],
    )
    session = await _make_session(session_repo)
    # 先建游标（此时积压 2 条未达阈值，不触发），避免走冷启动跳过存量那条路
    await _only_user(message_repo, session, 2)
    assert await scheduler.sweep() == 0

    await _only_user(message_repo, session, 6)
    assert await scheduler.sweep() == 1   # 命中一次即算 1（级联续抽不再计数）
    assert len(llm.calls) == 4            # 8 条按每批 2 条 → 4 次抽取
    cp = await memory_repo.get_checkpoint(str(session.id))
    assert cp.last_cursor == 8


@pytest.mark.asyncio
async def test_parse_failure_does_not_advance_cursor():
    scheduler, session_repo, message_repo, memory_repo, _ = _build(
        responses=[_reply("抱歉，我不知道该怎么抽取。")],
    )
    session = await _make_session(session_repo)
    await _only_user(message_repo, session, 6)

    assert await scheduler.sweep() == 0
    cp = await memory_repo.get_checkpoint(str(session.id))
    assert cp.last_cursor == 0            # 失败不推进，下一轮重试
    _, total = await memory_repo.list_page("u1")
    assert total == 0


@pytest.mark.asyncio
async def test_scope_isolation_by_agent_path():
    """每个 agent_path 有自己的会话与游标，记忆不跨作用域。"""
    scheduler, session_repo, message_repo, memory_repo, llm = _build(
        responses=[_reply(_extraction_json("顶层的事实", "顶层场景"))],
    )
    parent = await _make_session(session_repo, agent_path="/root")
    await _only_user(message_repo, parent, 5)
    assert await scheduler.sweep() == 1

    rows, _ = await memory_repo.list_page("u1")
    assert rows[0].agent_id == "/root"
    _, scoped = await memory_repo.list_page("u1", agent_id="/root/m1")
    assert scoped == 0
