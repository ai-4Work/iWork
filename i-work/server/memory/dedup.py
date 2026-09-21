"""候选召回 + 冲突判断（设计文档 L1-2.5 / L1-2.6）。

候选召回是**算法**不是 LLM：TF-IDF 检索当前作用域内 retrievable 的记忆，
每条新记忆取 top-5，按阈值过滤；两条边界 —— 永不跨作用域、排除同批新记忆自己。
冲突判断是**一次批量 LLM 调用**，四种动作：store / update / merge / skip。
没有独立的删除动作：删除是 update/merge 的副作用。

池为空（或检索无命中）时直接全部 store —— 对应文档三级降级的第 3 档"跳过去重"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from server.memory.extractor import call_json, parse_json_payload
from server.memory.prompts import DEDUP_SYSTEM_PROMPT, render_dedup_user_prompt
from server.memory.types import MEMORY_TYPES, L1Memory
from server.observability.logging import get_logger

logger = get_logger("iwork.memory")

ACTION_STORE = "store"
ACTION_UPDATE = "update"
ACTION_MERGE = "merge"
ACTION_SKIP = "skip"
ACTIONS = (ACTION_STORE, ACTION_UPDATE, ACTION_MERGE, ACTION_SKIP)


@dataclass
class Decision:
    """一条新记忆的落地决策。store/skip 时其余字段无意义。"""

    memory: L1Memory
    action: str = ACTION_STORE
    targets: list[L1Memory] = field(default_factory=list)
    content: str | None = None
    type: str | None = None
    priority: int | None = None
    timestamps: list[str] | None = None

    @property
    def supersedes(self) -> list[str]:
        """被本决策取代（软删）的旧记忆 id。store/skip 为空。"""
        if self.action not in (ACTION_UPDATE, ACTION_MERGE):
            return []
        return [t.id for t in self.targets]


class ConflictResolver:
    def __init__(self, llm, *, top_k: int = 5, min_score: float = 0.3):
        self._llm = llm
        self._top_k = top_k
        self._min_score = min_score

    async def resolve(
        self, new_memories: list[L1Memory], retriever,
    ) -> list[Decision]:
        if not new_memories:
            return []

        pool: dict[str, L1Memory] = {}
        batch_ids = {m.id for m in new_memories}
        pairs: list[tuple[L1Memory, list[str]]] = []
        for memory in new_memories:
            hits = [
                (cand, score) for cand, score in retriever.search(memory.content, self._top_k)
                # 排除同批：不和自己这一批的新记忆冲突（doc L1-2.5 边界二）
                if score >= self._min_score and cand.id not in batch_ids
            ]
            candidate_ids = []
            for cand, _ in hits:
                pool[cand.id] = cand
                candidate_ids.append(cand.id)
            pairs.append((memory, candidate_ids))

        if not pool:
            # 无检索能力或无相似候选：直接全部新增
            return [Decision(memory=m, action=ACTION_STORE) for m in new_memories]

        pool_list = list(pool.values())
        raw = await call_json(
            self._llm,
            DEDUP_SYSTEM_PROMPT,
            render_dedup_user_prompt(pool_list, pairs),
        )
        payload = parse_json_payload(raw)
        if not isinstance(payload, list):
            # 去重失败不比抽取失败：记忆本身是好数据，按 store 落地即可。
            # 下一轮这批行已在池里，会被重新判定并 merge —— 自愈，不丢数据。
            logger.warning("l1_dedup_parse_failed", raw_head=raw[:200])
            return [Decision(memory=m, action=ACTION_STORE) for m in new_memories]
        return self._to_decisions(new_memories, payload, pool)

    @staticmethod
    def _to_decisions(
        new_memories: list[L1Memory], payload: list, pool: dict[str, L1Memory],
    ) -> list[Decision]:
        by_id: dict[str, dict] = {}
        for row in payload:
            if isinstance(row, dict) and row.get("record_id"):
                by_id[str(row["record_id"])] = row

        decisions: list[Decision] = []
        for memory in new_memories:
            row = by_id.get(memory.id)
            if not row:
                decisions.append(Decision(memory=memory, action=ACTION_STORE))
                continue

            action = str(row.get("action") or "").strip().lower()
            if action not in ACTIONS:
                decisions.append(Decision(memory=memory, action=ACTION_STORE))
                continue

            targets = [
                pool[str(t)] for t in (row.get("target_ids") or []) if str(t) in pool
            ]
            if action in (ACTION_UPDATE, ACTION_MERGE) and not targets:
                # 没有合法目标就无从 update/merge，降级为新增
                decisions.append(Decision(memory=memory, action=ACTION_STORE))
                continue
            if action == ACTION_SKIP:
                decisions.append(Decision(memory=memory, action=ACTION_SKIP))
                continue

            decisions.append(Decision(
                memory=memory,
                action=action,
                targets=targets,
                content=_merged_content(row, memory),
                type=_merged_type(row, memory),
                priority=_merged_priority(row, memory, targets),
                timestamps=_merged_timestamps(row, memory, targets),
            ))
        return decisions


def _merged_content(row: dict, memory: L1Memory) -> str:
    text = str(row.get("merged_content") or "").strip()
    return text or memory.content


def _merged_type(row: dict, memory: L1Memory) -> str:
    value = str(row.get("merged_type") or "").strip()
    return value if value in MEMORY_TYPES else memory.type


def _merged_priority(row: dict, memory: L1Memory, targets: list[L1Memory]) -> int:
    try:
        return int(row.get("merged_priority"))
    except (TypeError, ValueError):
        return max([memory.priority] + [t.priority for t in targets])


def _merged_timestamps(
    row: dict, memory: L1Memory, targets: list[L1Memory],
) -> list[str]:
    """并集去重排序。LLM 给的逐项采用，但自己再并一遍，防止漏掉旧记忆的时间线。"""
    values = row.get("merged_timestamps")
    collected: list[str] = []
    if isinstance(values, list):
        collected.extend(str(v) for v in values if v)
    collected.extend(memory.timestamps)
    for target in targets:
        collected.extend(target.timestamps)
    return sorted({v for v in collected if v})
