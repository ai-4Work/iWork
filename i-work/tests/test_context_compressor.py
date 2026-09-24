"""B~E 模块（消息解析 / 粒度1 / 粒度2）+ L 模块（compress 端到端）组件测试。"""

import json
from uuid import uuid4

import pytest

from server.engine.info_block import InfoBlock, ContentType, Precision
from server.engine.context_compressor import ContextCompressor, CompressionConfig
from server.engine.token_counter import TokenCounter
from server.engine.offload import OffloadStore
from server.engine.tfidf import TFIDFRetriever
from server.storage.memory import InMemoryOffloadedBlocksRepo
from server.llm.client import FakeLLMClient, LLMChunk


def make_block(**kw) -> InfoBlock:
    defaults = dict(
        content_type=ContentType.TOOL_RESULT,
        precision=Precision.DERIVED,
        created_turn=1,
        token_count=1000,
    )
    defaults.update(kw)
    return InfoBlock(**defaults)


def make_comp(llm=None, config=None, offload_store=None) -> ContextCompressor:
    return ContextCompressor(
        llm_client=llm or FakeLLMClient(),
        token_counter=TokenCounter(provider="deepseek"),
        config=config or CompressionConfig(),
        offload_store=offload_store,
    )


def _merge_json(**overrides) -> str:
    data = {
        "title": "top50",
        "summary": "合并摘要",
        "phase_status": "closed",
        "key_facts": [],
        "decisions": [],
        "pending_items": [],
        "compression_ratio": 0.3,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def _tool_call(cid, name, args: dict) -> dict:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class _RaisingLLM:
    """stream 抛异常的假客户端，用于测 fail-open。"""

    async def stream(self, messages, system, tools=None, tool_choice=None):
        if False:
            yield  # pragma: no cover - 标记为 async generator
        raise RuntimeError("boom")


# ══ B. 消息 → InfoBlock 解析（_parse_to_infoblocks）══

def test_parse_user_starts_new_turn():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    users = [b for b in blocks if b.content_type == ContentType.USER_INPUT]
    assert [u.created_turn for u in users] == [1, 2]
    assert all(u.precision == Precision.CONFIRMED for u in users)


def test_parse_tool_result_by_id():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("call_1", "read_file", {"file_path": "a.py"}),
            _tool_call("call_2", "bash", {"command": "psql -f b.sql"}),
        ]},
        {"role": "tool", "tool_call_id": "call_2", "content": "b result"},
        {"role": "tool", "tool_call_id": "call_1", "content": "a result"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    tool_results = [b for b in blocks if b.content_type == ContentType.TOOL_RESULT]
    # 不因消息顺序错配：call_2 → b.sql，call_1 → a.py
    assert [b.artifact for b in tool_results] == ["b.sql", "a.py"]


def test_parse_thinking_discarded_with_text():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "可见文本", "reasoning_content": "推理"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    types = [b.content_type for b in blocks]
    assert ContentType.ASSISTANT_TEXT in types
    assert ContentType.THINKING not in types


def test_parse_text_inherits_pending_artifact():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("call_1", "write_file", {"file_path": "top50.sql"}),
        ]},
        {"role": "assistant", "content": "写好了"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    text_blocks = [b for b in blocks if b.content_type == ContentType.ASSISTANT_TEXT]
    assert text_blocks[0].artifact == "top50.sql"


def test_parse_multi_toolcalls_artifact_empty():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "多工具", "tool_calls": [
            _tool_call("c1", "read_file", {"file_path": "a.py"}),
            _tool_call("c2", "read_file", {"file_path": "b.py"}),
        ]},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    text_blocks = [b for b in blocks if b.content_type == ContentType.ASSISTANT_TEXT]
    assert text_blocks[0].artifact == ""


