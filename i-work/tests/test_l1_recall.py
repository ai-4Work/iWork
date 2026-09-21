"""召回：查询清洗、阈值、预算、注入块形状（设计文档 L1-3）。"""
import pytest

from server.memory.recall import L1RecallService, clean_query, format_entry
from server.memory.types import L1Memory
from server.storage.memory import InMemoryL1MemoryRepo


def _memory(mid, content, *, priority=80, mtype="episodic", agent_id="/root",
            scene="我在和用户调时区", metadata=None, version=1):
    return L1Memory(
        id=mid, user_id="u1", content=content, type=mtype, priority=priority,
        agent_id=agent_id, scene_name=scene, version=version,
        metadata=metadata or {},
    )


async def _service(repo, **kw):
    return L1RecallService(repo, **kw)


# ── 查询清洗 ───────────────────────────────────────────────────

def test_clean_query_strips_injected_blocks():
    raw = "问题正文\n<relevant-memories>- [episodic] 旧事实</relevant-memories>"
    assert clean_query(raw) == "问题正文"


def test_clean_query_handles_none():
    assert clean_query(None) == ""


# ── 格式化 ─────────────────────────────────────────────────────

def test_format_entry_includes_scene_and_type():
    line = format_entry(_memory("m1", "用户的时区是 UTC+8"), 500)
    assert line == "[episodic|我在和用户调时区] 用户的时区是 UTC+8"


def test_format_entry_appends_activity_time_only_for_episodic():
    episodic = _memory("m1", "上周做了压测", metadata={
        "activity_start_time": "2026-08-18", "activity_end_time": "2026-08-19",
    })
    assert "(活动时间: 2026-08-18 ~ 2026-08-19)" in format_entry(episodic, 500)

    persona = _memory("m2", "用户是后端工程师", mtype="persona", metadata={
        "activity_start_time": "2026-08-18",
    })
    assert "活动时间" not in format_entry(persona, 500)


def test_format_entry_truncates_over_budget():
    line = format_entry(_memory("m1", "长" * 100), 20)
    assert line.endswith("…已截断")
    assert len(line) == len("[episodic|我在和用户调时区] ") + 20 + len("…已截断")


# ── 召回 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_injects_relevant_memory():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m1", "用户的时区是 UTC+8，在北京")], [])
    service = await _service(repo, min_score=0.15)

    block = await service.recall_block(
        user_id="u1", agent_id="/root", query="我在哪个时区？时区是几点",
    )
    assert "<relevant-memories>" in block
    assert "用户的时区是 UTC+8" in block
    assert "仅作参考" in block


@pytest.mark.asyncio
async def test_short_query_is_skipped():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m1", "用户的时区是 UTC+8")], [])
    service = await _service(repo, min_score=0.15)
    assert await service.recall_block(user_id="u1", agent_id="/root", query="?") == ""
    assert await service.recall_block(user_id="u1", agent_id="/root", query="") == ""


@pytest.mark.asyncio
async def test_no_hit_produces_empty_block():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m1", "咖啡因会让人睡不着觉")], [])
    service = await _service(repo, min_score=0.3)
    block = await service.recall_block(
        user_id="u1", agent_id="/root", query="帮我算一下矩阵乘法",
    )
    assert block == ""


@pytest.mark.asyncio
async def test_recall_is_scoped_to_agent():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m1", "用户的时区是 UTC+8", agent_id="/root/m1")], [])
    service = await _service(repo, min_score=0.15)
    assert await service.recall_block(
        user_id="u1", agent_id="/root", query="用户的时区是几点",
    ) == ""


@pytest.mark.asyncio
async def test_recall_only_sees_retrievable_rows():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m_old", "用户的时区是 UTC+8")], [])
    await repo.apply_batch([_memory("m_new", "用户的时区是 UTC+8")], ["m_old"])
    service = await _service(repo, min_score=0.15)

    block = await service.recall_block(
        user_id="u1", agent_id="/root", query="用户的时区是几点",
    )
    assert block.count("- [") == 1


@pytest.mark.asyncio
async def test_total_budget_drops_overflowing_entries():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([
        _memory(f"m{i}", "用户的时区是 UTC+8 这件事反复确认过") for i in range(5)
    ], [])
    service = await _service(repo, min_score=0.15, max_chars=120)

    block = await service.recall_block(
        user_id="u1", agent_id="/root", query="用户的时区是几点",
    )
    assert block.count("- [") < 5


@pytest.mark.asyncio
async def test_search_is_the_tool_path_without_threshold_bypass():
    """memory_search 与被动召回同一套检索与阈值，只是可以指定 top_k。"""
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch(
        [_memory(f"m{i}", "用户的时区是 UTC+8 反复确认") for i in range(4)], [],
    )
    service = await _service(repo, min_score=0.15)
    assert len(await service.search(
        user_id="u1", agent_id="/root", query="用户的时区", top_k=2,
    )) == 2
    assert await service.search(user_id="u1", agent_id="/root", query="x") == []


@pytest.mark.asyncio
async def test_guide_xml_is_the_stable_half():
    repo = InMemoryL1MemoryRepo()
    service = await _service(repo)
    assert "<memory-tools-guide>" in service.guide_xml
