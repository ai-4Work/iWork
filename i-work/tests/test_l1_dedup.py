"""去重：候选召回边界、四种动作、落地语义（设计文档 L1-2.5 / L1-2.6）。"""
import json

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.dedup import ConflictResolver
from server.memory.retriever import TFIDFMemoryRetriever
from server.memory.store import apply_decisions
from server.memory.types import L1Memory
from server.storage.memory import InMemoryL1MemoryRepo


def _memory(mid, content, *, priority=80, version=1, mtype="episodic",
            agent_id="/root", timestamps=None):
    return L1Memory(
        id=mid, user_id="u1", content=content, type=mtype, priority=priority,
        agent_id=agent_id, version=version, timestamps=timestamps or [],
    )


def _retriever(*memories):
    return TFIDFMemoryRetriever(list(memories))


def _llm(payload) -> FakeLLMClient:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return FakeLLMClient(responses=[[LLMChunk(type="text", delta=text)]])


# ── 候选召回是算法，不是 LLM ────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_pool_stores_everything_without_calling_llm():
    llm = _llm("这段不该被读到")
    decisions = await ConflictResolver(llm).resolve(
        [_memory("m_new", "用户的时区是 UTC+8")], _retriever(),
    )
    assert [d.action for d in decisions] == ["store"]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_same_batch_memories_never_conflict_with_each_other():
    """边界二：同批新记忆互不冲突 —— 池里只有它们自己时不该问 LLM。"""
    llm = _llm("这段不该被读到")
    batch = [
        _memory("m_a", "用户的时区是 UTC+8"),
        _memory("m_b", "用户的时区是 UTC+8"),
    ]
    decisions = await ConflictResolver(llm).resolve(batch, _retriever(*batch))
    assert [d.action for d in decisions] == ["store", "store"]
    assert llm.calls == []


@pytest.mark.asyncio
async def test_unrelated_pool_memory_is_below_threshold():
    llm = _llm("这段不该被读到")
    pool = _memory("m_old", "咖啡因会让人睡不着觉")
    decisions = await ConflictResolver(llm, min_score=0.3).resolve(
        [_memory("m_new", "用户所在时区为东八区")], _retriever(pool),
    )
    assert [d.action for d in decisions] == ["store"]
    assert llm.calls == []


# ── 四种动作的解析 ─────────────────────────────────────────────

async def _resolve_against(payload, *, new, pool, min_score=0.15, top_k=5):
    llm = _llm(payload)
    decisions = await ConflictResolver(llm, top_k=top_k, min_score=min_score).resolve(
        new, _retriever(*pool),
    )
    return decisions, llm


@pytest.mark.asyncio
async def test_update_decision_pulls_merged_fields_from_llm():
    pool = [_memory("m_old", "用户的时区是 UTC+8", priority=80, version=3)]
    payload = [{
        "record_id": "m_new", "action": "update", "target_ids": ["m_old"],
        "merged_content": "用户的时区是 UTC+8（已确认）",
        "merged_priority": 95,
    }]
    decisions, llm = await _resolve_against(
        payload, new=[_memory("m_new", "用户的时区是 UTC+8")], pool=pool,
    )
    assert len(llm.calls) == 1
    d = decisions[0]
    assert d.action == "update" and d.supersedes == ["m_old"]
    assert d.content == "用户的时区是 UTC+8（已确认）"
    assert d.priority == 95


@pytest.mark.asyncio
async def test_invalid_target_degrades_to_store():
    pool = [_memory("m_old", "用户的时区是 UTC+8")]
    payload = [{"record_id": "m_new", "action": "merge", "target_ids": ["m_ghost"]}]
    decisions, _ = await _resolve_against(
        payload, new=[_memory("m_new", "用户的时区是 UTC+8")], pool=pool,
    )
    assert decisions[0].action == "store"


@pytest.mark.asyncio
async def test_unknown_action_degrades_to_store():
    pool = [_memory("m_old", "用户的时区是 UTC+8")]
    payload = [{"record_id": "m_new", "action": "delete", "target_ids": ["m_old"]}]
    decisions, _ = await _resolve_against(
        payload, new=[_memory("m_new", "用户的时区是 UTC+8")], pool=pool,
    )
    assert decisions[0].action == "store"


