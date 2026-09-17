"""10.5.3 offload + recall + granularity-1 compression tests."""

import pytest
from uuid import uuid4

from server.engine.info_block import InfoBlock, ContentType, Precision
from server.engine.tfidf import TFIDFRetriever, tokenize
from server.engine.offload import (
    retention_score, OffloadStore, OffloadedBlock, annotate_references, content_label,
    build_offload_index,
)
from server.storage.memory import InMemoryOffloadedBlocksRepo, InMemoryMessageRepo
from server.engine.context_compressor import ContextCompressor, CompressionConfig
from server.engine.token_counter import TokenCounter
from server.engine.context import ContextManager
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


# ── TF-IDF ──

def test_tokenize_chinese_bigram():
    toks = tokenize("数据库")
    assert "数" in toks and "据" in toks and "库" in toks
    assert "数据" in toks and "据库" in toks


def test_tfidf_retrieves_relevant_block():
    blocks = [
        type("B", (), {"block_id": "a", "content": "top50 SQL 查询 最终版本"})(),
        type("B", (), {"block_id": "b", "content": "无关的 天气 预报 内容"})(),
    ]
    retriever = TFIDFRetriever(blocks)
    results = retriever.search("top50 的 SQL 版本", top_k=2)
    assert results, "expected at least one hit"
    assert results[0][0] == "a"


# ── retention_score ──

def test_retention_score_tiers():
    kept = make_block(
        precision=Precision.CONFIRMED, last_referenced_turn=5,
        is_one_shot=False, token_count=100,
    )
    offloaded = make_block(
        precision=Precision.DERIVED, last_referenced_turn=-1,
        is_one_shot=False, token_count=5000,
    )
    discarded = make_block(
        precision=Precision.OBSOLETE, last_referenced_turn=-1,
        is_one_shot=True, token_count=20000,
    )
    assert retention_score(kept, 5) >= 0.60
    assert 0.30 <= retention_score(offloaded, 5) < 0.60
    assert retention_score(discarded, 5) < 0.30


def test_retention_score_precision_weights():
    # 固定 heat=0 / one_shot=1.0 / cost=1.0，只变化精度
    def score(p):
        return retention_score(make_block(
            precision=p, last_referenced_turn=-1,
            is_one_shot=False, token_count=100,
        ), current_turn=5)

    assert score(Precision.CONFIRMED) == pytest.approx(0.70)
    assert score(Precision.DERIVED) == pytest.approx(0.52)
    assert score(Precision.PENDING) == pytest.approx(0.43)
    assert score(Precision.OBSOLETE) == pytest.approx(0.25)


def test_retention_score_unknown_precision_fallback():
    # 未知精度 → 权重 0.3（.get 默认值）
    block = make_block(precision="NONSENSE", last_referenced_turn=-1,
                       is_one_shot=False, token_count=100)
    assert retention_score(block, 5) == pytest.approx(0.45 * 0.3 + 0.15 + 0.10)


def test_retention_score_heat_decay():
    # CONFIRMED + one_shot=1.0 + token=100 → 无 heat 时基线 = 0.70
    def score(last_ref, turn):
        return retention_score(make_block(
            precision=Precision.CONFIRMED, last_referenced_turn=last_ref,
            is_one_shot=False, token_count=100,
        ), current_turn=turn)

    assert score(5, 5) == pytest.approx(1.00)    # gap0 → heat=1.0
    assert score(0, 5) == pytest.approx(0.85)    # gap5 → heat=0.5
    assert score(5, 15) == pytest.approx(0.70)   # gap10 → heat=0
    assert score(-1, 15) == pytest.approx(0.70)  # 负值短路 → heat=0


def test_retention_score_one_shot():
    # CONFIRMED + heat=0 + cost=1.0 → 基线 = 0.60
    assert retention_score(make_block(
        precision=Precision.CONFIRMED, last_referenced_turn=-1,
        is_one_shot=False, token_count=100,
    ), 5) == pytest.approx(0.70)
    assert retention_score(make_block(
        precision=Precision.CONFIRMED, last_referenced_turn=-1,
        is_one_shot=True, token_count=100,
    ), 5) == pytest.approx(0.55)


def test_retention_score_token_cost():
    # CONFIRMED + heat=0 + one_shot=1.0 → 基线 = 0.60
    def score(tokens):
        return retention_score(make_block(
            precision=Precision.CONFIRMED, last_referenced_turn=-1,
            is_one_shot=False, token_count=tokens,
        ), 5)

    assert score(100) == pytest.approx(0.70)     # ≤200 → cost 1.0
    assert score(2000) == pytest.approx(0.67)    # ≤2000 → cost 0.7
    assert score(10000) == pytest.approx(0.64)   # ≤10000 → cost 0.4
    assert score(10001) == pytest.approx(0.61)   # >10000 → cost 0.1