def test_parse_is_one_shot():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("c1", "bash", {"command": "ls"}),
            _tool_call("c2", "read_file", {"file_path": "a.py"}),
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "bash result"},
        {"role": "tool", "tool_call_id": "c2", "content": "read result"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    by_tool = {b.tool_name: b for b in blocks if b.content_type == ContentType.TOOL_RESULT}
    assert by_tool["bash"].is_one_shot is True
    assert by_tool["read_file"].is_one_shot is False


def test_parse_extracted_ids():
    comp = make_comp()
    content = "结果在 /src/utils.py 和 a1b2c3d4-5678-9abc-def0-1234567890ab"
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("c1", "read_file", {"file_path": "a.py"}),
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": content},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)
    tool_result = [b for b in blocks if b.content_type == ContentType.TOOL_RESULT][0]
    ids = tool_result.extracted_ids
    assert "/src/utils.py" in ids
    assert any(i.startswith("a1b2c3d4") for i in ids)


def test_parse_json_decode_error():
    comp = make_comp()
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "not-json"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    blocks = comp._parse_to_infoblocks(messages, 0)  # 不抛异常
    tool_result = [b for b in blocks if b.content_type == ContentType.TOOL_RESULT][0]
    assert tool_result.artifact == ""


# ══ C. 粒度1 单块压缩（_compress_single_block）══

@pytest.mark.asyncio
async def test_compress_single_block_three_tiers_boundaries():
    fake = FakeLLMClient(responses=[
        [LLMChunk(type="text", delta="摘要：关键结果")],
        [LLMChunk(type="text", delta="摘要：关键结果")],
    ])
    comp = make_comp(llm=fake)

    small = make_block(token_count=100, content="x" * 100)
    assert await comp._compress_single_block(small) is small  # <200 不压

    low_mid = make_block(token_count=200, content="x" * 200)
    out = await comp._compress_single_block(low_mid)
    assert out.compressed and "摘要" in out.content  # 200 边界 → 摘要

    high_mid = make_block(token_count=10000, content="x" * 10000)
    out = await comp._compress_single_block(high_mid)
    assert out.compressed and "摘要" in out.content  # 10000 边界 → 摘要

    big = make_block(token_count=10001, content="x" * 10001)
    assert await comp._compress_single_block(big) is big  # >10000 不压


@pytest.mark.asyncio
async def test_compress_thinking_artifact_split():
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta="推理：最终结论")]])
    comp = make_comp(llm=fake)

    no_artifact = make_block(content_type=ContentType.THINKING, artifact="", token_count=500)
    out = await comp._compress_single_block(no_artifact)
    assert out.compressed and "推理" in out.content

    with_artifact = make_block(content_type=ContentType.THINKING, artifact="file:top50", token_count=500)
    out = await comp._compress_single_block(with_artifact)
    assert not out.compressed  # 有 artifact → 留粒度2


@pytest.mark.asyncio
async def test_compress_single_block_fail_open_empty():
    # LLM 返回空摘要 → 返回原块
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta="")]])
    comp = make_comp(llm=fake)
    block = make_block(token_count=5000)
    assert await comp._compress_single_block(block) is block


@pytest.mark.asyncio
async def test_compress_single_block_fail_open_exception():
    comp = make_comp(llm=_RaisingLLM())
    block = make_block(token_count=5000)
    assert await comp._compress_single_block(block) is block


# ══ D. 粒度2 分组（_group_by_artifact）══

def test_group_by_artifact_normalized():
    comp = make_comp()
    blocks = [
        make_block(block_id="b1", artifact="file:top50_v1.sql"),
        make_block(block_id="b2", artifact="file:schema.sql"),
        make_block(block_id="b3", artifact="file:top50_v3.sql"),
    ]
    groups = comp._group_by_artifact(blocks)
    assert len(groups) == 2
    assert [b.block_id for b in groups[0]] == ["b1", "b3"]  # top50 归一化同组，时序在前
    assert [b.block_id for b in groups[1]] == ["b2"]


def test_group_by_artifact_user_empty_singletons():
    comp = make_comp()
    blocks = [
        make_block(block_id="u", content_type=ContentType.USER_INPUT),
        make_block(block_id="e", artifact=""),
        make_block(block_id="a", artifact="file:a"),
    ]
    groups = comp._group_by_artifact(blocks)
    assert len(groups) == 3
    assert all(len(g) == 1 for g in groups)


# ══ E. 粒度2 合并（_summarize_group）══

@pytest.mark.asyncio
async def test_summarize_group_skip():
    comp = make_comp()
    single = [make_block(token_count=1000)]
    assert await comp._summarize_group(single) is single

    small = [make_block(token_count=100), make_block(token_count=100)]
    assert await comp._summarize_group(small) is small  # total < 500 跳过


