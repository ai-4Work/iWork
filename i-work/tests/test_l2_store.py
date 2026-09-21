"""L2 落地语义：三种动作的 heat / version / 血缘 / 软删（设计文档 L2-2.5 落地表）。"""
from datetime import datetime, timedelta, timezone

import pytest

from server.memory.l2.store import apply_actions
from server.memory.l2.types import (
    ACTION_CREATE, ACTION_MERGE, ACTION_UPDATE, L2Scene, SceneAction,
)
from server.storage.memory import InMemoryL2SceneRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
BATCH = ["m_new"]


class RecordingRepo(InMemoryL2SceneRepo):
    """记下每次 apply_batch 的入参，用于断言"一批一个事务"的形状。"""

    def __init__(self):
        super().__init__()
        self.batches: list[tuple[list[str], list[str]]] = []

    async def apply_batch(self, inserts, supersede_ids):
        self.batches.append(([r.name for r in inserts], sorted(supersede_ids)))
        await super().apply_batch(inserts, supersede_ids)


def _existing(sid, name, *, heat=1, version=1, content="旧正文", lineage=None,
              created_at=NOW):
    return L2Scene(
        id=sid, user_id="u1", agent_id="/root", name=name, summary=f"{name} 摘要",
        content=content, heat=heat, version=version,
        source_memory_ids=lineage or [], created_at=created_at, updated_at=created_at,
    )


async def _seed(*scenes) -> RecordingRepo:
    repo = RecordingRepo()
    await repo.apply_batch(list(scenes), [])
    repo.batches.clear()
    return repo


async def _run(repo, actions, *, batch=None):
    scenes = await repo.list_by_scope("u1", "/root")
    return await apply_actions(
        repo, actions, scenes=scenes, user_id="u1", agent_id="/root",
        batch_memory_ids=batch if batch is not None else BATCH,
    )


async def _live(repo):
    return {s.name: s for s in await repo.list_by_scope("u1", "/root")}


async def _all(repo):
    return {s.name: s for s in await repo.list_by_scope(
        "u1", "/root", retrievable_only=False)}


# ── create ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_starts_heat_and_version_at_one():
    repo = await _seed()
    stats = await _run(repo, [
        SceneAction(action=ACTION_CREATE, name="日常生活-日本旅行",
                    summary="s", content="c"),
    ])
    assert stats == {ACTION_CREATE: 1, ACTION_UPDATE: 0, ACTION_MERGE: 0}
    row = (await _live(repo))["日常生活-日本旅行"]
    assert (row.heat, row.version) == (1, 1)
    assert row.source_memory_ids == BATCH          # 动作没给 → 整批兜底
    assert row.retrievable is True


@pytest.mark.asyncio
async def test_create_on_existing_name_falls_back_to_update():
    """create 撞上已有名字：走 update 语义，否则整批会撞局部唯一索引回滚。"""
    repo = await _seed(_existing("s1", "技术研究-Rust学习", heat=2, version=1))
    stats = await _run(repo, [
        SceneAction(action=ACTION_CREATE, name="技术研究-Rust学习",
                    summary="新", content="新正文"),
    ])
    assert stats[ACTION_UPDATE] == 1 and stats[ACTION_CREATE] == 0
    live = await _live(repo)
    assert live["技术研究-Rust学习"].heat == 3
    assert len(repo.batches) == 1 and repo.batches[0][1] == ["s1"]


# ── update ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_bumps_heat_and_version_and_supersedes_old_row():
    earlier = NOW - timedelta(days=3)
    repo = await _seed(_existing("s1", "技术研究-Rust学习", heat=4, version=2,
                                 lineage=["m_old"], created_at=earlier))
    await _run(repo, [
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", summary="s2",
                    content="新正文", source_memory_ids=["m_new"]),
    ])
    row = (await _live(repo))["技术研究-Rust学习"]
    assert (row.heat, row.version) == (5, 3)
    assert row.id != "s1"                          # 新行，血缘靠旧行保留
    assert row.created_at == earlier               # 沿用目标最早的创建时间
    assert row.source_memory_ids == ["m_old", "m_new"]   # 血缘并集

    old = (await _all(repo))["技术研究-Rust学习"]
    assert old.id == "s1" or old.content == "旧正文"
    dead = [s for s in (await _all(repo)).values() if not s.retrievable]
    assert len(dead) == 1 and dead[0].content == "旧正文"   # 软删保留正文，可回查


@pytest.mark.asyncio
async def test_two_updates_on_same_name_in_one_batch_mint_one_row():
    """同一批里对同一场景两次更新：只产出一行、只软删一次，heat 累加两次。"""
    repo = await _seed(_existing("s1", "技术研究-Rust学习", heat=1, version=1))
    await _run(repo, [
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", content="一改"),
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", content="二改"),
    ])
    assert len(repo.batches) == 1
    names, supersede = repo.batches[0]
    assert names == ["技术研究-Rust学习"] and supersede == ["s1"]
    row = (await _live(repo))["技术研究-Rust学习"]
    assert (row.heat, row.version, row.content) == (3, 3, "二改")


