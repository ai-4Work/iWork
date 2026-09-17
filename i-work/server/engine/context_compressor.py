"""Context compression engine — 10.5.1 (sliding window) + 10.5.2 (two-granularity compression).

Compression is a runtime view transform: raw conversation history lives in
PostgreSQL as the source of truth; compressed messages are computed in-memory
per turn and never persisted.
"""

from __future__ import annotations

import json as _json
import re as _re
from dataclasses import dataclass, field

from server.engine.artifact import extract_artifact, normalize_artifact
from server.engine.info_block import ContentType, InfoBlock, Precision
from server.engine.token_counter import TokenCounter
from server.engine.offload import annotate_references, build_offload_index
from server.observability.logging import get_logger

logger = get_logger("iwork.compressor")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class CompressionConfig:
    model_context_limit: int = 65536
    raw_turns: int = 3
    compressed_turns: int = 7
    compress_threshold: float = 0.80
    recheck_threshold: float = 0.75
    enable_llm_compression: bool = True
    min_group_tokens: int = 500


@dataclass
class CompressionReport:
    compressed: bool = False
    original_tokens: int = 0
    compressed_tokens: int = 0
    compression_ratio: float = 1.0
    layers: dict = field(default_factory=dict)
    reason: str = ""


# ---------------------------------------------------------------------------
# Compression prompt (used in Phase 4)
# ---------------------------------------------------------------------------

COMPRESSION_PROMPT = """\
请对以下对话块做结构化压缩，输出 JSON：

要求：
1. 为每个信息点标注精度：CONFIRMED / DERIVED / PENDING / OBSOLETE
2. 工具返回只保留关键字段（金额、状态、ID），丢弃冗余 JSON
3. 错误尝试只保留最终成功的方法，失败路径可丢弃，标记为 OBSOLETE
4. 如果这些块描述的任务阶段已达成或已放弃，标记 phase_status: "closed"；
   仍在进行中则标记 "ongoing"
5. 标注每个 key_fact 引用了哪些旧信息块（block_id），无引用则为空数组
6. 输出格式（只输出 JSON，不要 markdown 代码块）：

{
  "title": "5-10字主题词",
  "summary": "结构化摘要文本",
  "phase_status": "closed",
  "key_facts": [
    {"fact": "具体事实", "precision": "CONFIRMED", "source_round": 5, "references": []}
  ],
  "decisions": [],
  "pending_items": [],
  "compression_ratio": 0.15
}

原始对话块（%s 个块，约 %s tokens）：
%s

压缩摘要："""


# ---------------------------------------------------------------------------
# Granularity-1 single-block summary prompts
# ---------------------------------------------------------------------------

TOOL_RESULT_SUMMARY_PROMPT = """\
请将下面的工具返回结果压缩为一段简洁摘要，只保留关键字段（金额、状态、ID、结果结论），丢弃冗余 JSON 与重复日志。

工具返回（约 {tokens} tokens）：
{content}

摘要："""

THINKING_SUMMARY_PROMPT = """\
请将下面的推理过程压缩为一句简洁摘要，只保留最终结论和关键推理步骤。

推理过程（约 {tokens} tokens）：
{content}

摘要："""


# ---------------------------------------------------------------------------
# ContextCompressor
# ---------------------------------------------------------------------------

