"""10.5.3 offload + recall orchestration.

Unloading low-retention blocks to external memory and recalling them via TF-IDF
search. Keeps only a small in-window index line, replacing the full block body.
"""

from __future__ import annotations

from dataclasses import dataclass

from server.engine.artifact import normalize_artifact
from server.engine.info_block import InfoBlock, Precision
from server.engine.tfidf import TFIDFRetriever
from server.observability.logging import get_logger

logger = get_logger("iwork.offload")


# ── Retention scoring (four dimensions, weights from design doc) ──

_KEEP = 0.60
_DISCARD = 0.30


def retention_score(block, current_turn: int) -> float:
    """Four-dimension retention score in [0, 1].

    Weights: precision 0.45 / reference heat 0.30 / one-shot 0.15 /
    token cost 0.10.
    """
    precision_w = {
        Precision.CONFIRMED: 1.0,
        Precision.DERIVED: 0.6,
        Precision.PENDING: 0.4,
        Precision.OBSOLETE: 0.0,
    }.get(block.precision, 0.3)

    if block.last_referenced_turn < 0:
        heat = 0.0
    else:
        gap = max(0, current_turn - block.last_referenced_turn)
        heat = max(0.0, 1.0 - gap / 10.0)

    one_shot = 0.0 if block.is_one_shot else 1.0

    tokens = block.token_count or 0
    if tokens <= 200:
        cost = 1.0
    elif tokens <= 2000:
        cost = 0.7
    elif tokens <= 10000:
        cost = 0.4
    else:
        cost = 0.1

    return (
        0.45 * precision_w
        + 0.30 * heat
        + 0.15 * one_shot
        + 0.10 * cost
    )


def annotate_references(blocks: list[InfoBlock]) -> list[InfoBlock]:
    """Fill last_referenced_turn via syntax-layer identifier intersection.

    For each block, if any later block's extracted identifiers overlap, mark
    this block as referenced at that later turn. O(n^2) over a small window.
    """
    for i, blk in enumerate(blocks):
        ids = set(blk.extracted_ids or [])
        if not ids:
            continue
        ref_turn = -1
        for later in blocks[i + 1:]:
            if ids & set(later.extracted_ids or []):
                ref_turn = max(ref_turn, later.created_turn)
        if ref_turn > 0:
            blk.last_referenced_turn = ref_turn
    return blocks


# ── Persisted offloaded block (repo-neutral dataclass) ──

@dataclass
class OffloadedBlock:
    block_id: str
    turn: int
    label: str
    precision: str
    artifact: str = ""
    content: str = ""


def content_label(block) -> str:
    """Best-effort short label for a block, used as offload index pointer."""
    if getattr(block, "title", ""):
        return block.title
    if getattr(block, "artifact", ""):
        return normalize_artifact(block.artifact)
    content = (getattr(block, "content", "") or "").strip()
    return content[:100] if content else (getattr(block, "block_id", "") or "")[:8]


def _to_offloaded(block: InfoBlock) -> OffloadedBlock:
    return OffloadedBlock(
        block_id=block.block_id,
        turn=block.created_turn,
        label=content_label(block),
        precision=block.precision.value if hasattr(block.precision, "value") else str(block.precision),
        artifact=block.artifact or "",
        content=block.content or "",
    )


class OffloadStore:
    """Orchestrates externalizing, offloading, and recalling blocks.

    Depends on an OffloadedBlocksRepo (PG or in-memory) + a TFIDFRetriever.
    """

    def __init__(self, repo, retriever: TFIDFRetriever | None = None):
        self._repo = repo
        self._retriever = retriever or TFIDFRetriever()

    async def offload(self, session_id: str, blocks, current_turn: int):
        """Score blocks and offload the middle tier.

        Returns (remaining, offloaded). Low-score (<0.30) blocks are dropped,
        mid-tier (0.30-0.60) written to external memory, high (>=0.60) kept.
        """
        remaining: list[InfoBlock] = []
        offloaded: list[InfoBlock] = []
        for b in blocks:
            score = retention_score(b, current_turn)
            if score >= _KEEP:
                remaining.append(b)
            elif score >= _DISCARD:
                await self._repo.insert(session_id, _to_offloaded(b))
                offloaded.append(b)
            # else: < 0.30 discarded
        logger.info(
            "blocks_offloaded",
            total=len(blocks), kept=len(remaining),
            offloaded=len(offloaded), discarded=len(blocks) - len(remaining) - len(offloaded),
        )
        return remaining, offloaded

    async def list_offloaded(self, session_id: str, limit: int = 100) -> list:
        """List accumulated offloaded blocks for a session, ordered by turn."""
        rows = await self._repo.list_by_session(session_id)
        rows = sorted(rows, key=lambda r: getattr(r, "turn", 0) or 0)
        return rows[:limit]

    async def recall(self, session_id: str, query: str, top_k: int = 5) -> list[dict]:
        """TF-IDF recall of offloaded blocks for a session."""
        rows = await self._repo.list_by_session(session_id)
        if not rows:
            return []
        self._retriever.build(rows)
        by_id = {getattr(r, "block_id", None): r for r in rows}
        out: list[dict] = []
        for bid, score in self._retriever.search(query, top_k):
            row = by_id.get(bid)
            if row is None or score <= 0:
                continue
            out.append({
                "block_id": bid,
                "label": getattr(row, "label", ""),
                "content": getattr(row, "content", ""),
                "score": score,
            })
        return out


async def build_offload_index(store: "OffloadStore", session_id: str) -> str:
    """Build the accumulated `[可用外部记忆]` index from offloaded blocks.

    Queries the offloaded_blocks table (ordered by turn, capped at 100).  Returns
    "" when nothing has been offloaded so the caller can skip injecting an empty
    header.
    """
    rows = await store.list_offloaded(session_id)
    if not rows:
        return ""
    lines = ["[可用外部记忆]"]
    for r in rows:
        lines.append(f"· {getattr(r, 'label', '')} (turn{getattr(r, 'turn', 0)})")
    return "\n".join(lines)