@pytest.mark.asyncio
async def test_update_keeps_old_summary_when_action_omits_it():
    repo = await _seed(_existing("s1", "技术研究-Rust学习"))
    await _run(repo, [
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", content="新正文"),
    ])
    assert (await _live(repo))["技术研究-Rust学习"].summary == "技术研究-Rust学习 摘要"


# ── merge ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_merge_sums_heat_takes_max_version_and_supersedes_all_sources():
    repo = await _seed(
        _existing("s1", "Python后端开发", heat=4, version=3, lineage=["m_a"],
                  created_at=NOW - timedelta(days=5)),
        _existing("s2", "Go后端开发", heat=1, version=1, lineage=["m_b"],
                  created_at=NOW - timedelta(days=1)),
    )
    stats = await _run(repo, [
        SceneAction(action=ACTION_MERGE, name="后端开发技术栈",
                    sources=["Python后端开发", "Go后端开发"],
                    summary="后端", content="合并正文", source_memory_ids=["m_c"]),
    ])
    assert stats[ACTION_MERGE] == 1
    names, supersede = repo.batches[0]
    assert names == ["后端开发技术栈"] and supersede == ["s1", "s2"]

    row = (await _live(repo))["后端开发技术栈"]
    assert (row.heat, row.version) == (6, 4)      # Σ(4+1)+1 / max(3,1)+1
    assert row.source_memory_ids == ["m_a", "m_b", "m_c"]
    assert row.created_at == NOW - timedelta(days=5)   # 被并者里最早的创建时间
    assert set(await _live(repo)) == {"后端开发技术栈"}


@pytest.mark.asyncio
async def test_merge_into_an_existing_non_source_scene_also_supersedes_it():
    """LLM 常写"把 A 并入已有的 B"：B 不在 sources 里，但新名字就是 B，也必须软删。"""
    repo = await _seed(
        _existing("s1", "求职材料-JD匹配", heat=2, version=1),
        _existing("s2", "职业发展-能力对齐", heat=3, version=1),
    )
    await _run(repo, [
        SceneAction(action=ACTION_MERGE, name="职业发展-能力对齐",
                    sources=["求职材料-JD匹配"], content="合并正文"),
    ])
    names, supersede = repo.batches[0]
    assert names == ["职业发展-能力对齐"] and supersede == ["s1", "s2"]
    row = (await _live(repo))["职业发展-能力对齐"]
    assert (row.heat, row.version) == (6, 2)


@pytest.mark.asyncio
async def test_merge_with_no_resolvable_target_degrades_to_create():
    repo = await _seed()
    stats = await _run(repo, [
        SceneAction(action=ACTION_MERGE, name="孤立的新场景", sources=[], content="c"),
    ])
    assert stats[ACTION_MERGE] == 0 and stats[ACTION_CREATE] == 1
    assert (await _live(repo))["孤立的新场景"].heat == 1


@pytest.mark.asyncio
async def test_merge_swallows_a_scene_minted_earlier_in_the_same_batch():
    """同批"先 create 再 merge 掉它"：中间行不该落库，也不该留软删痕迹。"""
    repo = await _seed(_existing("s1", "旧场景", heat=1, version=1))
    await _run(repo, [
        SceneAction(action=ACTION_CREATE, name="临时场景", content="临"),
        SceneAction(action=ACTION_MERGE, name="最终场景",
                    sources=["临时场景", "旧场景"], content="合"),
    ])
    assert repo.batches[0][1] == ["s1"]            # 只软删落库过的旧行
    live = await _live(repo)
    assert set(live) == {"最终场景"}
    assert live["最终场景"].heat == 3              # (1 + 1) + 1
    assert (await _all(repo)).get("临时场景") is None


# ── 一批一个事务 / 无动作 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_no_actions_touches_nothing():
    repo = await _seed(_existing("s1", "技术研究-Rust学习"))
    stats = await _run(repo, [])
    assert repo.batches == []                      # 不写库
    assert stats == {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_MERGE: 0}


@pytest.mark.asyncio
async def test_untouched_scenes_are_not_rewritten():
    """没被任何动作碰到的场景不进 inserts —— 否则每轮都会白写一遍全量场景。"""
    repo = await _seed(
        _existing("s1", "技术研究-Rust学习"),
        _existing("s2", "日常生活-健康", heat=9),
    )
    await _run(repo, [
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", content="新"),
    ])
    assert repo.batches[0][0] == ["技术研究-Rust学习"]


@pytest.mark.asyncio
async def test_batch_ids_fallback_applies_to_update_lineage_too():
    repo = await _seed(_existing("s1", "技术研究-Rust学习", lineage=["m_old"]))
    await _run(repo, [
        SceneAction(action=ACTION_UPDATE, name="技术研究-Rust学习", content="新"),
    ])
    assert (await _live(repo))["技术研究-Rust学习"].source_memory_ids == ["m_old", "m_new"]
