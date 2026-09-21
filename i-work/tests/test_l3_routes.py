"""/l3 路由：形状与仓储级过滤（本环境无 HTTP TestClient，端点逻辑下沉到纯函数/repo 验证）。

响应体整形走 `_l3_row`，列表的过滤/分页走 `L3PersonaRepository.list_page`，硬删语义在 repo 层
验证。另钉住一条：DELETE 用 query 参数定位作用域而不是路径 id（画像行没有 id，主键是
(user_id, agent_id)）。
"""
from datetime import datetime, timezone

import pytest

from server.api.routes import _l3_row, router_l3
from server.memory.l3.types import L3Persona
from server.storage.memory import InMemoryL3PersonaRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _persona(agent_id="", *, content="画像正文", version=1, count=7, updated_at=NOW):
    return L3Persona(
        user_id="u1", agent_id=agent_id, content=content, version=version,
        memory_count_at_generation=count,
        created_at=updated_at, updated_at=updated_at,
    )


# ── 路由表 ─────────────────────────────────────────────────────

def test_router_paths():
    paths = {(r.path, tuple(sorted(r.methods))) for r in router_l3.routes}
    assert ("/l3/personas", ("GET",)) in paths
    assert ("/l3/personas", ("DELETE",)) in paths


# ── 响应体整形 ─────────────────────────────────────────────────

def test_l3_row_shapes_dates_as_iso():
    row = _l3_row({
        "agent_id": "/root/m1", "content": "画像正文", "version": 3,
        "memory_count_at_generation": 42,
        "created_at": NOW, "updated_at": NOW,
    })
    assert row == {
        "agent_id": "/root/m1", "content": "画像正文", "version": 3,
        "memory_count_at_generation": 42,
        "created_at": "2026-09-20T10:00:00+00:00",
        "updated_at": "2026-09-20T10:00:00+00:00",
    }


def test_l3_row_has_no_id_field():
    """画像的行身份就是 (user_id, agent_id)，响应里不该冒出一个 id。"""
    assert "id" not in _l3_row({
        "agent_id": "", "content": "c", "version": 1,
        "memory_count_at_generation": 0,
        "created_at": NOW, "updated_at": NOW,
    })


# ── 列表的过滤 / 排序 / 分页 ───────────────────────────────────

@pytest.mark.asyncio
async def test_list_page_filters_by_agent_and_user():
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(_persona(""))                     # 顶层
    await repo.upsert(_persona("/root/m1"))

    rows, total = await repo.list_page("u1", agent_id="/root/m1")
    assert total == 1 and rows[0].agent_id == "/root/m1"
    assert (await repo.list_page("u1"))[1] == 2         # 不传就是全作用域
    assert (await repo.list_page("other-user"))[1] == 0


@pytest.mark.asyncio
async def test_list_page_orders_by_recency_and_paginates():
    repo = InMemoryL3PersonaRepo()
    for i in range(3):
        await repo.upsert(_persona(f"/root/a{i}"))

    rows, total = await repo.list_page("u1", limit=2, offset=0)
    assert total == 3 and len(rows) == 2
    rows, _ = await repo.list_page("u1", limit=2, offset=2)
    assert len(rows) == 1


# ── 写入语义：版本递增、created_at 不随重写变 ─────────────────

@pytest.mark.asyncio
async def test_upsert_inserts_then_bumps_version():
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(_persona(content="第一版"))
    first = await repo.get("u1", "")
    assert first.version == 1
    created = first.created_at

    await repo.upsert(_persona(content="第二版"))
    second = await repo.get("u1", "")
    assert second.version == 2
    assert second.content == "第二版"
    assert second.created_at == created


# ── DELETE 是硬删，且按作用域定位 ──────────────────────────────

@pytest.mark.asyncio
async def test_delete_is_hard_and_scoped():
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(_persona(""))
    await repo.upsert(_persona("/root/m1"))

    assert await repo.delete("u1", "/root/m1") is True
    assert await repo.get("u1", "/root/m1") is None
    assert await repo.get("u1", "") is not None          # 另一个作用域不受影响
    assert await repo.delete("u1", "/root/m1") is False  # 再删一次报"不存在"


@pytest.mark.asyncio
async def test_delete_tolerates_missing_agent_id_as_top_scope():
    """顶层作用域的 agent_id 是空串，不是缺省 —— 路由侧用 Query("") 兜住。"""
    repo = InMemoryL3PersonaRepo()
    await repo.upsert(_persona(""))
    assert await repo.delete("u1", "") is True