class ContextCompressor:
    """Per-session context compression engine.

    Instantiated once per QueryLoopEngine (i.e. per session).
    Stateless across turns — the only mutable state is the optional
    compress() result cache to avoid redundant LLM calls within the same turn.
    """

    def __init__(
        self,
        llm_client,          # LLMClient — used only for LLM-based merging (Phase 4)
        token_counter: TokenCounter,
        config: CompressionConfig | None = None,
        offload_store=None,  # OffloadStore | None — 10.5.3 external memory
    ) -> None:
        self._llm = llm_client
        self._tc = token_counter
        self._config = config or CompressionConfig()
        self._offload_store = offload_store
        # Simple cache: key=(turn, hash of first 3 messages) → compressed result
        self._cache: tuple[tuple[int, int], list[dict]] | None = None

    # ── Phase 2: Message → InfoBlock parsing ──────────────────────────

    def _parse_to_infoblocks(self, messages: list[dict], current_turn: int) -> list[InfoBlock]:
        """Parse OpenAI-format messages into InfoBlocks grouped by conversation turn.

        Heuristic turn detection:
        - Each user message starts a new turn.
        - Tool results belong to the same turn as their preceding assistant message.
        - Assistant text belongs to the same turn as the most recent user message.
        """
        blocks: list[InfoBlock] = []
        turn = 0
        pending_artifact: str = ""
        # tool_call_id -> (artifact, tool_name) so tool results are attributed
        # to the exact call that produced them, not the most recent one.
        pending_tool_calls: dict[str, tuple[str, str]] = {}

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = _json.dumps(content, ensure_ascii=False)
            elif not isinstance(content, str):
                content = str(content)

            if role == "user":
                turn += 1
                pending_artifact = ""
                pending_tool_calls = {}
                blk = InfoBlock(
                    content=content,
                    content_type=ContentType.USER_INPUT,
                    precision=Precision.CONFIRMED,
                    created_turn=turn,
                    token_count=self._tc.estimate(content),
                )
                blocks.append(blk)

            elif role == "assistant":
                tc = msg.get("tool_calls") or []
                reasoning = msg.get("reasoning_content", "") or ""
                text = content or ""
                if tc:
                    for t in tc:
                        func = t.get("function", {})
                        t_name = func.get("name", "")
                        try:
                            t_input = _json.loads(func.get("arguments", "{}"))
                        except _json.JSONDecodeError:
                            t_input = {}
                        artifact = extract_artifact(t_name, t_input)
                        call_id = t.get("id", "")
                        if call_id:
                            pending_tool_calls[call_id] = (artifact, t_name)
                        pending_artifact = artifact
                    # text/thinking inherit the single tool_call's artifact;
                    # with ≥2 tool_calls they map to no single artifact → None.
                    text_artifact = pending_artifact if len(tc) == 1 else ""
                    text_tool_name = tc[0].get("function", {}).get("name", "") if len(tc) == 1 else None

                    if text.strip():
                        blocks.append(InfoBlock(
                            artifact=text_artifact,
                            content=text,
                            content_type=ContentType.ASSISTANT_TEXT,
                            precision=Precision.DERIVED,
                            created_turn=turn,
                            tool_name=text_tool_name,
                            token_count=self._tc.estimate(text),
                        ))
                    elif reasoning:
                        # thinking is discarded when visible text is present
                        blocks.append(self._make_thinking_block(reasoning, text_artifact, turn))
                else:
                    # Pure text assistant response (thinking discarded when text present)
                    if text.strip():
                        blocks.append(InfoBlock(
                            artifact=pending_artifact,
                            content=content,
                            content_type=ContentType.ASSISTANT_TEXT,
                            precision=Precision.DERIVED,
                            created_turn=turn,
                            token_count=self._tc.estimate(content),
                        ))
                    elif reasoning:
                        blocks.append(self._make_thinking_block(reasoning, pending_artifact, turn))

            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                artifact, tool_name_str = pending_tool_calls.get(tc_id, ("", ""))
                blk = InfoBlock(
                    artifact=artifact,
                    content=content,
                    content_type=ContentType.TOOL_RESULT,
                    precision=Precision.DERIVED,
                    created_turn=turn,
                    tool_name=tool_name_str,
                    is_one_shot=_is_one_shot(tool_name_str),
                    token_count=self._tc.estimate(content),
                    extracted_ids=list(_extract_identifiers(content)),
                )
                blocks.append(blk)

        return blocks

    # ── Phase 3: Granularity-1 single-block compression ──

    def _make_thinking_block(self, reasoning: str, artifact: str, turn: int) -> InfoBlock:
        return InfoBlock(
            artifact=artifact,
            content=reasoning,
            content_type=ContentType.THINKING,
            precision=Precision.DERIVED,
            created_turn=turn,
            token_count=self._tc.estimate(reasoning),
        )

    async def _compress_single_block(self, block: InfoBlock) -> InfoBlock:
        """Token-size three-tier granularity-1 compression.

        tool_result: <200 原样；200~10000 LLM 单块摘要；>10000 原样留给 10.5.3 卸载。
        thinking (无 text 且 artifact=None): LLM 单块摘要；有 artifact 则留给粒度2。
        """
        if block.compressed:
            return block

        if block.content_type == ContentType.TOOL_RESULT:
            tokens = block.token_count or self._tc.estimate(block.content)
            if tokens < 200:
                return block
            if tokens <= 10000:
                return await self._llm_summarize_block(block, TOOL_RESULT_SUMMARY_PROMPT)
            return block

        if block.content_type == ContentType.THINKING:
            if block.artifact:
                return block  # 随粒度2 合并
            return await self._llm_summarize_block(block, THINKING_SUMMARY_PROMPT)

        return block

    async def _llm_summarize_block(
        self, block: InfoBlock, prompt_template: str, new_content_type: ContentType | None = None,
    ) -> InfoBlock:
        """LLM single-block summary. Failure is non-blocking — returns original."""
        tokens = block.token_count or self._tc.estimate(block.content)
        prompt = prompt_template.format(tokens=tokens, content=block.content[:8000])
        try:
            summary = (await self._call_haiku_for_compression(prompt)).strip()
        except Exception as exc:
            logger.warning("single_block_compression_failed", block_id=block.block_id, error=str(exc))
            return block
        if not summary:
            return block
        return InfoBlock(
            block_id=block.block_id,
            artifact=block.artifact,
            title=block.title,
            content=summary,
            content_type=new_content_type or block.content_type,
            precision=block.precision,
            created_turn=block.created_turn,
            last_referenced_turn=block.last_referenced_turn,
            token_count=self._tc.estimate(summary),
            tool_name=block.tool_name,
            is_one_shot=block.is_one_shot,
            extracted_ids=block.extracted_ids,
            compressed=True,
        )

    # ── Phase 4: Granularity-2 cross-block merging ──

    def _group_by_artifact(self, blocks: list[InfoBlock]) -> list[list[InfoBlock]]:
        """Group blocks by normalized artifact, regardless of adjacency.

        Blocks with empty artifact or from user input form their own
        single-element group and will not trigger merging.  Groups are ordered
        by the first occurrence of any member to preserve chronology.
        """
        singles: list[tuple[int, InfoBlock]] = []
        buckets: dict[str, list[InfoBlock]] = {}
        bucket_pos: dict[str, int] = {}

        for idx, blk in enumerate(blocks):
            if blk.content_type == ContentType.USER_INPUT or not blk.artifact:
                singles.append((idx, blk))
                continue
            norm = normalize_artifact(blk.artifact)
            if norm not in buckets:
                buckets[norm] = [blk]
                bucket_pos[norm] = idx
            else:
                buckets[norm].append(blk)

        positioned: list[tuple[int, list[InfoBlock]]] = [(i, [b]) for i, b in singles]
        for norm, group in buckets.items():
            positioned.append((bucket_pos[norm], group))
        positioned.sort(key=lambda item: item[0])
        return [group for _, group in positioned]

    async def _summarize_group(self, group: list[InfoBlock]) -> list[InfoBlock]:
        """Merge a same-artifact group into a single SUMMARY block via LLM.

        The LLM annotates each key_fact with a precision; OBSOLETE facts are
        dropped, and the merged block's precision is the weakest among the
        surviving facts (OBSOLETE if everything is superseded, PENDING if any
        unresolved item remains).  Single-block groups are left untouched.

        Failure is non-blocking — on any error the original blocks are returned.
        """
        if len(group) < 2:
            return group

        artifact = normalize_artifact(group[0].artifact)
        total_tokens = sum(b.token_count for b in group)
        if total_tokens < self._config.min_group_tokens:
            return group

        blocks_text = _format_blocks_for_prompt(group)

        prompt = COMPRESSION_PROMPT % (len(group), total_tokens, blocks_text)

        try:
            response_text = await self._call_haiku_for_compression(prompt)
            # Strip markdown code fences if present
            response_text = _re.sub(r"^```(?:json)?\s*", "", response_text.strip())
            response_text = _re.sub(r"\s*```$", "", response_text)
            data = _json.loads(response_text)

            key_facts = data.get("key_facts", []) or []
            pending = data.get("pending_items", []) or []
            if pending:
                merged_precision = Precision.PENDING
            elif key_facts:
                precisions = [
                    _PRECISION_BY_VALUE.get(f.get("precision", ""), Precision.DERIVED)
                    for f in key_facts
                ]
                survivors = [p for p in precisions if p != Precision.OBSOLETE]
                merged_precision = _merge_precision(survivors) if survivors else Precision.OBSOLETE
            else:
                merged_precision = _merge_precision([b.precision for b in group])

            merged = InfoBlock(
                artifact=group[0].artifact,
                title=data.get("title", ""),
                content=_json.dumps(data, ensure_ascii=False),
                content_type=ContentType.SUMMARY,
                precision=merged_precision,
                created_turn=group[0].created_turn,
                last_referenced_turn=max(b.last_referenced_turn for b in group),
                token_count=self._tc.estimate(_json.dumps(data, ensure_ascii=False)),
            )
            logger.info(
                "group_summarized",
                artifact=artifact,
                blocks_before=len(group),
                blocks_after=1,
                precision=merged_precision.value,
            )
            return [merged]
        except Exception as exc:
            logger.warning(
                "compression_llm_failed",
                artifact=artifact,
                error=str(exc),
            )
            return group

    async def _call_haiku_for_compression(self, prompt: str) -> str:
        """Call the compression LLM (Haiku or equivalent cheap model).

        Uses a single-turn non-streaming chat completion.
        """
        chunks: list[str] = []
        async for chunk in self._llm.stream(
            messages=[{"role": "user", "content": prompt}],
            system="你是一个上下文压缩引擎。只输出 JSON，不要解释。",
            tools=None,
            tool_choice="none",
        ):
            if chunk.type in ("text",) and chunk.delta:
                chunks.append(chunk.delta)
        return "".join(chunks)

    # ── Phase 5: Three-layer stack + compress() entry point ──

    def _blocks_to_messages(self, blocks: list[InfoBlock]) -> list[dict]:
        """Convert InfoBlocks back to OpenAI-format messages.

        Summary blocks become system-prefixed user messages to inject
        compressed context without confusing the assistant role.
        """
        messages: list[dict] = []
        for blk in blocks:
            if blk.content_type == ContentType.SUMMARY:
                messages.append({
                    "role": "user",
                    "content": f"[压缩历史] {blk.content}",
                })
            elif blk.content_type == ContentType.USER_INPUT:
                messages.append({"role": "user", "content": blk.content})
            elif blk.content_type == ContentType.ASSISTANT_TEXT:
                messages.append({"role": "assistant", "content": blk.content})
            elif blk.content_type == ContentType.TOOL_RESULT:
                messages.append({
                    "role": "tool",
                    "tool_call_id": blk.block_id,
                    "content": blk.content,
                })
            elif blk.content_type == ContentType.THINKING:
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": blk.content,
                })
        return messages

    def _get_mode_threshold(self, mode: str) -> float:
        """Task-type-aware compression threshold."""
        if mode == "ask":
            return 0.55
        return self._config.compress_threshold

    async def compress(
        self,
        session_id: str,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
        current_turn: int = 0,
        mode: str = "build",
    ) -> tuple[list[dict], CompressionReport]:
        """Main entry point. Build the three-layer compressed context.

        Returns (compressed_messages, report).  If no compression is needed,
        returns the original messages unchanged with report.compressed=False.
        """
        # 1. Estimate
        total_est = self._tc.estimate_context(messages, system_prompt, tools)
        limit = self._config.model_context_limit
        threshold = self._get_mode_threshold(mode)

        if total_est < limit * threshold:
            return messages, CompressionReport(
                compressed=False,
                original_tokens=total_est,
                compressed_tokens=total_est,
                compression_ratio=1.0,
                reason="under_threshold",
            )

        # 2. Parse → group by turn
        blocks = self._parse_to_infoblocks(messages, current_turn)
        turns = _group_by_turn(blocks)
        if len(turns) <= self._config.raw_turns:
            return messages, CompressionReport(
                compressed=False,
                original_tokens=total_est,
                compressed_tokens=total_est,
                compression_ratio=1.0,
                reason="too_few_turns",
            )

        # 3. Three-layer split
        raw_end = len(turns)
        raw_start = max(0, raw_end - self._config.raw_turns)
        mid_start = max(0, raw_start - self._config.compressed_turns)

        raw_blocks = [b for turn in turns[raw_start:] for b in turn]
        mid_blocks = [b for turn in turns[mid_start:raw_start] for b in turn]
        old_blocks = [b for turn in turns[:mid_start] for b in turn]

        # 4. Granularity 1: single-block compression on mid + old
        mid_blocks = [await self._compress_single_block(b) for b in mid_blocks]
        old_blocks = [await self._compress_single_block(b) for b in old_blocks]

        all_blocks = old_blocks + mid_blocks + raw_blocks
        compressed_msgs = self._blocks_to_messages(all_blocks)
        new_est = self._tc.estimate_context(compressed_msgs, system_prompt, tools)

        # 5. Granularity 2: LLM cross-block merging for old layer if still over threshold
        if new_est > limit * self._config.recheck_threshold and self._config.enable_llm_compression:
            if old_blocks:
                grouped = self._group_by_artifact(old_blocks)
                old_merged: list[InfoBlock] = []
                for group in grouped:
                    result = await self._summarize_group(group)
                    old_merged.extend(result)
                all_blocks = old_merged + mid_blocks + raw_blocks
                compressed_msgs = self._blocks_to_messages(all_blocks)
                new_est = self._tc.estimate_context(compressed_msgs, system_prompt, tools)

        # 6. 10.5.3 offload: still over threshold → offload low-retention blocks
        if new_est > limit * self._config.recheck_threshold:
            all_blocks = await self._offload_low_score_blocks(session_id, all_blocks, current_turn)
            compressed_msgs = self._blocks_to_messages(all_blocks)
            new_est = self._tc.estimate_context(compressed_msgs, system_prompt, tools)

        return compressed_msgs, CompressionReport(
            compressed=True,
            original_tokens=total_est,
            compressed_tokens=new_est,
            compression_ratio=new_est / max(total_est, 1),
            layers={
                "raw": len(raw_blocks),
                "compressed": len(mid_blocks),
                "meta_summary": len(old_blocks),
            },
            reason="threshold_exceeded",
        )

    async def _offload_low_score_blocks(
        self, session_id: str, blocks: list[InfoBlock], current_turn: int,
    ) -> list[InfoBlock]:
        """10.5.3: offload low-retention blocks, replace with an index line."""
        if self._offload_store is None:
            return blocks
        blocks = annotate_references(blocks)
        remaining, offloaded = await self._offload_store.offload(
            session_id, blocks, current_turn,
        )
        if offloaded:
            index_text = await build_offload_index(self._offload_store, session_id)
            if index_text:
                remaining.insert(0, InfoBlock(
                    content=index_text,
                    content_type=ContentType.SUMMARY,
                    precision=Precision.DERIVED,
                    created_turn=current_turn,
                    token_count=self._tc.estimate(index_text),
                ))
        return remaining


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_one_shot(tool_name: str) -> bool:
    """Tools whose results are typically large and never re-read."""
    return tool_name in ("bash", "web_fetch", "web_search", "glob", "grep")


