"""抽取：JSON 解析、质量门、上限截断、时间轨迹（设计文档 L1-2.4）。"""
import json

import pytest

from server.llm.client import FakeLLMClient, LLMChunk
from server.memory.extractor import MemoryExtractor, parse_json_payload
from server.memory.l0 import L0Batch, L0Message


# ── 解析 ───────────────────────────────────────────────────────

def test_parse_plain_json():
    assert parse_json_payload('[{"scene_name": "x"}]') == [{"scene_name": "x"}]


def test_parse_strips_json_fence():
    raw = '```json\n[{"a": 1}]\n```'
    assert parse_json_payload(raw) == [{"a": 1}]


def test_parse_ignores_trailing_prose():
    assert parse_json_payload('[{"a": 1}]\n\n以上就是抽取结果。') == [{"a": 1}]


@pytest.mark.parametrize("raw", ["", "抱歉，我不知道", "```\n不是 JSON\n```"])
def test_parse_failure_returns_none(raw):
    assert parse_json_payload(raw) is None


# ── 抽取 ───────────────────────────────────────────────────────

def _batch(*contents, timestamps=None):
    msgs = [
        L0Message(id=f"id{i}", role="user", content=c, sequence=i + 1,
                  timestamp=(timestamps or {}).get(f"id{i}"))
        for i, c in enumerate(contents)
    ]
    return L0Batch(new_messages=msgs, read_cursor=len(msgs))


def _llm(payload) -> FakeLLMClient:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return FakeLLMClient(responses=[[LLMChunk(type="text", delta=text)]])


def _scene(memories, scene="我在和用户调时区"):
    return [{"scene_name": scene, "memories": memories}]


@pytest.mark.asyncio
async def test_extract_builds_memory_with_scene_and_lineage():
    llm = _llm(_scene([{
        "content": "用户的时区是 UTC+8", "type": "episodic", "priority": 80,
        "source_message_ids": ["id0"],
        "metadata": {"activity_start_time": "2026-08-18", "activity_end_time": "2026-08-19"},
    }]))
    result = await MemoryExtractor(llm).extract(
        _batch("我在 UTC+8", timestamps={"id0": "2026-08-18T10:00:00Z"}),
        user_id="u1", agent_id="/root", session_id="s1",
    )
    assert result is not None
    assert result.scene_name == "我在和用户调时区"
    memory = result.memories[0]
    assert memory.content == "用户的时区是 UTC+8"
    assert memory.type == "episodic" and memory.priority == 80
    assert memory.source_message_ids == ["id0"]
    assert memory.agent_id == "/root" and memory.session_id == "s1"
    assert memory.id.startswith("m_")
    # 时间轨迹：来源消息时间 + 活动起止时间，并集排序
    assert memory.timestamps == [
        "2026-08-18", "2026-08-18T10:00:00Z", "2026-08-19",
    ]


@pytest.mark.asyncio
async def test_priority_floor_drops_low_value_memories():
    llm = _llm(_scene([
        {"content": "低分事件", "type": "episodic", "priority": 30},
        {"content": "低分画像", "type": "persona", "priority": 40},
        {"content": "低分规则", "type": "instruction", "priority": 60},
        {"content": "硬规则", "type": "instruction", "priority": -1},
        {"content": "合格事件", "type": "episodic", "priority": 60},
    ]))
    result = await MemoryExtractor(llm).extract(_batch("随便聊聊"), user_id="u1")
    assert [m.content for m in result.memories] == ["硬规则", "合格事件"]


@pytest.mark.asyncio
async def test_unknown_type_and_bad_priority_are_dropped():
    llm = _llm(_scene([
        {"content": "类型不对", "type": "fact", "priority": 90},
        {"content": "priority 不是数", "type": "episodic", "priority": "很高"},
        {"content": "   ", "type": "episodic", "priority": 90},
        {"content": "正常", "type": "episodic", "priority": 70},
    ]))
    result = await MemoryExtractor(llm).extract(_batch("聊聊"), user_id="u1")
    assert [m.content for m in result.memories] == ["正常"]


@pytest.mark.asyncio
async def test_per_run_cap_truncates():
    llm = _llm(_scene([
        {"content": f"第 {i} 条", "type": "episodic", "priority": 80}
        for i in range(15)
    ]))
    result = await MemoryExtractor(llm, max_per_run=10).extract(
        _batch("聊聊"), user_id="u1",
    )
    assert len(result.memories) == 10


@pytest.mark.asyncio
async def test_parse_failure_returns_none():
    llm = _llm("我无法完成这次抽取。")
    assert await MemoryExtractor(llm).extract(_batch("聊聊"), user_id="u1") is None


@pytest.mark.asyncio
async def test_empty_batch_is_a_noop():
    result = await MemoryExtractor(_llm("[]")).extract(
        L0Batch(), user_id="u1", previous_scene="上一个场景",
    )
    assert result.memories == [] and result.scene_name == "上一个场景"


@pytest.mark.asyncio
async def test_scene_name_falls_back_to_previous_when_absent():
    llm = _llm(_scene([{"content": "事实", "type": "episodic", "priority": 80}], scene=""))
    result = await MemoryExtractor(llm).extract(
        _batch("聊聊"), user_id="u1", previous_scene="延续中的场景",
    )
    assert result.scene_name == "延续中的场景"
