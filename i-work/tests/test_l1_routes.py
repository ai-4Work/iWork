"""/l1 路由：形状与仓储级过滤（本环境无 HTTP TestClient，端点逻辑下沉到纯函数/repo 验证）。

响应体整形走 `_l1_row`，列表的过滤/排序/分页走 `L1MemoryRepository.list_page`，
两者都是端点的直接实现，合起来覆盖 GET 的全部行为；DELETE 的硬删语义在 repo 层验证。
"""
from datetime import datetime, timezone

import pytest

from server.api.routes import _l1_row, router_l1
from server.memory.types import L1Memory
from server.storage.memory import InMemoryL1MemoryRepo


def _memory(mid, content, *, mtype="episodic", agent_id="/root", version=1,
            scene="我在和用户调时区", metadata=None, updated_at=None):
    return L1Memory(
        id=mid, user_id="u1", content=content, type=mtype, priority=80,
        agent_id=agent_id, scene_name=scene, version=version,
        metadata=metadata or {}, updated_at=updated_at,
        created_at=updated_at,
    )


# ── 路由表 ─────────────────────────────────────────────────────

def test_router_paths():
    paths = {(r.path, tuple(sorted(r.methods))) for r in router_l1.routes}
    assert ("/l1/memories", ("GET",)) in paths
    assert ("/l1/memories/{memory_id}", ("DELETE",)) in paths


# ── 响应体整形 ─────────────────────────────────────────────────

def test_l1_row_flattens_metadata_and_timestamps_as_iso():
    now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    row = _l1_row({
        "id": "m_1", "content": "用户的时区是 UTC+8", "type": "episodic",
        "priority": 80, "scene_name": "我在和用户调时区", "agent_id": "/root",
        "version": 2,
        "metadata_json": {
            "activity_start_time": "2026-08-18", "activity_end_time": "2026-08-19",
        },
        "created_at": now, "updated_at": now,
    })
    assert row == {
        "id": "m_1", "content": "用户的时区是 UTC+8", "type": "episodic",
        "priority": 80, "scene_name": "我在和用户调时区", "agent_id": "/root",
        "activity_start_time": "2026-08-18", "activity_end_time": "2026-08-19",
        "version": 2,
        "created_at": "2026-08-18T10:00:00+00:00",
        "updated_at": "2026-08-18T10:00:00+00:00",
    }


def test_l1_row_tolerates_missing_metadata():
    row = _l1_row({
        "id": "m_1", "content": "x", "type": "persona", "priority": 90,
        "scene_name": "", "agent_id": "", "version": 1,
        "metadata_json": None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    })
    assert row["activity_start_time"] is None


# ── 列表的过滤 / 排序 / 分页 ───────────────────────────────────

@pytest.mark.asyncio
async def test_list_page_hides_superseded_rows():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m_old", "旧事实")], [])
    await repo.apply_batch([_memory("m_new", "新事实")], ["m_old"])

    rows, total = await repo.list_page("u1")
    assert total == 1 and rows[0].id == "m_new"


@pytest.mark.asyncio
async def test_list_page_filters_by_type_and_agent():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([
        _memory("m1", "事件", mtype="episodic"),
        _memory("m2", "画像", mtype="persona"),
        _memory("m3", "子 agent 的事件", agent_id="/root/m1"),
    ], [])

    rows, total = await repo.list_page("u1", memory_type="persona")
    assert total == 1 and rows[0].id == "m2"

    rows, total = await repo.list_page("u1", agent_id="/root/m1")
    assert total == 1 and rows[0].id == "m3"

    assert (await repo.list_page("u1"))[1] == 3
    assert (await repo.list_page("other-user"))[1] == 0


@pytest.mark.asyncio
async def test_list_page_orders_by_updated_at_desc_and_paginates():
    repo = InMemoryL1MemoryRepo()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    await repo.apply_batch([
        _memory(f"m{i}", f"事实 {i}", updated_at=base.replace(day=i + 1))
        for i in range(5)
    ], [])

    rows, total = await repo.list_page("u1", limit=2, offset=0)
    assert total == 5
    assert [r.id for r in rows] == ["m4", "m3"]

    rows, _ = await repo.list_page("u1", limit=2, offset=2)
    assert [r.id for r in rows] == ["m2", "m1"]


# ── DELETE 是硬删 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_is_hard():
    repo = InMemoryL1MemoryRepo()
    await repo.apply_batch([_memory("m1", "删错的记忆")], [])

    assert await repo.delete("m1") is True
    assert await repo.get("m1") is None           # 行没了，不是 retrievable=false
    assert await repo.delete("m1") is False       # 再删一次报"不存在"
