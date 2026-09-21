"""L2 整合时机与调度（设计文档 L2-1.2）。

独立于 L1 的 sweep，独立后台任务，复用同一个 60s 轮询间隔。**触发判定全由 DB 现算**
（游标落 l2_checkpoints），与 L1 同款，重启不丢断点。

三种触发（doc L2-1.2）：
① 级联(主)  `l1_memories` 里有游标之后的行，且本批最新那条距今 >= cascade_delay(10s)
② 保底轮询  同上非空，且距上次整合 >= max_interval(3600s)
③ 最小间隔  距上次整合 < min_interval(900s) → 不跑（是约束，不是触发）

L1 完成后**不需要发任何信号**：轮询架构下"L1 落定 → 等 10s → 整合"退化成一句 DB 判定。
L1 还在追积压时它的 `updated_at` 一直在翻新，① 自然不满足；L1 收工后 10s 即触发。

不做文档 ④"冷会话超 24h 跳过"：它防的是空转，而 ② 只在 pending 非空时才可能被看到，
没有新 L1 记忆就根本进不了这个分支，保护是隐含的。

不需要加锁：一次 sweep 串行 await，同作用域不会有两个整合并行（与 scheduler.py:17-18 同款结论）。
任一步失败只记日志、**不推进游标**，下一轮重试。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from server.memory.l2.consolidator import MemoryConsolidator
from server.memory.l2.reader import L2Reader
from server.memory.l2.store import apply_actions
from server.memory.l2.types import L2Checkpoint
from server.observability.logging import get_logger
from server.storage.base import L1MemoryRepository, L2SceneRepository

logger = get_logger("iwork.memory")

TRIGGER_CASCADE = "cascade"
TRIGGER_POLL = "poll"


def _aware(value: datetime | None) -> datetime | None:
    """tz-naive 一律按 UTC 解释，避免与 now(tz-aware) 相减时炸。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class L2Scheduler:
    def __init__(
        self,
        *,
        memory_repo: L1MemoryRepository,
        scene_repo: L2SceneRepository,
        reader: L2Reader,
        consolidator: MemoryConsolidator,
        interval_seconds: int = 60,
        batch_memories: int = 20,
        candidate_scenes: int = 5,
        candidate_chars: int = 12000,
        max_scenes: int = 15,
        cascade_delay_seconds: int = 10,
        min_interval_seconds: int = 900,
        max_interval_seconds: int = 3600,
        persona_scheduler=None,
    ):
        self._repo = memory_repo
        self._scenes = scene_repo
        self._reader = reader
        self._consolidator = consolidator
        self._interval = interval_seconds
        self._batch_memories = batch_memories
        self._candidate_scenes = candidate_scenes
        self._candidate_chars = candidate_chars
        self._max_scenes = max_scenes
        self._cascade_delay = timedelta(seconds=cascade_delay_seconds)
        self._min_interval = timedelta(seconds=min_interval_seconds)
        self._max_interval = timedelta(seconds=max_interval_seconds)
        # L3 画像的级联入口（doc L3-1.2：L3 不是独立定时任务）。刻意不 import L3 模块，
        # 只调约定的 maybe_generate，避免 l2 ← l3 ← l2 的循环依赖。
        self.persona_scheduler = persona_scheduler

    async def run_forever(self) -> None:
        logger.info("l2_scheduler_started", interval_seconds=self._interval)
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                logger.info("l2_scheduler_stopped")
                raise
            except Exception as exc:  # sweep 整体失败不能让后台任务静默死掉
                logger.warning("l2_sweep_failed", error=str(exc))
            await asyncio.sleep(self._interval)

    async def sweep(self) -> int:
        """巡检一轮，返回实际整合的作用域数。"""
        consolidated = 0
        for user_id, agent_id in await self._collect_scopes():
            try:
                if await self._maybe_consolidate(user_id, agent_id):
                    consolidated += 1
            except Exception as exc:
                logger.warning(
                    "l2_scope_sweep_failed", user_id=user_id, agent_id=agent_id,
                    error=str(exc),
                )
        return consolidated

    # ── 作用域枚举 ──────────────────────────────────────────────

    async def _collect_scopes(self) -> list[tuple[str, str]]:
        """有可检索 L1 记忆的作用域（L2 的输入来源），与已有游标的作用域取并集。"""
        scopes = set(await self._repo.list_scopes())
        for checkpoint in await self._scenes.list_checkpoints():
            scopes.add((checkpoint.user_id, checkpoint.agent_id or ""))
        return sorted(scopes)

    # ── 单作用域 ────────────────────────────────────────────────

    async def _maybe_consolidate(self, user_id: str, agent_id: str) -> bool:
        now = datetime.now(timezone.utc)
        checkpoint = await self._repo_checkpoint(user_id, agent_id)

        data = await self._reader.read(
            user_id, agent_id,
            since=checkpoint.last_memory_at,
            batch_memories=self._batch_memories,
            candidate_scenes=self._candidate_scenes,
            candidate_chars=self._candidate_chars,
            max_scenes=self._max_scenes,
        )
        if data is None:      # 游标之后没有新 L1 记忆
            return False

        trigger = self._trigger_for(checkpoint, data.newest_at, now)
        if trigger is None:
            return False

        scope_label = f"{user_id}/{agent_id}"
        result = await self._consolidator.consolidate(data, scope_label=scope_label)
        if result is None:    # 解析失败：不推进游标，下一轮重试
            return False

        scenes = await self._scenes.list_by_scope(user_id, agent_id)
        if result.actions:
            await apply_actions(
                self._scenes, result.actions,
                scenes=scenes, user_id=user_id, agent_id=agent_id,
                batch_memory_ids=data.memory_ids,
            )
            scenes = await self._scenes.list_by_scope(user_id, agent_id)

        checkpoint.last_memory_at = data.newest_at
        checkpoint.last_run_at = now
        checkpoint.processing_count += 1
        if result.persona_update_request:
            # 落 P1 信号（doc L2-2.6 第 4 步），紧接着由 L3 评估是否需要插队生成画像
            checkpoint.persona_update_request = result.persona_update_request
        await self._scenes.upsert_checkpoint(checkpoint)

        logger.info(
            "l2_consolidate_done",
            user_id=user_id,
            agent_id=agent_id,
            trigger=trigger,
            memories=len(data.memories),
            actions=len(result.actions),
            scenes=len(scenes),
            processing_count=checkpoint.processing_count,
        )

        # ── 级联 L3（doc L3-1.2）：L2 跑完才轮到评估画像，L2 被跳过时不评估 ──
        if self.persona_scheduler is not None:
            await self.persona_scheduler.maybe_generate(
                user_id, agent_id, request=checkpoint.persona_update_request,
            )
        return True

    async def _repo_checkpoint(self, user_id: str, agent_id: str) -> L2Checkpoint:
        checkpoint = await self._scenes.get_checkpoint(user_id, agent_id)
        if checkpoint is not None:
            return checkpoint
        # 冷启动：游标从 epoch 起算也只吃 batch_memories 条/轮，不会把历史全量重抽一遍
        checkpoint = L2Checkpoint(user_id=user_id, agent_id=agent_id)
        await self._scenes.upsert_checkpoint(checkpoint)
        return checkpoint

    def _trigger_for(
        self, checkpoint: L2Checkpoint, newest_at: datetime | None, now: datetime,
    ) -> str | None:
        last_run = _aware(checkpoint.last_run_at)
        # ③ 最小间隔：是约束，先于两个触发判定（否则 L1 每轮落定都会立刻拖着 L2 跑）
        if last_run is not None and now - last_run < self._min_interval:
            return None
        # ① 级联（主）
        newest = _aware(newest_at)
        if newest is not None and now - newest >= self._cascade_delay:
            return TRIGGER_CASCADE
        # ② 保底轮询：级联迟迟不满足（记忆持续零星落库）时的兜底。
        # 冷启动（从未整合过）不走这里 —— 否则 10s 级联延迟会被立刻绕过去，
        # 第一次见到作用域就整合，与文档"L1 落定后等 10s"的节奏不符。
        if last_run is not None and now - last_run >= self._max_interval:
            return TRIGGER_POLL
        return None
