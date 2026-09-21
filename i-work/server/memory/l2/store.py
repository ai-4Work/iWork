"""L2 落地写入（设计文档 L2-2.5 的动作落地表）。

一次整合的动作数组 → `(inserts, supersede_ids)`，一个事务写入（形状与
`memory/store.py:apply_decisions` 完全一致）：新行插入 + 被取代行 `retrievable=false`
同生共死，否则会出现新旧两份都进导航的中间态。

| 动作   | heat            | version           | 血缘              | 软删         |
|--------|-----------------|-------------------|-------------------|--------------|
| create | 1               | 1                 | 动作给的 ∪ 本批   | —            |
| update | 旧 + 1          | 旧 + 1            | 旧 ∪ 动作给的     | 旧行         |
| merge  | Σ(被并 heat)+1  | max(被并 ver)+1   | 被并者 ∪ 动作给的 | 全部 sources |

热度与 version 一律由工程侧算（文档 L2-2.5），LLM 说了不算。

一批动作**按序作用在一个以规范化名字为键的工作副本上**，而不是各自独立算：同一批里
"A 改名并 merge 掉 B"这类动作之间存在依赖，独立算会产出指向已被软删行的引用。这样也顺带
保证落地后名字集合内部无冲突（`uq_l2_scenes_scope_name` 是局部唯一索引，名字撞了整批回滚）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from server.memory.l2.types import (
    ACTION_CREATE, ACTION_MERGE, ACTION_UPDATE, L2Scene, SceneAction, new_scene_id,
)
from server.observability.logging import get_logger
from server.storage.base import L2SceneRepository

logger = get_logger("iwork.memory")


async def apply_actions(
    repo: L2SceneRepository,
    actions: list[SceneAction],
    *,
    scenes: list[L2Scene],
    user_id: str,
    agent_id: str,
    batch_memory_ids: list[str],
) -> dict[str, int]:
    """把一批动作落到场景库，返回各动作的条数（供日志/测试断言）。"""
    now = datetime.now(timezone.utc)
    stats = {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_MERGE: 0}

    working: dict[str, L2Scene] = {s.name: s for s in scenes}
    fresh: set[str] = set()        # 本批新建（尚未落库）的行所在的名字
    superseded: set[str] = set()   # 需要软删的**已落库**行 id

    def lineage_of(action: SceneAction, old: list[str]) -> list[str]:
        """血缘 = 旧 ∪ 动作给的；动作没给就退化为整批。保持插序去重。"""
        ids = list(old)
        for mid in action.source_memory_ids or batch_memory_ids:
            if mid not in ids:
                ids.append(mid)
        return ids

    def do_create(action: SceneAction) -> str:
        working[action.name] = L2Scene(
            id=new_scene_id(),
            user_id=user_id,
            agent_id=agent_id,
            name=action.name,
            summary=action.summary,
            content=action.content,
            heat=1,
            version=1,
            source_memory_ids=lineage_of(action, []),
            created_at=now,
            updated_at=now,
        )
        fresh.add(action.name)
        return ACTION_CREATE

    def do_update(action: SceneAction) -> str:
        base = working[action.name]
        heat, version = base.heat + 1, base.version + 1
        lineage = lineage_of(action, base.source_memory_ids)
        summary = action.summary or base.summary

        if action.name in fresh:
            # 本批内二次更新：直接改这份还没落库的新行，不重复软删
            base.summary = summary
            base.content = action.content
            base.heat = heat
            base.version = version
            base.source_memory_ids = lineage
            base.updated_at = now
            return ACTION_UPDATE

        # 更新 = 新行 + 软删旧行（旧行与血缘保留，硬删只留给用户手删）
        superseded.add(base.id)
        working[action.name] = L2Scene(
            id=new_scene_id(),
            user_id=base.user_id,
            agent_id=base.agent_id,
            name=action.name,
            summary=summary,
            content=action.content,
            heat=heat,
            version=version,
            source_memory_ids=lineage,
            created_at=base.created_at or now,
            updated_at=now,
        )
        fresh.add(action.name)
        return ACTION_UPDATE

    def do_merge(action: SceneAction) -> str:
        # 目标 = sources 里能解析到的行；外加"新名字恰好是某个未列入 sources 的现有场景"
        # 这一情形（LLM 常这么写：把 A 并入已有的 B）
        names = [n for n in dict.fromkeys(action.sources) if n in working]
        if action.name in working and action.name not in names:
            names.append(action.name)
        if not names:
            # 一个都没解析到：降级为新建，否则这批记忆就丢了
            return do_create(action)

        targets = [working[n] for n in names]
        merged: list[str] = []
        for t in targets:
            merged.extend(i for i in t.source_memory_ids if i not in merged)
        created_ats = [t.created_at for t in targets if t.created_at]

        for name in names:
            row = working.pop(name)
            if name in fresh:
                fresh.discard(name)   # 本批新建又被并走，直接消失，不留痕
            else:
                superseded.add(row.id)

        working[action.name] = L2Scene(
            id=new_scene_id(),
            user_id=targets[0].user_id,
            agent_id=targets[0].agent_id,
            name=action.name,
            summary=action.summary or targets[0].summary,
            content=action.content,
            heat=sum(t.heat for t in targets) + 1,
            version=max(t.version for t in targets) + 1,
            source_memory_ids=lineage_of(action, merged),
            created_at=min(created_ats) if created_ats else now,
            updated_at=now,
        )
        fresh.add(action.name)
        return ACTION_MERGE

    for action in actions:
        if action.action == ACTION_MERGE:
            effective = do_merge(action)
        elif action.action == ACTION_UPDATE and action.name in working:
            effective = do_update(action)
        elif action.name in working:
            # create 撞上已有名字 → 走 update 语义，否则会撞局部唯一索引
            effective = do_update(action)
        else:
            # 真新建；也是「update 目标不在作用域内」的降级去向
            effective = do_create(action)
        stats[effective] = stats.get(effective, 0) + 1

    # fresh 里的都是本批新建的行；working 里其余条目是没被碰过的已落库行，无需重写
    inserts = [working[name] for name in fresh if name in working]
    if inserts or superseded:
        await repo.apply_batch(inserts, sorted(superseded))
        logger.info(
            "l2_store_applied", inserts=len(inserts), superseded=len(superseded), **stats,
        )
    return stats