def test_retention_score_exact_tiers():
    keep = make_block(precision=Precision.CONFIRMED, last_referenced_turn=5,
                      is_one_shot=False, token_count=100)
    mid = make_block(precision=Precision.DERIVED, last_referenced_turn=-1,
                     is_one_shot=False, token_count=5000)
    drop = make_block(precision=Precision.OBSOLETE, last_referenced_turn=-1,
                      is_one_shot=True, token_count=20000)
    assert retention_score(keep, 5) == pytest.approx(1.00)
    assert retention_score(mid, 5) == pytest.approx(0.46)
    assert retention_score(drop, 5) == pytest.approx(0.01)


# ── OffloadStore ──

@pytest.mark.asyncio
async def test_offload_and_recall_roundtrip():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    sid = str(uuid4())

    keep = make_block(
        block_id="keep", precision=Precision.CONFIRMED,
        last_referenced_turn=3, is_one_shot=False, token_count=100,
    )
    mid = make_block(
        block_id="mid", precision=Precision.DERIVED,
        last_referenced_turn=-1, is_one_shot=False, token_count=5000,
        title="top50 版本", content="top50 SQL 的最终版本 SELECT * FROM top50",
        created_turn=1,
    )
    drop = make_block(
        block_id="drop", precision=Precision.OBSOLETE,
        is_one_shot=True, token_count=20000,
    )

    remaining, offloaded = await store.offload(sid, [keep, mid, drop], current_turn=3)
    assert [b.block_id for b in remaining] == ["keep"]
    assert [b.block_id for b in offloaded] == ["mid"]

    results = await store.recall(sid, "top50 SQL 版本", top_k=5)
    assert results, "expected recall to hit the offloaded block"
    assert results[0]["block_id"] == "mid"
    assert "top50" in results[0]["content"]


@pytest.mark.asyncio
async def test_offload_discard_not_persisted():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    sid = str(uuid4())
    keep = make_block(block_id="keep", precision=Precision.CONFIRMED,
                      last_referenced_turn=3, is_one_shot=False, token_count=100)
    mid = make_block(block_id="mid", precision=Precision.DERIVED,
                     last_referenced_turn=-1, is_one_shot=False, token_count=5000)
    drop = make_block(block_id="drop", precision=Precision.OBSOLETE,
                      last_referenced_turn=-1, is_one_shot=True, token_count=20000)
    await store.offload(sid, [keep, mid, drop], current_turn=3)
    stored = await repo.list_by_session(sid)
    assert [b.block_id for b in stored] == ["mid"]  # drop 被丢弃，不落库


@pytest.mark.asyncio
async def test_recall_empty_repo():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    assert await store.recall(str(uuid4()), "top50", top_k=5) == []


@pytest.mark.asyncio
async def test_recall_irrelevant_filtered():
    repo = InMemoryOffloadedBlocksRepo()
    sid = str(uuid4())
    await repo.insert(sid, OffloadedBlock(
        block_id="mid", turn=1, label="天气", precision="DERIVED",
        artifact="", content="天气预报 内容",
    ))
    store = OffloadStore(repo, TFIDFRetriever())
    # 查询词与已存内容完全不相关 → TF-IDF 得分为 0 → 被过滤
    assert await store.recall(sid, "数据库", top_k=5) == []


@pytest.mark.asyncio
async def test_build_offload_index_empty():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    assert await build_offload_index(store, str(uuid4())) == ""


@pytest.mark.asyncio
async def test_build_offload_index_order():
    repo = InMemoryOffloadedBlocksRepo()
    sid = str(uuid4())
    await repo.insert(sid, OffloadedBlock(block_id="b", turn=2, label="later", precision="DERIVED"))
    await repo.insert(sid, OffloadedBlock(block_id="a", turn=1, label="earlier", precision="DERIVED"))
    store = OffloadStore(repo, TFIDFRetriever())
    idx = await build_offload_index(store, sid)
    lines = idx.split("\n")
    assert lines[0] == "[可用外部记忆]"
    assert lines[1] == "· earlier (turn1)"
    assert lines[2] == "· later (turn2)"


def test_annotate_references_fills_turn():
    a = make_block(block_id="a", created_turn=1, extracted_ids=["/src/a.py"])
    b = make_block(block_id="b", created_turn=3, extracted_ids=["/src/a.py", "/src/b.py"])
    annotate_references([a, b])
    assert a.last_referenced_turn == 3
    assert b.last_referenced_turn == -1