def _extract_identifiers(text: str) -> set[str]:
    """Extract stable identifiers from text for reference matching (Phase 1 of reference detection)."""
    ids: set[str] = set()
    patterns = [
        r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
        r'(?:/[\w.-]+)+(?:\.\w+)?',
    ]
    for pat in patterns:
        for m in _re.finditer(pat, text):
            ids.add(m.group(0))
    return ids


def _group_by_turn(blocks: list[InfoBlock]) -> list[list[InfoBlock]]:
    """Group InfoBlocks by their created_turn field."""
    turns: list[list[InfoBlock]] = []
    current: list[InfoBlock] = []
    current_turn = -1
    for blk in blocks:
        if blk.created_turn != current_turn:
            if current:
                turns.append(current)
            current = [blk]
            current_turn = blk.created_turn
        else:
            current.append(blk)
    if current:
        turns.append(current)
    return turns


def _format_blocks_for_prompt(blocks: list[InfoBlock]) -> str:
    """Format InfoBlocks as text for the compression prompt."""
    lines: list[str] = []
    for blk in blocks:
        ct = blk.content_type.value
        precision = blk.precision.value
        lines.append(
            f"[{ct} | {precision} | turn {blk.created_turn} | {blk.block_id[:8]}]\n"
            f"{blk.content[:2000]}\n"  # truncate per block to keep prompt reasonable
        )
    return "\n".join(lines)


_PRECISION_BY_VALUE = {p.value: p for p in Precision}


def _merge_precision(precisions: list[Precision]) -> Precision:
    """Conservative merge: the weakest precision wins."""
    order = {Precision.OBSOLETE: 0, Precision.DERIVED: 1, Precision.PENDING: 2, Precision.CONFIRMED: 3}
    return min(precisions, key=lambda p: order.get(p, 1))
