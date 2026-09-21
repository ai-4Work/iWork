"""L2 召回：导航渲染与热度排序、预算丢弃、按名读取命中与未命中（设计文档 L2-3）。"""
from datetime import datetime, timezone

import pytest

from server.memory.l2.recall import L2RecallService, format_entry
from server.memory.l2.types import L2Scene
from server.storage.memory import InMemoryL2SceneRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _scene(sid, name, *, heat=1, summary="摘要", content="正文", updated_at=NOW):
    return L2Scene(
        id=sid, user_id="u1", agent_id="/root", name=name, summary=summary,
        content=content, heat=heat, created_at=NOW, updated_at=updated_at,
    )


async def _repo(*scenes) -> InMemoryL2SceneRepo:
    repo = InMemoryL2SceneRepo()
    if scenes:
        await repo.apply_batch(list(scenes), [])
    return repo


def test_format_entry_uses_flame_tiers():
    assert format_entry(_scene("s1", "a", heat=120)).startswith("🔥🔥 Scene: a")
    assert format_entry(_scene("s1", "a", heat=1000)).startswith("🔥🔥🔥🔥🔥 Scene: a")
    assert format_entry(_scene("s1", "a", heat=1)).startswith("· Scene: a")


@pytest.mark.asyncio
async def test_navigation_is_empty_without_scenes():
    svc = L2RecallService(await _repo())
    assert await svc.navigation_xml(user_id="u1", agent_id="/root") == ""


@pytest.mark.asyncio
async def test_navigation_sorted_by_heat_desc():
    repo = await _repo(
        _scene("s1", "低热", heat=1),
        _scene("s2", "高热", heat=300),
        _scene("s3", "中热", heat=50),
    )
    xml = await L2RecallService(repo).navigation_xml(user_id="u1", agent_id="/root")
    assert xml.startswith("<scene-navigation>") and xml.endswith("</scene-navigation>")
    assert xml.index("高热") < xml.index("中热") < xml.index("低热")


@pytest.mark.asyncio
async def test_navigation_lists_soft_deleted_scenes_nowhere():
    """被 merge 取代的场景不进导航。"""
    repo = await _repo(_scene("s1", "已合并"), _scene("s2", "现存"))
    await repo.apply_batch([], ["s1"])
    xml = await L2RecallService(repo).navigation_xml(user_id="u1", agent_id="/root")
    assert "现存" in xml and "已合并" not in xml


@pytest.mark.asyncio
async def test_navigation_drops_tail_over_budget():
    """超预算按热度降序丢尾部（与 L1 format_block 同款的严格口径）。"""
    repo = await _repo(
        _scene("s1", "高热", heat=300, summary="x" * 200),
        _scene("s2", "次热", heat=100, summary="x" * 200),
    )
    svc = L2RecallService(repo, nav_max_chars=400)
    xml = await svc.navigation_xml(user_id="u1", agent_id="/root")
    assert "高热" in xml and "次热" not in xml
    assert xml.startswith("<scene-navigation>") and xml.endswith("</scene-navigation>")


@pytest.mark.asyncio
async def test_navigation_can_be_emptied_by_a_tiny_budget():
    """预算小到一条都装不下时返回空串（不注入），而不是截半条目。"""
    repo = await _repo(_scene("s1", "高热", heat=300, summary="x" * 200))
    svc = L2RecallService(repo, nav_max_chars=50)
    assert await svc.navigation_xml(user_id="u1", agent_id="/root") == ""


@pytest.mark.asyncio
async def test_navigation_scope_isolation():
    repo = InMemoryL2SceneRepo()
    await repo.apply_batch([
        _scene("s1", "顶层场景"),
        L2Scene(id="s2", user_id="u1", agent_id="/root/m1", name="子场景",
                summary="s", content="c", created_at=NOW, updated_at=NOW),
    ], [])
    svc = L2RecallService(repo)
    top = await svc.navigation_xml(user_id="u1", agent_id="/root")
    sub = await svc.navigation_xml(user_id="u1", agent_id="/root/m1")
    assert "顶层场景" in top and "子场景" not in top
    assert "子场景" in sub and "顶层场景" not in sub