def test_annotate_references_no_ids_and_latest():
    a = make_block(block_id="a", created_turn=1, extracted_ids=[])
    b = make_block(block_id="b", created_turn=2, extracted_ids=["/src/utils.py"])
    c = make_block(block_id="c", created_turn=5, extracted_ids=["/src/utils.py", "x"])
    annotate_references([a, b, c])
    assert a.last_referenced_turn == -1   # 无 id → 自然冷
    assert b.last_referenced_turn == 5    # 被 c 引用，取最新 turn
    assert c.last_referenced_turn == -1   # 最新块无后续引用


def test_content_label_prefers_title():
    assert content_label(make_block(title="top50", content="x" * 500)) == "top50"


def test_content_label_fallback_sources():
    assert content_label(make_block(title="top50 版本", content="x" * 500)) == "top50 版本"
    assert content_label(make_block(artifact="top50_v2.sql")) == "top50"
    assert content_label(make_block(content="x" * 500)) == "x" * 100
    assert content_label(make_block(block_id="abcdefgh123", content="")) == "abcdefgh"


# ── Granularity-1 three-tier ──

@pytest.mark.asyncio
async def test_compress_single_block_three_tiers():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    fake = FakeLLMClient(responses=[
        [LLMChunk(type="text", delta="摘要：关键结果")],   # 200~10000 摘要
        [LLMChunk(type="text", delta="推理：最终结论")],    # thinking 摘要
    ])
    comp = ContextCompressor(
        llm_client=fake,
        token_counter=TokenCounter(provider="deepseek"),
        offload_store=store,
    )
    sid = str(uuid4())

    small = make_block(token_count=100, content="x" * 100)
    out = await comp._compress_single_block(small)
    assert out is small  # <200 不压

    mid = make_block(token_count=5000, content="y" * 5000)
    out = await comp._compress_single_block(mid)
    assert out.compressed and "摘要" in out.content

    big = make_block(token_count=20000, content="z" * 20000, title="big")
    out = await comp._compress_single_block(big)
    assert out is big  # >10000 不压，留给 10.5.3 卸载
    assert len(await repo.list_by_session(sid)) == 0  # 压缩阶段不再外部化

    thinking = make_block(
        content_type=ContentType.THINKING, artifact="",
        token_count=500, content="t" * 500,
    )
    out = await comp._compress_single_block(thinking)
    assert out.compressed and "推理" in out.content

    thinking_artifact = make_block(
        content_type=ContentType.THINKING, artifact="file:top50",
        token_count=500, content="t" * 500,
    )
    out = await comp._compress_single_block(thinking_artifact)
    assert not out.compressed  # 有 artifact → 留给粒度2


# ── 卸载闭环（_offload_low_score_blocks）──

@pytest.mark.asyncio
async def test_offload_low_score_blocks_index():
    repo = InMemoryOffloadedBlocksRepo()
    store = OffloadStore(repo, TFIDFRetriever())
    comp = ContextCompressor(
        llm_client=FakeLLMClient(),
        token_counter=TokenCounter(provider="deepseek"),
        offload_store=store,
    )
    sid = str(uuid4())

    keep = make_block(
        block_id="keep", precision=Precision.CONFIRMED,
        last_referenced_turn=2, is_one_shot=False, token_count=100,
    )
    mid = make_block(
        block_id="mid", precision=Precision.DERIVED,
        last_referenced_turn=-1, is_one_shot=False, token_count=5000,
        title="top50", content="top50 SQL 最终版本",
    )
    remaining = await comp._offload_low_score_blocks(sid, [keep, mid], current_turn=2)
    ids = [b.block_id for b in remaining]
    assert "mid" not in ids
    assert any("可用外部记忆" in b.content for b in remaining)
    stored = await repo.list_by_session(sid)
    assert len(stored) == 1 and stored[0].block_id == "mid"


# ── 被动召回注入（ContextManager.build）──

@pytest.mark.asyncio
async def test_passive_recall_injection():
    repo = InMemoryMessageRepo()
    sid = uuid4()
    await repo.append_message(sid, {"role": "user", "content": "top50 SQL 的最终版本是什么"})

    offload_repo = InMemoryOffloadedBlocksRepo()
    await offload_repo.insert(str(sid), OffloadedBlock(
        block_id="mid", turn=1, label="top50", precision="DERIVED",
        artifact="file:top50", content="SELECT * FROM top50 WHERE id = 1",
    ))
    store = OffloadStore(offload_repo, TFIDFRetriever())

    ctx_mgr = ContextManager(repo)
    ctx = await ctx_mgr.build(sid, 1, "build", "code", offload_store=store)
    assert "[召回记忆]" in ctx.system_prompt

    # 无 offload_store 时行为不变
    ctx2 = await ctx_mgr.build(sid, 1, "build", "code")
    assert "[召回记忆]" not in ctx2.system_prompt