@pytest.mark.asyncio
async def test_summarize_group_precision_obsolete():
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta=_merge_json(
        key_facts=[{"fact": "f1", "precision": "OBSOLETE"}, {"fact": "f2", "precision": "OBSOLETE"}],
    ))]])
    comp = make_comp(llm=fake)
    group = [make_block(artifact="file:top50", token_count=300), make_block(artifact="file:top50", token_count=300)]
    out = await comp._summarize_group(group)
    assert len(out) == 1
    assert out[0].precision == Precision.OBSOLETE


@pytest.mark.asyncio
async def test_summarize_group_precision_pending():
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta=_merge_json(
        key_facts=[{"fact": "f", "precision": "CONFIRMED"}],
        pending_items=["未决"],
    ))]])
    comp = make_comp(llm=fake)
    group = [make_block(artifact="file:top50", token_count=300), make_block(artifact="file:top50", token_count=300)]
    out = await comp._summarize_group(group)
    assert len(out) == 1
    assert out[0].precision == Precision.PENDING


@pytest.mark.asyncio
async def test_summarize_group_precision_weakest():
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta=_merge_json(
        key_facts=[{"fact": "f1", "precision": "DERIVED"}, {"fact": "f2", "precision": "CONFIRMED"}],
    ))]])
    comp = make_comp(llm=fake)
    group = [make_block(artifact="file:top50", token_count=300), make_block(artifact="file:top50", token_count=300)]
    out = await comp._summarize_group(group)
    assert len(out) == 1
    assert out[0].precision == Precision.DERIVED


@pytest.mark.asyncio
async def test_summarize_group_llm_failure():
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta="not-json")]])
    comp = make_comp(llm=fake)
    group = [make_block(artifact="file:a", token_count=300), make_block(artifact="file:a", token_count=300)]
    out = await comp._summarize_group(group)
    assert len(out) == 2  # 原组返回


@pytest.mark.asyncio
async def test_summarize_group_fence_strip_title():
    raw = "```json\n" + _merge_json(title="数据分布") + "\n```"
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta=raw)]])
    comp = make_comp(llm=fake)
    group = [make_block(artifact="file:a", token_count=300), make_block(artifact="file:a", token_count=300)]
    out = await comp._summarize_group(group)
    assert len(out) == 1
    assert out[0].content_type == ContentType.SUMMARY
    assert out[0].title == "数据分布"


# ══ L. compress() 端到端（三层 + 阈值）══

@pytest.mark.asyncio
async def test_compress_under_threshold():
    comp = make_comp(config=CompressionConfig(model_context_limit=100000))
    messages = [{"role": "user", "content": "你好"}]
    msgs, report = await comp.compress(str(uuid4()), messages, current_turn=0, mode="build")
    assert report.compressed is False
    assert report.reason == "under_threshold"
    assert msgs == messages


@pytest.mark.asyncio
async def test_compress_too_few_turns():
    comp = make_comp(config=CompressionConfig(model_context_limit=10, raw_turns=10))
    messages = [
        {"role": "user", "content": "x" * 100},
        {"role": "assistant", "content": "x" * 100},
        {"role": "user", "content": "x" * 100},
    ]
    msgs, report = await comp.compress(str(uuid4()), messages, current_turn=0, mode="build")
    assert report.compressed is False
    assert report.reason == "too_few_turns"


@pytest.mark.asyncio
async def test_compress_three_layer_split():
    config = CompressionConfig(model_context_limit=500, raw_turns=3, compressed_turns=7)
    comp = make_comp(config=config)
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"问题{i} " + "x" * 100})
        messages.append({"role": "assistant", "content": f"回答{i} " + "y" * 100})
    msgs, report = await comp.compress(str(uuid4()), messages, current_turn=15, mode="build")
    assert report.compressed is True
    # 每轮 2 块（user + assistant text），raw=3 轮 / compressed=7 轮 / meta_summary=5 轮
    assert report.layers == {"raw": 6, "compressed": 14, "meta_summary": 10}


@pytest.mark.asyncio
async def test_compress_granularity2_old_layer_only():
    config = CompressionConfig(model_context_limit=1000, raw_turns=3, compressed_turns=7, min_group_tokens=500)
    fake = FakeLLMClient(responses=[[LLMChunk(type="text", delta=_merge_json())]])
    comp = make_comp(llm=fake, config=config)
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            _tool_call(f"c{i}", "read_file", {"file_path": "top50.sql"}),
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 350})
    msgs, report = await comp.compress(str(uuid4()), messages, current_turn=15, mode="build")
    summary_msgs = [m for m in msgs if m.get("role") == "user" and m.get("content", "").startswith("[压缩历史]")]
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(summary_msgs) == 1   # old 层 5 块合并为 1 个 SUMMARY
    assert len(tool_msgs) == 10     # mid 7 + raw 3，mid 层未合并


