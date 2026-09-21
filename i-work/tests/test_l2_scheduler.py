"""L2 调度：三种触发、游标推进、失败不推进、每批上限、作用域隔离（设计文档 L2-1.2）。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.l2.consolidator import MemoryConsolidator
from server.memory.l2.reader import L2Reader
from server.memory.l2.scheduler import L2Scheduler
from server.memory.l2.types import L2Checkpoint
from server.memory.types import L1Memory
from server.storage.memory import InMemoryL1MemoryRepo, InMemoryL2SceneRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _actions_json(name="技术研究-Rust学习", action="update"):
    return json.dumps({"actions": [{
        "action": action, "name": name, "summary": "摘要", "content": "正文",
    }]}, ensure_ascii=False)


def _llm(*payloads) -> FakeLLMClient:
    return FakeLLMClient(
        responses=[[LLMChunk(type="text", delta=p)] for p in payloads],
    )


def _build(*, llm=None, batch_memories=20, min_interval_seconds=900,
           max_interval_seconds=3600, cascade_delay_seconds=10):
    memory_repo = InMemoryL1MemoryRepo()
    scene_repo = InMemoryL2SceneRepo()
    llm = llm or _llm(_actions_json())
    scheduler = L2Scheduler(
        memory_repo=memory_repo,
        scene_repo=scene_repo,
        reader=L2Reader(memory_repo, scene_repo),
        consolidator=MemoryConsolidator(llm, max_scenes=15),
        batch_memories=batch_memories,
        cascade_delay_seconds=cascade_delay_seconds,
        min_interval_seconds=min_interval_seconds,
        max_interval_seconds=max_interval_seconds,
    )
    return scheduler, memory_repo, scene_repo, llm


async def _seed_l1(repo, *ids, agent_id="/root", age_seconds=60, user_id="u1"):
    """落一批 L1 记忆，updated_at 挪到 age_seconds 之前（逐条递增，游标才分得开）。"""
    base = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    rows = [
        L1Memory(id=i, user_id=user_id, content=f"记忆{i}", agent_id=agent_id,
                 updated_at=base + timedelta(milliseconds=n))
        for n, i in enumerate(ids)
    ]
    await repo.apply_batch(rows, [])
    return rows


# ── 触发判定 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cascade_fires_after_delay():
    scheduler, memory_repo, scene_repo, llm = _build()
    await _seed_l1(memory_repo, "m1", age_seconds=60)

    assert await scheduler.sweep() == 1
    assert len(llm.calls) == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.processing_count == 1
    assert cp.last_memory_at is not None and cp.last_run_at is not None


@pytest.mark.asyncio
async def test_cascade_waits_for_the_delay():
    """L1 刚落定（< 10s）时不整合 —— 文档 L2-1.2 的"延迟 10s 触发"。"""
    scheduler, memory_repo, _, llm = _build()
    await _seed_l1(memory_repo, "m1", age_seconds=2)

    assert await scheduler.sweep() == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_no_new_memories_never_calls_llm():
    """游标之后没有新记忆：不进触发判定，也不建游标之外的状态。"""
    scheduler, memory_repo, scene_repo, llm = _build()
    await _seed_l1(memory_repo, "m1", age_seconds=60)
    assert await scheduler.sweep() == 1
    assert await scheduler.sweep() == 0      # 游标已到最新
    assert len(llm.calls) == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.processing_count == 1          # 没跑就不加计数


@pytest.mark.asyncio
async def test_min_interval_blocks_cascade():
    scheduler, memory_repo, scene_repo, llm = _build(llm=_llm(_actions_json(), _actions_json()))
    await _seed_l1(memory_repo, "m1", age_seconds=60)
    assert await scheduler.sweep() == 1

    await _seed_l1(memory_repo, "m2", age_seconds=60)
    assert await scheduler.sweep() == 0      # 距上次整合 0s < 900s
    assert len(llm.calls) == 1

    # 把上次整合时间挪到最小间隔之前 → 级联立刻放行
    cp = await scene_repo.get_checkpoint("u1", "/root")
    cp.last_run_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    assert await scheduler.sweep() == 1
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_poll_backstop_fires_when_cascade_never_holds():
    """级联迟迟不满足（记忆持续零星落库）时，靠保底轮询兜住。"""
    scheduler, memory_repo, scene_repo, _ = _build(cascade_delay_seconds=3600)
    await _seed_l1(memory_repo, "m1", age_seconds=60)
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root",
        last_run_at=datetime.now(timezone.utc) - timedelta(seconds=3700),
    ))

    assert await scheduler.sweep() == 1


# ── 游标与失败 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parse_failure_does_not_advance_cursor():
    scheduler, memory_repo, scene_repo, llm = _build(llm=_llm("我不会整合。"))
    await _seed_l1(memory_repo, "m1", age_seconds=60)

    assert await scheduler.sweep() == 0
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.processing_count == 0
    assert cp.last_memory_at is None         # 失败不推进，下一轮重试
    assert (await scene_repo.list_by_scope("u1", "/root")) == []


@pytest.mark.asyncio
async def test_batch_limit_processes_only_first_n_per_sweep():
    """每批最多吃 batch_memories 条，剩下的下轮自然续上。"""
    scheduler, memory_repo, scene_repo, llm = _build(
        batch_memories=2, llm=_llm(_actions_json(), _actions_json(),
                                  _actions_json(), _actions_json()),
    )
    rows = await _seed_l1(memory_repo, *[f"m{i}" for i in range(5)], age_seconds=600)

    assert await scheduler.sweep() == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.last_memory_at == rows[1].updated_at     # 只吃到第 2 条

    # 后续几轮继续吃（每轮都要过最小间隔闸门）
    for _ in range(2):
        cp.last_run_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
        assert await scheduler.sweep() == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.last_memory_at == rows[4].updated_at
    assert cp.processing_count == 3


@pytest.mark.asyncio
async def test_cold_start_does_not_reprocess_history_in_one_go():
    """冷启动游标从 epoch 起算也只会吃一批 —— 不需要 L1 那种跳过存量的特判。"""
    scheduler, memory_repo, scene_repo, _ = _build(batch_memories=3)
    await _seed_l1(memory_repo, *[f"m{i}" for i in range(10)], age_seconds=600)

    assert await scheduler.sweep() == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.processing_count == 1
    assert cp.last_memory_at is not None


@pytest.mark.asyncio
async def test_scene_is_written_and_carries_lineage():
    scheduler, memory_repo, scene_repo, _ = _build()
    await _seed_l1(memory_repo, "m1", "m2", age_seconds=60)

    assert await scheduler.sweep() == 1
    rows = await scene_repo.list_by_scope("u1", "/root")
    assert len(rows) == 1
    assert rows[0].name == "技术研究-Rust学习"
    assert rows[0].source_memory_ids == ["m1", "m2"]   # 动作没给 → 整批兜底


@pytest.mark.asyncio
async def test_persona_request_is_persisted_without_a_cascade():
    """没挂 L3 时信号就只是落库 —— 消费方是 L3 生成侧，不是 L2。"""
    payload = json.dumps({"actions": [], "persona_update_request": "重大价值观转变"},
                         ensure_ascii=False)
    scheduler, memory_repo, scene_repo, _ = _build(llm=_llm(payload))
    await _seed_l1(memory_repo, "m1", age_seconds=60)

    assert await scheduler.sweep() == 1
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.persona_update_request == "重大价值观转变"
    assert await scene_repo.list_by_scope("u1", "/root") == []   # 空动作不写库


# ── L3 级联（doc L3-1.2：L3 没有独立定时任务，挂在 L2 的 sweep 上）──

class _RecordingPersonaScheduler:
    def __init__(self):
        self.calls = []

    async def maybe_generate(self, user_id, agent_id, *, request=""):
        self.calls.append((user_id, agent_id, request))
        return False


@pytest.mark.asyncio
async def test_l3_is_cascaded_after_consolidation_with_the_signal():
    payload = json.dumps({"actions": [], "persona_update_request": "重大价值观转变"},
                         ensure_ascii=False)
    scheduler, memory_repo, _, _ = _build(llm=_llm(payload))
    l3 = _RecordingPersonaScheduler()
    scheduler.persona_scheduler = l3
    await _seed_l1(memory_repo, "m1", age_seconds=60)

    assert await scheduler.sweep() == 1
    assert l3.calls == [("u1", "/root", "重大价值观转变")]


@pytest.mark.asyncio
async def test_l3_is_not_cascaded_when_l2_is_skipped():
    """L2 被跳过的那一轮 L3 不评估 —— 否则"没东西可整合"也会不停触发画像。"""
    scheduler, memory_repo, _, _ = _build()
    l3 = _RecordingPersonaScheduler()
    scheduler.persona_scheduler = l3
    await _seed_l1(memory_repo, "m1", age_seconds=2)   # 没过级联延迟

    assert await scheduler.sweep() == 0
    assert l3.calls == []


# ── 作用域 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scopes_are_isolated_by_agent_path():
    scheduler, memory_repo, scene_repo, llm = _build(
        llm=_llm(_actions_json("顶层场景", action="create"),
                 _actions_json("子场景", action="create")),
    )
    await _seed_l1(memory_repo, "m1", agent_id="/root", age_seconds=60)
    await _seed_l1(memory_repo, "m2", agent_id="/root/m1", age_seconds=60)

    assert await scheduler.sweep() == 2
    assert [s.name for s in await scene_repo.list_by_scope("u1", "/root")] == ["顶层场景"]
    assert [s.name for s in await scene_repo.list_by_scope("u1", "/root/m1")] == ["子场景"]

    top = await scene_repo.get_checkpoint("u1", "/root")
    sub = await scene_repo.get_checkpoint("u1", "/root/m1")
    assert top.last_memory_at is not None and sub.last_memory_at is not None


@pytest.mark.asyncio
async def test_scope_with_only_checkpoint_and_no_memories_is_skipped():
    """游标表里遗留的作用域（记忆全被用户删光）不该被反复扫。"""
    scheduler, _, scene_repo, llm = _build()
    await scene_repo.upsert_checkpoint(L2Checkpoint(user_id="u9", agent_id="/root"))

    assert await scheduler.sweep() == 0
    assert llm.calls == []
