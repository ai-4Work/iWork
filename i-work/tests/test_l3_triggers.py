"""L3 触发判定与落地（设计文档 L3-1.2 / L3-2.2 / L3-2.6 / L3-2.7）。

四优先级按序评估：P1 主动请求 → P2 冷启动 → P3 首个场景 → P4 阈值。**没有 l3_checkpoints**，
触发判据全部由 DB 现算，所以这些用例全部通过内存替身摆位来验。

顺带钉住三条容易写错的语义：短路时不消费 P1 信号、成功时 version +1 且 created_at 保留、
生成失败（LLM 空返回）不写行也不清信号。
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.l2.types import L2Checkpoint, L2Scene
from server.memory.l3.generator import PersonaGenerator
from server.memory.l3.reader import L3Reader
from server.memory.l3.scheduler import L3Scheduler
from server.memory.l3.types import (
    TRIGGER_COLD_START, TRIGGER_FIRST_SCENE, TRIGGER_REQUEST, TRIGGER_THRESHOLD,
)
from server.memory.types import L1Memory
from server.storage.memory import (
    InMemoryL1MemoryRepo, InMemoryL2SceneRepo, InMemoryL3PersonaRepo,
)

BODY = "# User Narrative Profile\n\n> **Archetype**: 务实理想主义者"


def _llm(*payloads: str) -> FakeLLMClient:
    return FakeLLMClient(
        responses=[[LLMChunk(type="text", delta=p)] for p in payloads],
    )


def _build(*, llm=None, threshold=50):
    memory_repo = InMemoryL1MemoryRepo()
    scene_repo = InMemoryL2SceneRepo()
    persona_repo = InMemoryL3PersonaRepo()
    scheduler = L3Scheduler(
        persona_repo=persona_repo,
        scene_repo=scene_repo,
        memory_repo=memory_repo,
        reader=L3Reader(memory_repo, scene_repo, persona_repo),
        generator=PersonaGenerator(llm or _llm(BODY)),
        threshold=threshold,
    )
    return scheduler, memory_repo, scene_repo, persona_repo


async def _seed_scene(repo, name="技术研究-Rust学习", *, agent_id="/root",
                      updated_at=None, heat=1):
    scene = L2Scene(
        id=f"sc-{name}", user_id="u1", agent_id=agent_id, name=name,
        summary="摘要", content="完整经过", heat=heat, version=1,
        created_at=updated_at or datetime.now(timezone.utc),
        updated_at=updated_at or datetime.now(timezone.utc),
    )
    await repo.apply_batch([scene], [])
    return scene


async def _seed_l1(repo, *ids, agent_id="/root", updated_at=None):
    rows = [
        L1Memory(id=i, user_id="u1", content=f"记忆{i}", agent_id=agent_id,
                 updated_at=updated_at or datetime.now(timezone.utc))
        for i in ids
    ]
    await repo.apply_batch(rows, [])
    return rows


def _after(seconds=1) -> datetime:
    """晚于"刚刚写下的画像行"的时间戳 —— 用来把场景/记忆摆成"画像之后变化/新增"。"""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# ── P1 主动请求 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p1_request_fires_and_is_consumed():
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=0,
        persona_update_request="重大价值观转变",
    ))

    assert await scheduler.maybe_generate(
        "u1", "/root", request="重大价值观转变",
    ) is True

    persona = await persona_repo.get("u1", "/root")
    assert persona.content == BODY
    assert persona.version == 1
    assert persona.memory_count_at_generation == 1
    # 信号被消费：下次不会因为同一个请求又跑一遍
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.persona_update_request == ""


@pytest.mark.asyncio
async def test_p1_short_circuit_keeps_the_signal_for_next_round():
    """画像已存在、场景一个没变 → 短路跳过，且**不清信号**（doc L3-2.2）。"""
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True      # 先出一份画像

    # L2 这一轮报了主动请求，但场景没动（updated_at 早于刚写的画像行）
    cp = await scene_repo.get_checkpoint("u1", "/root")
    cp.persona_update_request = "重大价值观转变"
    await scene_repo.upsert_checkpoint(cp)

    assert await scheduler.maybe_generate("u1", "/root", request="重大价值观转变") is False
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.persona_update_request == "重大价值观转变"   # 留着，等场景变了再消费
    assert (await persona_repo.get("u1", "/root")).version == 1


# ── P2 冷启动 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p2_cold_start_fires_without_persona_row():
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))

    assert await scheduler.maybe_generate("u1", "/root") is True
    assert (await persona_repo.get("u1", "/root")).version == 1


@pytest.mark.asyncio
async def test_p2_does_not_fire_when_the_cursor_never_moved():
    """processing_count == 0：L2 还没整合过，不该凭空造画像。"""
    scheduler, _, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)      # 有场景，但游标没动（也没有画像行）

    assert await scheduler.maybe_generate("u1", "/root") is False
    assert await persona_repo.get("u1", "/root") is None


# ── P3 首个场景 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p3_fires_when_new_memory_lands_after_persona():
    """processing_count 停在 1、画像之后又落了新记忆 → P3。"""
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True      # 先冷启动出第一份

    # 场景动了一次 + 画像之后落了新记忆（processed 仍是 1）
    await _seed_scene(scene_repo, updated_at=_after())
    await _seed_l1(memory_repo, "m2", updated_at=_after())

    assert await scheduler.maybe_generate("u1", "/root") is True
    assert (await persona_repo.get("u1", "/root")).version == 2


@pytest.mark.asyncio
async def test_p3_does_not_fire_without_new_memory():
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True

    await _seed_scene(scene_repo, updated_at=_after())    # 场景变了，但没新记忆

    assert await scheduler.maybe_generate("u1", "/root") is False
    assert (await persona_repo.get("u1", "/root")).version == 1


# ── P4 阈值 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p4_fires_at_the_threshold():
    scheduler, memory_repo, scene_repo, persona_repo = _build(threshold=2)
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1", "m2")
    # processed == 2 把 P3 排除掉，单看 P4
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=2,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True

    await _seed_l1(memory_repo, "m3", "m4", updated_at=_after())   # 增量 2 == 阈值
    await _seed_scene(scene_repo, updated_at=_after())

    assert await scheduler.maybe_generate("u1", "/root") is True
    assert (await persona_repo.get("u1", "/root")).version == 2


@pytest.mark.asyncio
async def test_p4_does_not_fire_below_the_threshold():
    scheduler, memory_repo, scene_repo, persona_repo = _build(threshold=3)
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1", "m2")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=2,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True

    await _seed_l1(memory_repo, "m3", "m4", updated_at=_after())   # 增量 2 < 3
    await _seed_scene(scene_repo, updated_at=_after())

    assert await scheduler.maybe_generate("u1", "/root") is False
    assert (await persona_repo.get("u1", "/root")).version == 1


@pytest.mark.asyncio
async def test_trigger_reasons_are_reported():
    """触发原因是给日志/可观测性看的，四档各自可辨。"""
    scheduler, memory_repo, scene_repo, _ = _build(threshold=1)
    await _seed_scene(scene_repo)
    await _seed_l1(memory_repo, "m1")

    assert await scheduler._trigger_for("u1", "/root", "主动") == TRIGGER_REQUEST
    assert await scheduler._trigger_for("u1", "/root", "") is None    # 还没跑过 L2

    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))
    assert await scheduler._trigger_for("u1", "/root", "") == TRIGGER_COLD_START

    await scheduler.maybe_generate("u1", "/root")                    # 冷启动落一行
    # 场景未变 + 无新记忆：P3 的条件（有新记忆）不成立，P4 增量 0 < 1
    assert await scheduler._trigger_for("u1", "/root", "") is None

    await _seed_l1(memory_repo, "m2", updated_at=_after())
    assert await scheduler._trigger_for("u1", "/root", "") == TRIGGER_FIRST_SCENE


# ── 落地与失败语义 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rewrite_bumps_version_and_keeps_created_at():
    scheduler, memory_repo, scene_repo, persona_repo = _build()
    await _seed_scene(scene_repo)
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))
    assert await scheduler.maybe_generate("u1", "/root") is True
    first = await persona_repo.get("u1", "/root")
    created = first.created_at

    await _seed_scene(scene_repo, updated_at=_after())
    await _seed_l1(memory_repo, "m1", updated_at=_after())   # P3 还要"有新记忆"
    assert await scheduler.maybe_generate("u1", "/root") is True

    again = await persona_repo.get("u1", "/root")
    assert again.version == 2
    assert again.created_at == created          # "首次生成时间"不随重写变
    assert again.updated_at >= created


@pytest.mark.asyncio
async def test_empty_generation_writes_nothing_and_keeps_the_signal():
    """LLM 返回空 → 不写行、不清信号，跟 L2 的节奏下轮重试（doc L3-2.6）。"""
    scheduler, memory_repo, scene_repo, persona_repo = _build(llm=_llm("   "))
    await _seed_scene(scene_repo)
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
        persona_update_request="重大价值观转变",
    ))

    assert await scheduler.maybe_generate("u1", "/root", request="重大价值观转变") is False
    assert await persona_repo.get("u1", "/root") is None
    cp = await scene_repo.get_checkpoint("u1", "/root")
    assert cp.persona_update_request == "重大价值观转变"


@pytest.mark.asyncio
async def test_llm_exception_is_swallowed():
    """画像生成炸了不能把 L2 的整轮 sweep 判成失败。"""

    class Boom:
        async def stream(self, *a, **kw):
            raise RuntimeError("provider down")
            yield  # pragma: no cover

    scheduler, memory_repo, scene_repo, persona_repo = _build(llm=Boom())
    await _seed_scene(scene_repo)
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))

    assert await scheduler.maybe_generate("u1", "/root") is False
    assert await persona_repo.get("u1", "/root") is None


# ── 作用域隔离 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scopes_are_isolated_by_agent_path():
    scheduler, memory_repo, scene_repo, persona_repo = _build(llm=_llm(BODY, BODY))
    await _seed_scene(scene_repo, "顶层场景")
    await _seed_scene(scene_repo, "子场景", agent_id="/root/m1")
    await scene_repo.upsert_checkpoint(L2Checkpoint(
        user_id="u1", agent_id="/root", processing_count=1,
    ))

    # 顶层靠冷启动（它的游标走到了 1）；子作用域游标没动，只能靠 P1 主动请求
    assert await scheduler.maybe_generate("u1", "/root", request="") is True
    assert await scheduler.maybe_generate("u1", "/root/m1", request="") is False

    assert await scheduler.maybe_generate("u1", "/root/m1", request="主动") is True
    rows, total = await persona_repo.list_page("u1")
    assert total == 2
    assert {r.agent_id for r in rows} == {"/root", "/root/m1"}
