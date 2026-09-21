"""落地写入（设计文档 L1-2.7）。

四条动作的落地语义：
- store  → 插入新行（retrievable=true）
- update → 新行 version = 目标最大 version + 1；目标行 retrievable 置 false
- merge  → 同 update，但内容/类型/优先级/时间线取冲突判断给出的合并结果
- skip   → 无操作

一批次一个事务（L1MemoryRepository.apply_batch 保证原子）：否则会出现
"新行已进、旧行未软删"的双份可检索状态。

血缘靠行内 source_message_ids，不单开血缘表（与 016 迁移的合并决策一致）。
"""
from __future__ import annotations

from server.memory.dedup import ACTION_SKIP, Decision
from server.storage.base import L1MemoryRepository
from server.observability.logging import get_logger

logger = get_logger("iwork.memory")


async def apply_decisions(
    repo: L1MemoryRepository, decisions: list[Decision],
) -> dict[str, int]:
    """把一批决策落到记忆库，返回各动作的条数（供日志/测试断言）。"""
    stats = {"store": 0, "update": 0, "merge": 0, "skip": 0}
    inserts = []
    supersede: list[str] = []

    for decision in decisions:
        stats[decision.action] = stats.get(decision.action, 0) + 1
        if decision.action == ACTION_SKIP:
            continue

        row = decision.memory
        if decision.supersedes:
            max_version = max(t.version for t in decision.targets)
            row.version = max_version + 1
            row.content = decision.content or row.content
            row.type = decision.type or row.type
            if decision.priority is not None:
                row.priority = decision.priority
            row.timestamps = decision.timestamps or row.timestamps
            supersede.extend(decision.supersedes)
        inserts.append(row)

    if inserts or supersede:
        await repo.apply_batch(inserts, sorted(set(supersede)))

    if any(stats.values()):
        logger.info("l1_store_applied", **stats)
    return stats
