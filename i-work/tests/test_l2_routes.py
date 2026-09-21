"""/l2 路由：形状与仓储级过滤（本环境无 HTTP TestClient，端点逻辑下沉到纯函数/repo 验证）。

响应体整形走 `_l2_row`，列表的过滤/排序/分页走 `L2SceneRepository.list_by_scope` 与
`list_page`，两者都是端点的直接实现，合起来覆盖 GET 的全部行为；DELETE 的硬删语义在
repo 层验证。`scene_read` 工具的执行边界（限次、未命中回名单）单列一节。
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server.api.routes import _l2_row, router_l2
from server.engine.query_loop import QueryLoopEngine, SCENE_READ_TOOLS
from server.memory.l2.recall import L2RecallService
from server.memory.l2.types import L2Scene
from server.storage.memory import InMemoryL2SceneRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _scene(sid, name, *, heat=1, version=1, agent_id="/root", summary="摘要",
           content="正文", updated_at=NOW):
    return L2Scene(
        id=sid, user_id="u1", agent_id=agent_id, name=name, summary=summary,
        content=content, heat=heat, version=version,
        created_at=updated_at, updated_at=updated_at,
    )


# ── 路由表 ─────────────────────────────────────────────────────

def test_router_paths():
    paths = {(r.path, tuple(sorted(r.methods))) for r in router_l2.routes}
    assert ("/l2/scenes", ("GET",)) in paths
    assert ("/l2/scenes/{scene_id}", ("DELETE",)) in paths


# ── 响应体整形 ─────────────────────────────────────────────────

def test_l2_row_shapes_fields_as_iso():
    row = _l2_row({
        "id": "s_1", "name": "技术研究-Rust学习", "summary": "摘要",
        "content": "正文", "heat": 4, "version": 3, "agent_id": "/root",
        "source_memory_ids": ["m1", "m2"],
        "created_at": NOW, "updated_at": NOW,
    })
    assert row == {
        "id": "s_1", "name": "技术研究-Rust学习", "summary": "摘要",
        "content": "正文", "heat": 4, "version": 3, "agent_id": "/root",
        "source_memory_ids": ["m1", "m2"],
        "created_at": "2026-09-20T10:00:00+00:00",
        "updated_at": "2026-09-20T10:00:00+00:00",
    }


def test_l2_row_tolerates_null_lineage():
    row = _l2_row({
        "id": "s_1", "name": "n", "summary": "", "content": "c", "heat": 1,
        "version": 1, "agent_id": "", "source_memory_ids": None,
        "created_at": NOW, "updated_at": NOW,
    })
    assert row["source_memory_ids"] == []


# ── 列表的过滤 / 排序 / 分页 ───────────────────────────────────

@pytest.mark.asyncio
async def test_list_page_hides_superseded_scenes():
    repo = InMemoryL2SceneRepo()
    await repo.apply_batch([_scene("s_old", "旧场景")], [])
    await repo.apply_batch([_scene("s_new", "新场景", version=2)], ["s_old"])

    rows, total = await repo.list_page("u1")
    assert total == 1 and rows[0].id == "s_new"


@pytest.mark.asyncio
async def test_list_page_orders_by_heat_desc_then_recency_and_paginates():
    repo = InMemoryL2SceneRepo()
    await repo.apply_batch([
        _scene("s1", "低热", heat=1),
        _scene("s2", "高热", heat=300),
        _scene("s3", "中热", heat=50),
    ], [])

    rows, total = await repo.list_page("u1", limit=2, offset=0)
    assert total == 3
    assert [r.id for r in rows] == ["s2", "s3"]

    rows, _ = await repo.list_page("u1", limit=2, offset=2)
    assert [r.id for r in rows] == ["s1"]


@pytest.mark.asyncio
async def test_list_page_filters_by_agent_and_user():
    repo = InMemoryL2SceneRepo()
    await repo.apply_batch([
        _scene("s1", "顶层场景"),
        _scene("s2", "子场景", agent_id="/root/m1"),
    ], [])

    rows, total = await repo.list_page("u1", agent_id="/root/m1")
    assert total == 1 and rows[0].id == "s2"
    assert (await repo.list_page("u1"))[1] == 2
    assert (await repo.list_page("other-user"))[1] == 0


# ── DELETE 是硬删 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_is_hard():
    repo = InMemoryL2SceneRepo()
    await repo.apply_batch([_scene("s1", "删错的场景")], [])

    assert await repo.delete("s1") is True
    assert await repo.get("s1") is None           # 行没了，不是 retrievable=false
    assert await repo.delete("s1") is False       # 再删一次报"不存在"


# ── scene_read 工具的定义与执行边界 ────────────────────────────

def test_scene_read_tool_definition():
    assert [t["name"] for t in SCENE_READ_TOOLS] == ["scene_read"]
    schema = SCENE_READ_TOOLS[0]["input_schema"]
    assert schema["required"] == ["name"]


def test_scene_read_is_server_executed_read_only():
    """漏了分类就会按客户端工具下发（前端没这个工具），也会被误记进副作用账。"""
    from server.tools.dispatcher import ToolDispatcher, ToolLocation
    from server.tools.idempotency import tool_idempotency, tool_side_effect

    assert ToolDispatcher().classify("scene_read") == ToolLocation.SERVER
    assert tool_idempotency("scene_read") == "read-only"
    assert tool_side_effect("scene_read") is False


def _engine_with(*scenes):
    """只带 _execute_scene_read 所需属性的替身（session / 限次计数 / 召回服务）。"""
    repo = InMemoryL2SceneRepo()
    stub = SimpleNamespace(
        _scene_recall=L2RecallService(repo),
        _scene_read_calls=0,
        session=SimpleNamespace(user_id="u1"),
        _agent_scope="/root",
    )
    return stub, repo


@pytest.mark.asyncio
async def test_scene_read_returns_full_content():
    stub, repo = _engine_with()
    await repo.apply_batch([_scene("s1", "技术研究-Rust学习", content="完整经过")], [])

    result = await QueryLoopEngine._execute_scene_read(
        stub, {"name": "技术研究-Rust学习"},
    )
    assert result["status"] == "ok"
    assert result["content"] == "完整经过"
    assert result["heat"] == 1 and result["updated_at"] is not None


@pytest.mark.asyncio
async def test_scene_read_miss_lists_available_names():
    stub, repo = _engine_with()
    await repo.apply_batch([_scene("s1", "技术研究-Rust学习")], [])

    result = await QueryLoopEngine._execute_scene_read(stub, {"name": "编造的场景"})
    assert result["status"] == "not_found"
    assert result["available_scenes"] == ["技术研究-Rust学习"]


@pytest.mark.asyncio
async def test_scene_read_requires_name_and_is_rate_limited():
    stub, _ = _engine_with()
    assert (await QueryLoopEngine._execute_scene_read(stub, {}))["status"] == "error"

    limit = 3   # settings.l2_scene_read_max_calls 默认值
    for _ in range(limit):
        await QueryLoopEngine._execute_scene_read(stub, {"name": "随便"})
    exhausted = await QueryLoopEngine._execute_scene_read(stub, {"name": "随便"})
    assert exhausted["status"] == "exhausted"
    assert stub._scene_read_calls == limit