@pytest.mark.asyncio
async def test_read_scene_hits_by_exact_name():
    repo = await _repo(_scene("s1", "技术研究-Rust学习", content="完整正文"))
    got = await L2RecallService(repo).read_scene(
        user_id="u1", agent_id="/root", name="技术研究-Rust学习",
    )
    assert got is not None and got.content == "完整正文"


@pytest.mark.asyncio
async def test_read_scene_normalizes_padded_name():
    """模型常带上空格 / .md 后缀：规范化后重试一次，仍命中同一场景。"""
    repo = await _repo(_scene("s1", "技术研究-Rust学习", content="正文"))
    svc = L2RecallService(repo)
    for raw in ("技术研究 Rust学习.md", " 技术研究-Rust学习 "):
        got = await svc.read_scene(user_id="u1", agent_id="/root", name=raw)
        assert got is not None and got.id == "s1"


@pytest.mark.asyncio
async def test_read_scene_miss_returns_none_and_names_lists_available():
    repo = await _repo(_scene("s1", "技术研究-Rust学习"), _scene("s2", "日常生活-健康"))
    svc = L2RecallService(repo)
    assert await svc.read_scene(
        user_id="u1", agent_id="/root", name="根本不存在的场景",
    ) is None
    assert set(await svc.names(user_id="u1", agent_id="/root")) == {
        "技术研究-Rust学习", "日常生活-健康",
    }


@pytest.mark.asyncio
async def test_read_scene_does_not_see_soft_deleted():
    repo = await _repo(_scene("s1", "已合并"))
    await repo.apply_batch([], ["s1"])
    assert await L2RecallService(repo).read_scene(
        user_id="u1", agent_id="/root", name="已合并",
    ) is None


def test_guide_mentions_the_call_limit():
    from server.memory.l2.prompts import SCENE_TOOLS_GUIDE
    assert "scene_read" in SCENE_TOOLS_GUIDE
    assert "3 次" in SCENE_TOOLS_GUIDE


# ── 注入位置（稳定半边，不进用户消息前缀） ─────────────────────

@pytest.mark.asyncio
async def test_navigation_is_appended_after_the_stable_guides():
    """导航排在 system 最末（提示词缓存按前缀命中，易变内容必须放最后）。"""
    from uuid import uuid4
    from server.engine.context import ContextManager
    from server.storage.memory import InMemoryMessageRepo

    mgr = ContextManager(InMemoryMessageRepo())
    sid = uuid4()
    await mgr.append_text(sid, "user", "上次那个超时是怎么查的？")
    ctx = await mgr.build(
        sid, 1, "ask", "office",
        memory_guide_xml="<memory-tools-guide>L1</memory-tools-guide>",
        scene_nav_xml="<scene-navigation>\nS\n</scene-navigation>",
    )
    assert ctx.system_prompt.endswith("</scene-navigation>")
    assert ctx.system_prompt.index("</memory-tools-guide>") < ctx.system_prompt.index(
        "<scene-navigation>"
    )
    # 用户消息前缀仍然干净：导航不走动态半边
    assert ctx.messages[-1]["content"] == "上次那个超时是怎么查的？"


@pytest.mark.asyncio
async def test_empty_navigation_injects_nothing():
    from uuid import uuid4
    from server.engine.context import ContextManager
    from server.storage.memory import InMemoryMessageRepo

    mgr = ContextManager(InMemoryMessageRepo())
    sid = uuid4()
    await mgr.append_text(sid, "user", "hi")
    ctx = await mgr.build(sid, 1, "ask", "office", scene_nav_xml="")
    assert "scene-navigation" not in ctx.system_prompt
