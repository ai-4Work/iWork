"""抽取时机与调度（设计文档 L1-1.2 / L1-2.8）。

用**轮询 sweep**（默认 60s 一跳）而不是 per-session 计时器：触发判定全部由 DB
现算（游标落 l1_checkpoints），进程重启不丢断点，也天然覆盖"会话已结束仍有余量"
的冲刷场景。

四种触发：
① 阈值触发(主)  本会话未抽的 user 消息数 >= turn_threshold
② 空闲兜底      仍有未抽消息 且 距上次抽取超过 idle_minutes
③ 存量级联      抽完一轮后剩余仍 >= 一整批 → 立即续抽
④ 会话冲刷      会话已归档（非 active）且仍有未抽消息

冷启动：第一次见到某会话时，若积压超过一整批（= 开启 L1 之前就存在的历史），
直接把游标推到当前末尾跳过 —— 否则会把存量对话全量重抽一遍。新会话积压不足
一批，从 0 起算正常抽。

sweep 是串行 await 的，同一作用域不会有两个抽取并行，因此不需要额外加锁；
将来若改成并发 worker，需按 (user_id, agent_id) 加锁保护"读候选池 + 落地"这段。

任一步失败都只记日志、**不推进游标**，下一轮重试；抽取解析失败同理。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from server.memory.dedup import ConflictResolver
from server.memory.extractor import MemoryExtractor
from server.memory.l0 import L0Reader
from server.memory.retriever import TFIDFMemoryRetriever
from server.memory.store import apply_decisions
from server.memory.types import L1Checkpoint
from server.models.session import SessionStatus
from server.observability.logging import get_logger
from server.storage.base import L1MemoryRepository, SessionRepository

logger = get_logger("iwork.memory")


class L1Scheduler:
    def __init__(
        self,
        *,
        session_repo: SessionRepository,
        memory_repo: L1MemoryRepository,
        reader: L0Reader,
        extractor: MemoryExtractor,
        resolver: ConflictResolver,
        interval_seconds: int = 60,
        turn_threshold: int = 5,
        idle_minutes: int = 10,
        batch_size: int = 10,
        max_cascade: int = 5,
    ):
        self._sessions = session_repo
        self._repo = memory_repo
        self._reader = reader
        self._extractor = extractor
        self._resolver = resolver
        self._interval = interval_seconds
        self._turn_threshold = turn_threshold
        self._idle = timedelta(minutes=idle_minutes)
        self._batch_size = batch_size
        self._max_cascade = max_cascade

    async def run_forever(self) -> None:
        """后台常驻循环。取消（关机）时干净退出。"""
        logger.info("l1_scheduler_started", interval_seconds=self._interval)
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                logger.info("l1_scheduler_stopped")
                raise
            except Exception as exc:  # sweep 整体失败不能让后台任务静默死掉
                logger.warning("l1_sweep_failed", error=str(exc))
            await asyncio.sleep(self._interval)

    async def sweep(self) -> int:
        """巡检一轮，返回实际抽取的会话数。"""
        started = time.perf_counter()
        extracted = 0
        candidates = await self._collect_sessions()
        for session in candidates:
            try:
                if await self._maybe_extract(session):
                    extracted += 1
            except Exception as exc:
                logger.warning(
                    "l1_session_sweep_failed",
                    session_id=str(session.id), error=str(exc),
                )
        logger.info(
            "l1_sweep_done",
            candidates=len(candidates),
            extracted=extracted,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return extracted

    # ── 候选会话 ────────────────────────────────────────────────

    async def _collect_sessions(self):
        """活跃会话 + 游标表里已归档的会话（冲刷触发要后者）。"""
        sessions = list(await self._sessions.list_active())
        seen = {s.id for s in sessions}
        for checkpoint in await self._repo.list_checkpoints():
            try:
                sid = UUID(str(checkpoint.session_id))
            except (ValueError, TypeError):
                continue
            if sid in seen:
                continue
            session = await self._sessions.get(sid)
            if session is not None and session.status != SessionStatus.ACTIVE:
                sessions.append(session)
                seen.add(sid)
        return sessions

    # ── 单会话 ──────────────────────────────────────────────────

    async def _maybe_extract(self, session) -> bool:
        user_id = str(session.user_id)
        agent_id = getattr(session, "agent_path", "") or ""
        checkpoint = await self._get_or_create_checkpoint(session, user_id, agent_id)

        batch = await self._reader.read_batch(session.id, checkpoint.last_cursor)
        trigger = self._trigger_for(session, batch, checkpoint)
        if trigger is None:
            return False

        return await self._run_session(
            session, checkpoint, batch, trigger, user_id, agent_id,
        )

    async def _get_or_create_checkpoint(
        self, session, user_id: str, agent_id: str,
    ) -> L1Checkpoint:
        checkpoint = await self._repo.get_checkpoint(str(session.id))
        if checkpoint is not None:
            return checkpoint

        batch = await self._reader.read_batch(session.id, 0)
        legacy = batch.pending_total > self._batch_size
        checkpoint = L1Checkpoint(
            session_id=str(session.id),
            user_id=user_id,
            agent_id=agent_id,
            last_cursor=await self._current_end(session.id) if legacy else 0,
            # 冷启动即开始计空闲 —— 否则"从未抽过"的会话永远算不出空闲时长
            last_extracted_at=datetime.now(timezone.utc),
        )
        await self._repo.upsert_checkpoint(checkpoint)
        if legacy:
            logger.info(
                "l1_cold_start_skipped",
                session_id=str(session.id), pending=batch.pending_total,
            )
        return checkpoint

    async def _current_end(self, session_id) -> int:
        return await self._reader.current_end(session_id)

    def _trigger_for(self, session, batch, checkpoint) -> str | None:
        if not batch.has_new:
            return None
        # ④ 会话冲刷：收官时把尾巴一次性抽掉
        if session.status != SessionStatus.ACTIVE:
            return "flush"
        # ① 阈值触发（主）
        if batch.pending_user >= self._turn_threshold:
            return "threshold"
        # ② 空闲兜底
        last = checkpoint.last_extracted_at
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last > self._idle:
                return "idle"
        return None

    async def _run_session(
        self, session, checkpoint, batch, trigger: str, user_id: str, agent_id: str,
    ) -> bool:
        if not await self._extract(
            session, checkpoint, batch, trigger, user_id, agent_id,
        ):
            return False

        # ③ 存量级联：抽完仍有一整批以上的 user 消息积压 → 立即续抽
        for _ in range(self._max_cascade):
            nxt = await self._reader.read_batch(session.id, checkpoint.last_cursor)
            if not nxt.has_new or nxt.pending_user < self._batch_size:
                break
            if not await self._extract(
                session, checkpoint, nxt, "cascade", user_id, agent_id,
            ):
                break
        return True

    async def _extract(
        self, session, checkpoint, batch, trigger: str, user_id: str, agent_id: str,
    ) -> bool:
        """抽一批 → 去重 → 落地 → 推进游标。返回是否成功（失败不推游标）。"""
        started = time.perf_counter()
        logger.info(
            "l1_extract_start",
            session_id=str(session.id),
            trigger=trigger,
            messages=len(batch.new_messages),
            pending_user=batch.pending_user,
        )
        result = await self._extractor.extract(
            batch,
            user_id=user_id,
            agent_id=agent_id,
            session_id=str(session.id),
            previous_scene=checkpoint.last_scene_name,
        )
        if result is None:
            return False

        if result.memories:
            pool = await self._repo.list_by_scope(user_id, agent_id)
            retriever = TFIDFMemoryRetriever(pool)
            decisions = await self._resolver.resolve(result.memories, retriever)
            await apply_decisions(self._repo, decisions)

        checkpoint.last_cursor = batch.read_cursor
        checkpoint.last_scene_name = result.scene_name or checkpoint.last_scene_name
        checkpoint.last_extracted_at = datetime.now(timezone.utc)
        await self._repo.upsert_checkpoint(checkpoint)

        logger.info(
            "l1_extract_done",
            session_id=str(session.id),
            trigger=trigger,
            messages=len(batch.new_messages),
            memories=len(result.memories),
            cursor=checkpoint.last_cursor,
            scene=checkpoint.last_scene_name,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return True