@pytest.mark.asyncio
async def test_dedup_parse_failure_degrades_to_store():
    pool = [_memory("m_old", "用户的时区是 UTC+8")]
    decisions, _ = await _resolve_against(
        "我不会判断这个。", new=[_memory("m_new", "用户的时区是 UTC+8")], pool=pool,
    )
    assert decisions[0].action == "store"


@pytest.mark.asyncio
async def test_merge_unions_timestamps_and_priority():
    pool = [
        _memory("m_old", "用户的时区是 UTC+8", priority=80,
                timestamps=["2026-08-18"]),
    ]
    payload = [{
        "record_id": "m_new", "action": "merge", "target_ids": ["m_old"],
        "merged_timestamps": ["2026-08-18"],
    }]
    decisions, _ = await _resolve_against(
        payload,
        new=[_memory("m_new", "用户的时区是 UTC+8", priority=90,
                     timestamps=["2026-08-20"])],
        pool=pool,
    )
    d = decisions[0]
    assert d.timestamps == ["2026-08-18", "2026-08-20"]
    assert d.priority == 90


# ── 落地语义 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_apply_decisions_versions_and_soft_deletes():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m_old", "旧事实", version=3)], [])

    new = _memory("m_new", "新事实")
    decisions, _ = await _resolve_against(
        [{"record_id": "m_new", "action": "merge", "target_ids": ["m_old"],
          "merged_content": "合并后的事实", "merged_timestamps": ["2026-08-20"]}],
        new=[new],
        pool=[_memory("m_old", "旧事实", version=3, timestamps=["2026-08-18"])],
    )
    stats = await apply_decisions(repo, decisions)
    assert stats["merge"] == 1

    rows, total = await repo.list_page("u1")
    assert total == 1                       # 旧行已软删，列表只出新行
    assert rows[0].id == "m_new"
    assert rows[0].version == 4             # 目标最大版本 + 1
    assert rows[0].content == "合并后的事实"
    assert rows[0].timestamps == ["2026-08-18", "2026-08-20"]

    old = await repo.get("m_old")
    assert old.retrievable is False         # 事实源保留，不进检索


@pytest.mark.asyncio
async def test_apply_decisions_skips_whole_batch_when_only_skip():
    repo = InMemoryL1MemoryRepo()
    decisions, _ = await _resolve_against(
        [{"record_id": "m_new", "action": "skip"}],
        new=[_memory("m_new", "已经在记忆里的旧事")],
        pool=[_memory("m_old", "已经在记忆里的旧事")],
    )
    stats = await apply_decisions(repo, decisions)
    assert stats["skip"] == 1
    assert await repo.list_page("u1") == ([], 0)


@pytest.mark.asyncio
async def test_apply_decisions_soft_delete_is_reversible_source_kept():
    """被取代的行留在库里（血缘不断），只是不进检索。"""
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m_old", "旧事实")], [])
    await repo.apply_batch([_memory("m_new", "新事实")], ["m_old"])

    assert (await repo.get("m_old")).retrievable is False
    assert [m.id for m in await repo.list_by_scope("u1", "/root")] == ["m_new"]
    assert len(await repo.list_by_scope("u1", "/root", retrievable_only=False)) == 2


@pytest.mark.asyncio
async def test_candidates_are_restricted_to_scope():
    """边界一：永不跨作用域 —— 别的 agent 的记忆根本不在候选池里。"""
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([
        _memory("m_mine", "用户的时区是 UTC+8", agent_id="/root"),
        _memory("m_theirs", "用户的时区是 UTC+8", agent_id="/root/m1"),
    ], [])

    mine = await repo.list_by_scope("u1", "/root")
    assert [m.id for m in mine] == ["m_mine"]
    # 候选池就是这一层的产出，所以 m_theirs 连被检索的机会都没有
    assert _retriever(*mine).search("用户的时区是 UTC+8", 5)[0][0].id == "m_mine"
