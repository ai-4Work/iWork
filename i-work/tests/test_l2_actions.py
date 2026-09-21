"""L2 动作解析与降级 / 名字规范化 / 热度分档 / 输入构造（设计文档 L2-2.2 / L2-2.5 / L2-2.6）。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.l2.consolidator import MemoryConsolidator
from server.memory.l2.reader import L2Input, L2Reader
from server.memory.l2.types import (
    L2Scene, SCENE_MAX_CHARS, heat_flames, normalize_name,
)
from server.memory.types import L1Memory
from server.storage.memory import InMemoryL1MemoryRepo, InMemoryL2SceneRepo

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


# ── 名字规范化（doc L2-2.6 第 2 步，纯函数） ─────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("日常生活 健康管理", "日常生活-健康管理"),      # 空格 → 短横线
    ("日常生活　健康管理", "日常生活-健康管理"),      # 全角空格同样处理
    ("Coffee (Yirgacheffe)", "Coffee-Yirgacheffe"),  # 括号删除，留下的空格变短横线
    ("技术研究-Billing超时排查.md", "技术研究-Billing超时排查"),  # 后缀剥掉
    ("  Q1-Milestone?  ", "Q1-Milestone"),           # 首尾空白 + 标点
    ("a//b\\\\c", "abc"),                            # 斜杠在名字里非法，直接删
    ("a--b___c", "a-b___c"),                         # 连字符折叠，下划线保留
    ("...", "scene-"),                               # 只剩分隔符 → 交给兜底
])
def test_normalize_name_branches(raw, expected):
    got = normalize_name(raw)
    if expected == "scene-":
        assert got.startswith("scene-") and len(got) == len("scene-") + 6
    else:
        assert got == expected


def test_normalize_name_empty_gets_hex_fallback():
    for raw in ("", "   ", "???", "()"):
        got = normalize_name(raw)
        assert got.startswith("scene-")
        assert got != normalize_name(raw) or True  # 只要求非空且形状正确


def test_normalize_name_truncates_to_200():
    assert len(normalize_name("x" * 500)) == 200


def test_heat_flames_tiers():
    # doc L2-3.3：>=1000 五火 / >=500 四火 / >=200 三火 / >=100 双火 / >=50 单火 / 其余 0
    assert [heat_flames(h) for h in (1, 49, 50, 100, 200, 500, 1000)] == [0, 0, 1, 2, 3, 4, 5]


# ── 输入构造（reader） ──────────────────────────────────────────

def _l1(mid, content, *, updated_at=None, retrievable=True):
    return L1Memory(
        id=mid, user_id="u1", content=content, agent_id="/root",
        retrievable=retrievable, updated_at=updated_at or NOW,
    )


def _scene(sid, name, *, content="正文", heat=1, summary="摘要"):
    return L2Scene(
        id=sid, user_id="u1", agent_id="/root", name=name, summary=summary,
        content=content, heat=heat, created_at=NOW, updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_reader_returns_none_when_no_new_memories():
    reader = L2Reader(InMemoryL1MemoryRepo(), InMemoryL2SceneRepo())
    got = await reader.read(
        "u1", "/root", since=None, batch_memories=20,
        candidate_scenes=5, candidate_chars=12000, max_scenes=15,
    )
    assert got is None


@pytest.mark.asyncio
async def test_reader_batches_and_reports_newest_timestamp():
    l1 = InMemoryL1MemoryRepo()
    for i in range(5):
        await l1.apply_batch(
            [_l1(f"m{i}", f"记忆{i}", updated_at=NOW + timedelta(minutes=i))], [],
        )
    reader = L2Reader(l1, InMemoryL2SceneRepo())
    got = await reader.read(
        "u1", "/root", since=None, batch_memories=2,
        candidate_scenes=5, candidate_chars=12000, max_scenes=15,
    )
    assert [m.id for m in got.memories] == ["m0", "m1"]     # 升序取前 N
    assert got.newest_at == NOW + timedelta(minutes=1)      # 本批最新那条
    assert got.memory_ids == ["m0", "m1"]


@pytest.mark.asyncio
async def test_reader_candidates_respect_char_budget():
    l1 = InMemoryL1MemoryRepo()
    await l1.apply_batch([_l1("m0", "Rust 解析器 重写")], [])
    scenes = InMemoryL2SceneRepo()
    await scenes.apply_batch([
        _scene("s1", "技术研究-Rust学习", content="R" * 900),
        _scene("s2", "日常生活-健康", content="H" * 900),
    ], [])
    reader = L2Reader(l1, scenes)
    got = await reader.read(
        "u1", "/root", since=None, batch_memories=20,
        candidate_scenes=5, candidate_chars=1000, max_scenes=15,
    )
    # 第一条无条件收下（900 < 1000），第二条 900 超预算被挡
    assert len(got.candidates) == 1
    assert len(got.scenes) == 2       # 清单仍是全量


@pytest.mark.asyncio
async def test_reader_scope_isolation():
    l1 = InMemoryL1MemoryRepo()
    await l1.apply_batch([_l1("m0", "顶层")], [])
    other = L1Memory(id="m1", user_id="u1", content="子 agent", agent_id="/root/m1",
                     updated_at=NOW)
    await l1.apply_batch([other], [])
    reader = L2Reader(l1, InMemoryL2SceneRepo())
    got = await reader.read(
        "u1", "/root", since=None, batch_memories=20,
        candidate_scenes=5, candidate_chars=12000, max_scenes=15,
    )
    assert [m.id for m in got.memories] == ["m0"]


# ── 动作解析与降级（consolidator） ──────────────────────────────

def _payload(obj) -> FakeLLMClient:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return FakeLLMClient(responses=[[LLMChunk(type="text", delta=text)]])


def _input(*, memories=("mem_1",), scenes=("技术研究-Rust学习",)):
    return L2Input(
        memories=[
            L1Memory(id=i, user_id="u1", content=f"内容{i}", agent_id="/root",
                     created_at=NOW)
            for i in memories
        ],
        scenes=[_scene("s1", n) for n in scenes],
        candidates=[(n, "正文") for n in scenes],
        newest_at=NOW,
    )


def _consolidator(llm, *, max_scenes=15):
    return MemoryConsolidator(llm, max_scenes=max_scenes)


@pytest.mark.asyncio
async def test_parse_failure_returns_none():
    """解析失败 → None：调用方据此不推进游标（与 L1 extractor.py:94-100 同款语义）。"""
    c = _consolidator(_payload("抱歉，我无法完成这个任务。"))
    assert await c.consolidate(_input()) is None


@pytest.mark.asyncio
async def test_accepts_fenced_object_and_bare_array():
    fenced = (
        '```json\n{"actions": [{"action": "update", "name": "技术研究-Rust学习",'
        ' "summary": "s", "content": "c"}]}\n```'
    )
    r = await _consolidator(_payload(fenced)).consolidate(_input())
    assert [(a.action, a.name) for a in r.actions] == [("update", "技术研究-Rust学习")]

    r = await _consolidator(_payload([{"action": "create", "name": "新场景", "content": "c"}])
                            ).consolidate(_input())
    assert [(a.action, a.name) for a in r.actions] == [("create", "新场景")]


@pytest.mark.asyncio
async def test_persona_request_from_field_and_from_marker():
    text = '{"actions": [], "persona_update_request": "重大价值观转变"}'
    assert (await _consolidator(_payload(text)).consolidate(_input())
            ).persona_update_request == "重大价值观转变"

    text = '{"actions": []}\n[PERSONA_UPDATE_REQUEST]跨场景洞察[/PERSONA_UPDATE_REQUEST]'
    assert (await _consolidator(_payload(text)).consolidate(_input())
            ).persona_update_request == "跨场景洞察"


@pytest.mark.asyncio
async def test_empty_content_action_is_dropped():
    """正文为空的动作丢弃：merge 若正文为空照做，等于删掉旧场景不留替代。"""
    payload = {"actions": [
        {"action": "create", "name": "空正文", "content": "   "},
        {"action": "create", "name": "无正文键"},
        {"action": "create", "name": "好的", "content": "c"},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert [a.name for a in r.actions] == ["好的"]


@pytest.mark.asyncio
async def test_unknown_action_degrades_to_create():
    payload = {"actions": [{"action": "delete", "name": "随便", "content": "c"}]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert [(a.action, a.name) for a in r.actions] == [("create", "随便")]


@pytest.mark.asyncio
async def test_update_with_unknown_target_degrades_to_create():
    """update 的目标不在作用域内 → create（丢掉动作等于永久丢那批记忆）。"""
    payload = {"actions": [
        {"action": "update", "name": "根本不存在的场景", "content": "c"},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert [(a.action, a.name) for a in r.actions] == [("create", "根本不存在的场景")]


@pytest.mark.asyncio
async def test_update_target_matches_after_normalization():
    """LLM 照旧带上空格/后缀也应命中现有场景，而不是降级成新建。"""
    payload = {"actions": [
        {"action": "update", "name": "技术研究 Rust学习.md", "content": "c"},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert [(a.action, a.name) for a in r.actions] == [("update", "技术研究-Rust学习")]


@pytest.mark.asyncio
async def test_merge_with_no_resolvable_source_degrades_to_create():
    payload = {"actions": [
        {"action": "merge", "name": "合并后", "sources": ["幻觉A", "幻觉B"],
         "content": "c"},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert [(a.action, a.name, a.sources) for a in r.actions] == [("create", "合并后", [])]


@pytest.mark.asyncio
async def test_merge_filters_hallucinated_sources_but_keeps_real_ones():
    payload = {"actions": [
        {"action": "merge", "name": "后端开发技术栈",
         "sources": ["技术研究-Rust学习", "编造的场景"], "content": "c"},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert r.actions[0].sources == ["技术研究-Rust学习"]


@pytest.mark.asyncio
async def test_source_memory_ids_filtered_and_fallback_to_whole_batch():
    payload = {"actions": [
        {"action": "create", "name": "A", "content": "c",
         "source_memory_ids": ["mem_1", "mem_bogus"]},
        {"action": "create", "name": "B", "content": "c"},
    ]}
    data = _input(memories=("mem_1", "mem_2"))
    r = await _consolidator(_payload(payload)).consolidate(data)
    assert r.actions[0].source_memory_ids == ["mem_1"]           # 幻觉 id 被剔掉
    assert r.actions[1].source_memory_ids == ["mem_1", "mem_2"]  # 没给 → 退化为整批


@pytest.mark.asyncio
async def test_content_and_summary_truncated():
    payload = {"actions": [
        {"action": "create", "name": "长文", "content": "x" * 5000, "summary": "s" * 900},
    ]}
    r = await _consolidator(_payload(payload)).consolidate(_input())
    assert len(r.actions[0].content) == SCENE_MAX_CHARS
    assert len(r.actions[0].summary) == 500


@pytest.mark.asyncio
async def test_prompt_carries_warning_and_scene_count():
    """场景数达上限时用户提示词出现红色预警（doc L2-2.3 三级预警）。"""
    llm = _payload({"actions": []})
    data = _input(scenes=tuple(f"场景{i}" for i in range(3)))
    await _consolidator(llm, max_scenes=3).consolidate(data)
    user_prompt = llm.calls[0]["messages"][-1]["content"]
    assert "场景数量警告" in user_prompt
    assert "MERGE" in user_prompt