@pytest.mark.asyncio
async def test_compress_triggers_offload():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    config = CompressionConfig(model_context_limit=1000, raw_turns=3, compressed_turns=7)
    comp = make_comp(config=config, offload_store=store)
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"q{i}"})
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            _tool_call(f"c{i}", "read_file", {"file_path": f"f{i}.py"}),
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 300})
    sid = str(uuid4())
    msgs, report = await comp.compress(sid, messages, current_turn=15, mode="build")
    assert report.compressed is True
    assert any("可用外部记忆" in m.get("content", "") for m in msgs)
    assert len(await repo.list_by_session(sid)) > 0  # 有块被卸载落库


def _eight_turns() -> list[dict]:
    """8 轮 ≈ 736 token 的一段历史（见下面两条阈值用例）。"""
    messages: list[dict] = []
    for _ in range(8):
        messages.append({"role": "user", "content": "x" * 100})
        messages.append({"role": "assistant", "content": "y" * 100})
    return messages


@pytest.mark.asyncio
async def test_compress_ask_vs_build_threshold():
    comp = make_comp(config=CompressionConfig(model_context_limit=1000))
    messages = _eight_turns()
    # 8 轮 total ≈ 736，落在 550(0.55) 与 800(0.80) 之间
    _, report_ask = await comp.compress(str(uuid4()), messages, current_turn=8, mode="ask")
    _, report_build = await comp.compress(str(uuid4()), messages, current_turn=8, mode="build")
    assert report_ask.compressed is True
    assert report_build.compressed is False
    assert report_build.reason == "under_threshold"


@pytest.mark.asyncio
async def test_compress_explicit_threshold_overrides_window_ratio():
    """配了绝对阈值就以它为准 —— 包括不再乘 mode 的 0.55/0.80。"""
    comp = make_comp(config=CompressionConfig(model_context_limit=1000))
    messages = _eight_turns()  # ≈ 736 token

    # 阈值 600 < 736：build 模式（默认 0.80×1000=800 会放过）也压
    _, low = await comp.compress(
        str(uuid4()), messages, current_turn=8, mode="build",
        compress_threshold_tokens=600,
    )
    assert low.compressed is True

    # 阈值 900 > 736：ask 模式（默认 0.55×1000=550 会压）也不压
    _, high = await comp.compress(
        str(uuid4()), messages, current_turn=8, mode="ask",
        compress_threshold_tokens=900,
    )
    assert high.compressed is False
    assert high.reason == "under_threshold"


def test_blocks_to_messages():
    comp = make_comp()
    blocks = [
        InfoBlock(block_id="s1", content_type=ContentType.SUMMARY, content="摘要1"),
        InfoBlock(block_id="u1", content_type=ContentType.USER_INPUT, content="问题"),
        InfoBlock(block_id="a1", content_type=ContentType.ASSISTANT_TEXT, content="回答"),
        InfoBlock(block_id="t1", content_type=ContentType.TOOL_RESULT, content="结果"),
        InfoBlock(block_id="h1", content_type=ContentType.THINKING, content="推理"),
    ]
    msgs = comp._blocks_to_messages(blocks)
    assert msgs[0] == {"role": "user", "content": "[压缩历史] 摘要1"}
    assert msgs[1] == {"role": "user", "content": "问题"}
    assert msgs[2] == {"role": "assistant", "content": "回答"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "结果"}
    assert msgs[4] == {"role": "assistant", "content": "", "reasoning_content": "推理"}


@pytest.mark.asyncio
async def test_compress_report_fields():
    config = CompressionConfig(model_context_limit=500, raw_turns=3, compressed_turns=7)
    comp = make_comp(config=config)
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"问题{i} " + "x" * 100})
        messages.append({"role": "assistant", "content": f"回答{i} " + "y" * 100})
    msgs, report = await comp.compress(str(uuid4()), messages, current_turn=15, mode="build")
    assert report.compressed is True
    assert report.reason == "threshold_exceeded"
    assert 0 < report.compression_ratio <= 1
    assert report.compressed_tokens <= report.original_tokens
    assert set(report.layers) == {"raw", "compressed", "meta_summary"}
