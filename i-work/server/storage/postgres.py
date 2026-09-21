from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func, update, delete, text, bindparam
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from server.models.session import Session, SessionStatus
from server.models.message import Message, MessageStatus, SkillInvocation, MCPServerConfig
from server.models.mail import RUNNABLE_MSG_TYPES, RESULT_MSG_TYPES
from server.models.tool_invocation import ToolInvocation, InvocationState
from server.memory.types import L1Memory, L1Checkpoint
from server.memory.l2.types import L2Scene, L2Checkpoint
from server.memory.l3.types import L3Persona
from server.storage.base import (
    SessionRepository, MessageRepository, ToolInvocationRepository,
    L1MemoryRepository, L2SceneRepository, L3PersonaRepository,
)
from server.db.models import (
    OrmUser, OrmSession, OrmMessage, OrmConversationHistory,
    OrmUserSkill, OrmUserMcpServer, OrmSkillHub, OrmMcpHub,
    OrmExpertHub, OrmExpertTeamHub, OrmOffloadedBlock, OrmToolInvocation,
    OrmL1Memory, OrmL1Checkpoint, OrmL2Scene, OrmL2Checkpoint, OrmL3Persona,
)


# ═══════════════════════════════════════════════════════════════
# PgSessionRepo
# ═══════════════════════════════════════════════════════════════

class PgSessionRepo(SessionRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def create(self, session: Session) -> Session:
        async with self._sf() as db:
            orm = OrmSession.from_pydantic(session)
            db.add(orm)
            await db.commit()
            return session

    async def get(self, session_id: _uuid.UUID) -> Session | None:
        async with self._sf() as db:
            orm = await db.get(OrmSession, session_id)
            return orm.to_pydantic() if orm else None

    async def update(self, session: Session) -> Session:
        async with self._sf() as db:
            orm = OrmSession.from_pydantic(session)
            orm.updated_at = datetime.now(timezone.utc)
            await db.merge(orm)
            await db.commit()
            return session

    async def list_active(self, user_id: str | None = None) -> list[Session]:
        async with self._sf() as db:
            stmt = select(OrmSession).where(OrmSession.status == "active")
            if user_id:
                stmt = stmt.where(OrmSession.user_id == _uuid.UUID(user_id))
            result = await db.execute(stmt)
            return [row.to_pydantic() for row in result.scalars()]

    async def list_by_user(
        self, user_id: str, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Session], int]:
        async with self._sf() as db:
            uid = _uuid.UUID(user_id)
            stmt = select(OrmSession).where(OrmSession.user_id == uid)
            if status and status != "all":
                stmt = stmt.where(OrmSession.status == status)
            # Count
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            # Fetch
            stmt = stmt.order_by(OrmSession.updated_at.desc()).offset(offset).limit(limit)
            result = await db.execute(stmt)
            rows = [row.to_pydantic() for row in result.scalars()]
            return rows, total

    async def archive(self, session_id: _uuid.UUID) -> None:
        async with self._sf() as db:
            await db.execute(
                update(OrmSession)
                .where(OrmSession.id == session_id)
                .values(status="archived", updated_at=datetime.now(timezone.utc))
            )
            await db.commit()

    async def list_by_parent(self, parent_id: _uuid.UUID) -> list[Session]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmSession).where(OrmSession.parent_id == parent_id)
            )
            return [row.to_pydantic() for row in result.scalars()]

    async def get_by_path(
        self, root_session_id: _uuid.UUID, agent_path: str,
    ) -> Session | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmSession).where(
                    OrmSession.root_session_id == root_session_id,
                    OrmSession.agent_path == agent_path,
                )
            )
            row = result.scalars().first()
            return row.to_pydantic() if row else None


# ═══════════════════════════════════════════════════════════════
# PgMessageRepo
# ═══════════════════════════════════════════════════════════════

class PgMessageRepo(MessageRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def create(self, message: Message) -> Message:
        async with self._sf() as db:
            orm = OrmMessage.from_pydantic(message)
            db.add(orm)
            await db.commit()
            return message

    async def get(self, message_id: _uuid.UUID) -> Message | None:
        async with self._sf() as db:
            orm = await db.get(OrmMessage, message_id)
            return orm.to_pydantic() if orm else None

    async def update(self, message: Message) -> Message:
        async with self._sf() as db:
            orm = OrmMessage.from_pydantic(message)
            await db.merge(orm)
            await db.commit()
            return message

    async def get_by_client_message_id(
        self, session_id: _uuid.UUID, client_message_id: str,
    ) -> Message | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMessage).where(
                    OrmMessage.session_id == session_id,
                    OrmMessage.client_message_id == client_message_id,
                ).limit(1)
            )
            orm = result.scalars().first()
            return orm.to_pydantic() if orm else None

    async def dequeue_next(self, session_id: _uuid.UUID) -> Message | None:
        """原子出队：SELECT ... FOR UPDATE SKIP LOCKED。

        msg_type 过滤是**分流**的关键：result/status 信封只是喂给挂起父协程的数据，
        若被这里取走就会替父跑一轮 LLM（§9.11.12 #11）。
        """
        async with self._sf() as db:
            # 用原生 SQL 实现原子出队。类型集合走 bindparam（而非字面量），
            # 与 count_pending / list_pending / renumber_queue 共用同一份常量。
            result = await db.execute(
                text("""
                    WITH next_msg AS (
                        SELECT id FROM messages
                        WHERE session_id = :sid AND status = 'pending'
                          AND msg_type IN :types
                        ORDER BY queue_position
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE messages SET status = 'processing', queue_position = NULL
                    FROM next_msg WHERE messages.id = next_msg.id
                    RETURNING messages.*;
                """).bindparams(bindparam("types", expanding=True)),
                {"sid": session_id, "types": list(RUNNABLE_MSG_TYPES)},
            )
            row = result.fetchone()
            if row is None:
                await db.commit()
                return None
            await db.commit()
            # 重取 ORM 对象以获取更新后的状态
            orm = await db.get(OrmMessage, row.id)
            if orm:
                await self.renumber_queue(session_id)
                return orm.to_pydantic()
            return None

    async def list_pending(self, session_id: _uuid.UUID) -> list[Message]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMessage)
                .where(OrmMessage.session_id == session_id)
                .where(OrmMessage.status == "pending")
                .where(OrmMessage.msg_type.in_(RUNNABLE_MSG_TYPES))
                .order_by(OrmMessage.queue_position)
            )
            return [row.to_pydantic() for row in result.scalars()]

    async def count_pending(self, session_id: _uuid.UUID) -> int:
        """⚠️ 必须与 dequeue_next / list_pending 用同一套 msg_type 过滤。

        少一个过滤：信封会计入 max_queue_size 预算，把合法用户消息挤成 429。
        """
        async with self._sf() as db:
            result = await db.execute(
                select(func.count()).where(
                    OrmMessage.session_id == session_id,
                    OrmMessage.status == "pending",
                    OrmMessage.msg_type.in_(RUNNABLE_MSG_TYPES),
                )
            )
            return result.scalar() or 0

    async def list_pending_results(
        self, session_id: _uuid.UUID, recipient_agent_id: str,
    ) -> list[Message]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMessage)
                .where(OrmMessage.session_id == session_id)
                .where(OrmMessage.status == "pending")
                .where(OrmMessage.msg_type.in_(RESULT_MSG_TYPES))
                .where(OrmMessage.recipient_agent_id == recipient_agent_id)
                .order_by(OrmMessage.created_at)
            )
            return [row.to_pydantic() for row in result.scalars()]

    async def cancel_pending(self, message_id: _uuid.UUID, session_id: _uuid.UUID) -> bool:
        async with self._sf() as db:
            orm = await db.get(OrmMessage, message_id)
            if orm and orm.session_id == session_id and orm.status == "pending":
                orm.status = "cancelled"
                orm.queue_position = None
                await db.commit()
                await self.renumber_queue(session_id)
                return True
            return False

    async def renumber_queue(self, session_id: _uuid.UUID) -> None:
        """窗口函数重排 queue_position。"""
        async with self._sf() as db:
            await db.execute(
                text("""
                    UPDATE messages SET queue_position = sub.rn
                    FROM (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY queue_position) AS rn
                        FROM messages
                        WHERE session_id = :sid AND status = 'pending'
                          AND msg_type IN :types
                    ) AS sub
                    WHERE messages.id = sub.id
                """).bindparams(bindparam("types", expanding=True)),
                {"sid": session_id, "types": list(RUNNABLE_MSG_TYPES)},
            )
            await db.commit()

    async def list_processing(self) -> list[Message]:
        """查询所有正在处理的消息（引擎恢复用）。"""
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMessage).where(OrmMessage.status == "processing")
            )
            return [row.to_pydantic() for row in result.scalars()]


# ═══════════════════════════════════════════════════════════════
# UserRepo
# ═══════════════════════════════════════════════════════════════

class UserRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get_or_create_default(self) -> OrmUser:
        from server.db.seed import DEFAULT_USER_ID
        async with self._sf() as db:
            user = await db.get(OrmUser, DEFAULT_USER_ID)
            if user:
                return user
            user = OrmUser(
                id=DEFAULT_USER_ID,
                username="default-user",
                display_name="默认用户",
            )
            db.add(user)
            await db.commit()
            return user

    async def get_by_id(self, user_id: _uuid.UUID) -> OrmUser | None:
        async with self._sf() as db:
            return await db.get(OrmUser, user_id)


# ═══════════════════════════════════════════════════════════════
# ConversationHistoryRepo
# ═══════════════════════════════════════════════════════════════

class ConversationHistoryRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def append_message(self, session_id: _uuid.UUID, msg: dict) -> None:
        """追加一条对话消息，sequence 自动递增。

        可选元数据键 message_id / turn（ContextManager 盖印）用于把行归属到 Message。
        """
        async with self._sf() as db:
            # 获取当前最大 sequence
            result = await db.execute(
                select(func.coalesce(func.max(OrmConversationHistory.sequence), 0))
                .where(OrmConversationHistory.session_id == session_id)
            )
            max_seq = result.scalar() or 0
            entry = OrmConversationHistory(
                session_id=session_id,
                role=msg["role"],
                content=msg.get("content"),
                reasoning_content=msg.get("reasoning_content"),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                message_id=(_uuid.UUID(msg["message_id"]) if msg.get("message_id") else None),
                turn=msg.get("turn"),
                sequence=max_seq + 1,
            )
            db.add(entry)
            await db.commit()

    async def get_history(self, session_id: _uuid.UUID) -> list[dict]:
        """获取完整对话历史，按 sequence 排序。"""
        async with self._sf() as db:
            result = await db.execute(
                select(OrmConversationHistory)
                .where(OrmConversationHistory.session_id == session_id)
                .order_by(OrmConversationHistory.sequence)
            )
            return [row.to_dict() for row in result.scalars()]

    async def append_tool_call_to_last_assistant(
        self, session_id: _uuid.UUID, tool_call_block: dict,
    ) -> None:
        """将 tool_call 合并到最近一条 assistant 消息的 tool_calls 中。
        向上查找最近的 assistant 行（跨过 tool/user 消息），确保 reasoning_content 不丢失。"""
        async with self._sf() as db:
            # 向上查找最近的 assistant 行（limit 5，足够跨过多条 tool result）
            result = await db.execute(
                select(OrmConversationHistory)
                .where(OrmConversationHistory.session_id == session_id)
                .order_by(OrmConversationHistory.sequence.desc())
                .limit(5)
            )
            rows = result.scalars().all()
            target = None
            for row in rows:
                if row.role == "assistant":
                    target = row
                    break
            if target is not None:
                existing = list(target.tool_calls or [])
                existing.append(tool_call_block)
                target.tool_calls = existing
            else:
                # 没有 assistant 消息（理论上不应发生），新建一行
                seq_result = await db.execute(
                    select(func.coalesce(func.max(OrmConversationHistory.sequence), 0))
                    .where(OrmConversationHistory.session_id == session_id)
                )
                max_seq = seq_result.scalar() or 0
                db.add(OrmConversationHistory(
                    session_id=session_id,
                    role="assistant",
                    content=None,
                    tool_calls=[tool_call_block],
                    sequence=max_seq + 1,
                ))
            await db.commit()

    async def clear_history(self, session_id: _uuid.UUID) -> None:
        async with self._sf() as db:
            await db.execute(
                delete(OrmConversationHistory).where(
                    OrmConversationHistory.session_id == session_id
                )
            )
            await db.commit()

    async def list_rows(self, session_id: _uuid.UUID) -> list[dict]:
        """列出会话全部历史行（含 message_id/turn/sequence 元数据），按 sequence 升序。

        供历史读取端点做消息组装；与 get_history（OpenAI 兼容 to_dict）不同，这里返回全字段。
        """
        async with self._sf() as db:
            result = await db.execute(
                select(OrmConversationHistory)
                .where(OrmConversationHistory.session_id == session_id)
                .order_by(OrmConversationHistory.sequence)
            )
            rows = []
            for r in result.scalars():
                rows.append({
                    "sequence": r.sequence,
                    "role": r.role,
                    "content": r.content,
                    "reasoning_content": r.reasoning_content,
                    "tool_calls": r.tool_calls,
                    "tool_call_id": r.tool_call_id,
                    "message_id": str(r.message_id) if r.message_id else None,
                    "turn": r.turn,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return rows

    async def truncate_after(self, session_id: _uuid.UUID, boundary_sequence: int) -> None:
        """删除 sequence > boundary_sequence 的所有历史行（重跑/截断用）。

        边界 = 目标消息首行的 sequence：把该消息后续行与之后所有消息的历史一并删掉。
        """
        async with self._sf() as db:
            await db.execute(
                delete(OrmConversationHistory).where(
                    OrmConversationHistory.session_id == session_id,
                    OrmConversationHistory.sequence > boundary_sequence,
                )
            )
            await db.commit()

    async def truncate_message_after(
        self, session_id: _uuid.UUID, message_id, boundary_sequence: int,
    ) -> None:
        """删除本消息在 boundary 之后的历史行（M4 regenerate，只清该消息自身旧回复/工具痕迹）。

        与 truncate_after 的整会话截断不同：仅删除 message_id == 本消息 且
        sequence > boundary 的行，保留更早消息与后续消息的历史。
        """
        mid = str(message_id)
        async with self._sf() as db:
            await db.execute(
                delete(OrmConversationHistory).where(
                    OrmConversationHistory.session_id == session_id,
                    OrmConversationHistory.message_id == mid,
                    OrmConversationHistory.sequence > boundary_sequence,
                )
            )
            await db.commit()


# ═══════════════════════════════════════════════════════════════
# OffloadedBlocksRepo
# ═══════════════════════════════════════════════════════════════

class OffloadedBlocksRepo:
    """卸载块存储（10.5.3 外部记忆）。block 为 OffloadedBlock dataclass。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    @staticmethod
    def _coerce_sid(session_id):
        if isinstance(session_id, str):
            return _uuid.UUID(session_id)
        return session_id

    async def insert(self, session_id, block) -> None:
        async with self._sf() as db:
            db.add(OrmOffloadedBlock(
                session_id=self._coerce_sid(session_id),
                block_id=block.block_id,
                turn=block.turn,
                label=block.label,
                precision=block.precision,
                artifact=block.artifact,
                content=block.content,
            ))
            await db.commit()

    async def list_by_session(self, session_id) -> list[OrmOffloadedBlock]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmOffloadedBlock).where(
                    OrmOffloadedBlock.session_id == self._coerce_sid(session_id)
                ).order_by(OrmOffloadedBlock.turn)
            )
            return list(result.scalars())

    async def delete_by_session(self, session_id) -> None:
        async with self._sf() as db:
            await db.execute(
                delete(OrmOffloadedBlock).where(
                    OrmOffloadedBlock.session_id == self._coerce_sid(session_id)
                )
            )
            await db.commit()


# ═══════════════════════════════════════════════════════════════
# L1MemoryRepo（记忆模块：原子记忆 + 抽取游标）
# ═══════════════════════════════════════════════════════════════

class PgL1MemoryRepo(L1MemoryRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    @staticmethod
    def _coerce_uuid(value) -> _uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, _uuid.UUID) else _uuid.UUID(str(value))

    @classmethod
    def _to_pydantic(cls, orm: OrmL1Memory) -> L1Memory:
        return L1Memory(
            id=orm.id,
            user_id=str(orm.user_id),
            content=orm.content,
            type=orm.type,
            agent_id=orm.agent_id or "",
            session_id=str(orm.session_id) if orm.session_id else None,
            priority=orm.priority or 0,
            scene_name=orm.scene_name or "",
            source_message_ids=list(orm.source_message_ids or []),
            metadata=dict(orm.metadata_json or {}),
            timestamps=list(orm.timestamps or []),
            version=orm.version or 1,
            retrievable=bool(orm.retrievable),
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    async def apply_batch(
        self, inserts: list[L1Memory], supersede_ids: list[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._sf() as db:
            for m in inserts:
                db.add(OrmL1Memory(
                    id=m.id,
                    user_id=self._coerce_uuid(m.user_id),
                    agent_id=m.agent_id or "",
                    session_id=self._coerce_uuid(m.session_id),
                    content=m.content,
                    type=m.type,
                    priority=m.priority,
                    scene_name=m.scene_name or "",
                    source_message_ids=list(m.source_message_ids),
                    metadata_json=dict(m.metadata),
                    timestamps=list(m.timestamps),
                    version=m.version,
                    retrievable=m.retrievable,
                    created_at=m.created_at or now,
                    updated_at=m.updated_at or now,
                ))
            if supersede_ids:
                await db.execute(
                    update(OrmL1Memory)
                    .where(OrmL1Memory.id.in_(supersede_ids))
                    .values(retrievable=False, updated_at=now)
                )
            await db.commit()

    async def get(self, memory_id: str) -> L1Memory | None:
        async with self._sf() as db:
            orm = await db.get(OrmL1Memory, memory_id)
            return self._to_pydantic(orm) if orm else None

    async def list_by_scope(
        self, user_id: str, agent_id: str, *, retrievable_only: bool = True,
    ) -> list[L1Memory]:
        async with self._sf() as db:
            stmt = select(OrmL1Memory).where(
                OrmL1Memory.user_id == self._coerce_uuid(user_id),
                OrmL1Memory.agent_id == (agent_id or ""),
            )
            if retrievable_only:
                stmt = stmt.where(OrmL1Memory.retrievable == True)
            result = await db.execute(stmt.order_by(OrmL1Memory.updated_at.desc()))
            return [self._to_pydantic(r) for r in result.scalars()]

    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        memory_type: str | None = None, limit: int = 200, offset: int = 0,
    ) -> tuple[list[L1Memory], int]:
        async with self._sf() as db:
            stmt = select(OrmL1Memory).where(
                OrmL1Memory.user_id == self._coerce_uuid(user_id),
                OrmL1Memory.retrievable == True,
            )
            if agent_id is not None:
                stmt = stmt.where(OrmL1Memory.agent_id == agent_id)
            if memory_type:
                stmt = stmt.where(OrmL1Memory.type == memory_type)
            total = (await db.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            result = await db.execute(
                stmt.order_by(OrmL1Memory.updated_at.desc()).offset(offset).limit(limit)
            )
            return [self._to_pydantic(r) for r in result.scalars()], int(total)

    async def delete(self, memory_id: str) -> bool:
        async with self._sf() as db:
            orm = await db.get(OrmL1Memory, memory_id)
            if orm is None:
                return False
            await db.delete(orm)
            await db.commit()
            return True

    async def get_checkpoint(self, session_id: str) -> L1Checkpoint | None:
        async with self._sf() as db:
            orm = await db.get(OrmL1Checkpoint, self._coerce_uuid(session_id))
            return self._cp_to_pydantic(orm) if orm else None

    async def list_checkpoints(self) -> list[L1Checkpoint]:
        async with self._sf() as db:
            result = await db.execute(select(OrmL1Checkpoint))
            return [self._cp_to_pydantic(r) for r in result.scalars()]

    async def upsert_checkpoint(self, checkpoint: L1Checkpoint) -> None:
        async with self._sf() as db:
            await db.merge(OrmL1Checkpoint(
                session_id=self._coerce_uuid(checkpoint.session_id),
                user_id=self._coerce_uuid(checkpoint.user_id),
                agent_id=checkpoint.agent_id or "",
                last_cursor=checkpoint.last_cursor,
                last_scene_name=checkpoint.last_scene_name or "",
                last_extracted_at=checkpoint.last_extracted_at,
            ))
            await db.commit()

    @staticmethod
    def _cp_to_pydantic(orm: OrmL1Checkpoint) -> L1Checkpoint:
        return L1Checkpoint(
            session_id=str(orm.session_id),
            user_id=str(orm.user_id),
            agent_id=orm.agent_id or "",
            last_cursor=orm.last_cursor or 0,
            last_scene_name=orm.last_scene_name or "",
            last_extracted_at=orm.last_extracted_at,
        )

    # ── L2 整合侧的两个读口 ─────────────────────────────────────

    async def list_scopes(self) -> list[tuple[str, str]]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmL1Memory.user_id, OrmL1Memory.agent_id)
                .where(OrmL1Memory.retrievable == True)
                .distinct()
            )
            return [(str(uid), aid or "") for uid, aid in result.all()]

    async def list_since(
        self, user_id: str, agent_id: str, since: datetime | None,
        limit: int = 20,
    ) -> list[L1Memory]:
        async with self._sf() as db:
            stmt = select(OrmL1Memory).where(
                OrmL1Memory.user_id == self._coerce_uuid(user_id),
                OrmL1Memory.agent_id == (agent_id or ""),
                OrmL1Memory.retrievable == True,
            )
            if since is not None:
                stmt = stmt.where(OrmL1Memory.updated_at > since)
            result = await db.execute(
                stmt.order_by(OrmL1Memory.updated_at.asc()).limit(limit)
            )
            return [self._to_pydantic(r) for r in result.scalars()]

    async def count_by_scope(self, user_id: str, agent_id: str) -> int:
        async with self._sf() as db:
            return int((await db.execute(
                select(func.count()).select_from(OrmL1Memory.__table__).where(
                    OrmL1Memory.user_id == self._coerce_uuid(user_id),
                    OrmL1Memory.agent_id == (agent_id or ""),
                    OrmL1Memory.retrievable == True,
                )
            )).scalar() or 0)


# ═══════════════════════════════════════════════════════════════
# L2SceneRepo（记忆模块：场景记忆 + 整合游标）
# ═══════════════════════════════════════════════════════════════

class PgL2SceneRepo(L2SceneRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    @staticmethod
    def _coerce_uuid(value) -> _uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, _uuid.UUID) else _uuid.UUID(str(value))

    @staticmethod
    def _to_scene(orm: OrmL2Scene) -> L2Scene:
        return L2Scene(
            id=orm.id,
            user_id=str(orm.user_id),
            agent_id=orm.agent_id or "",
            name=orm.name,
            summary=orm.summary or "",
            content=orm.content,
            heat=orm.heat or 1,
            version=orm.version or 1,
            source_memory_ids=list(orm.source_memory_ids or []),
            retrievable=bool(orm.retrievable),
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    async def apply_batch(
        self, inserts: list[L2Scene], supersede_ids: list[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._sf() as db:
            if supersede_ids:
                # 先软删再插入：名字有局部唯一索引（WHERE retrievable），
                # merge 后同名重建时旧行必须先让位，(user_id, agent_id, name) 才不撞。
                await db.execute(
                    update(OrmL2Scene)
                    .where(OrmL2Scene.id.in_(supersede_ids))
                    .values(retrievable=False, updated_at=now)
                )
                await db.flush()
            for s in inserts:
                db.add(OrmL2Scene(
                    id=s.id,
                    user_id=self._coerce_uuid(s.user_id),
                    agent_id=s.agent_id or "",
                    name=s.name,
                    summary=s.summary or "",
                    content=s.content,
                    heat=s.heat,
                    version=s.version,
                    source_memory_ids=list(s.source_memory_ids),
                    retrievable=s.retrievable,
                    created_at=s.created_at or now,
                    updated_at=s.updated_at or now,
                ))
            await db.commit()

    async def get(self, scene_id: str) -> L2Scene | None:
        async with self._sf() as db:
            orm = await db.get(OrmL2Scene, scene_id)
            return self._to_scene(orm) if orm else None

    async def get_by_name(
        self, user_id: str, agent_id: str, name: str,
    ) -> L2Scene | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmL2Scene).where(
                    OrmL2Scene.user_id == self._coerce_uuid(user_id),
                    OrmL2Scene.agent_id == (agent_id or ""),
                    OrmL2Scene.name == name,
                    OrmL2Scene.retrievable == True,
                )
            )
            orm = result.scalars().first()
            return self._to_scene(orm) if orm else None

    async def list_by_scope(
        self, user_id: str, agent_id: str, *, retrievable_only: bool = True,
    ) -> list[L2Scene]:
        async with self._sf() as db:
            stmt = select(OrmL2Scene).where(
                OrmL2Scene.user_id == self._coerce_uuid(user_id),
                OrmL2Scene.agent_id == (agent_id or ""),
            )
            if retrievable_only:
                stmt = stmt.where(OrmL2Scene.retrievable == True)
            result = await db.execute(
                stmt.order_by(OrmL2Scene.heat.desc(), OrmL2Scene.updated_at.desc())
            )
            return [self._to_scene(r) for r in result.scalars()]

    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> tuple[list[L2Scene], int]:
        async with self._sf() as db:
            stmt = select(OrmL2Scene).where(
                OrmL2Scene.user_id == self._coerce_uuid(user_id),
                OrmL2Scene.retrievable == True,
            )
            if agent_id is not None:
                stmt = stmt.where(OrmL2Scene.agent_id == agent_id)
            total = (await db.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            result = await db.execute(
                stmt.order_by(OrmL2Scene.heat.desc(), OrmL2Scene.updated_at.desc())
                .offset(offset).limit(limit)
            )
            return [self._to_scene(r) for r in result.scalars()], int(total)

    async def delete(self, scene_id: str) -> bool:
        async with self._sf() as db:
            orm = await db.get(OrmL2Scene, scene_id)
            if orm is None:
                return False
            await db.delete(orm)
            await db.commit()
            return True

    async def get_checkpoint(self, user_id: str, agent_id: str) -> L2Checkpoint | None:
        async with self._sf() as db:
            orm = await db.get(
                OrmL2Checkpoint, (self._coerce_uuid(user_id), agent_id or ""),
            )
            return self._cp_to_pydantic(orm) if orm else None

    async def list_checkpoints(self) -> list[L2Checkpoint]:
        async with self._sf() as db:
            result = await db.execute(select(OrmL2Checkpoint))
            return [self._cp_to_pydantic(r) for r in result.scalars()]

    async def upsert_checkpoint(self, checkpoint: L2Checkpoint) -> None:
        async with self._sf() as db:
            await db.merge(OrmL2Checkpoint(
                user_id=self._coerce_uuid(checkpoint.user_id),
                agent_id=checkpoint.agent_id or "",
                last_memory_at=checkpoint.last_memory_at,
                last_run_at=checkpoint.last_run_at,
                processing_count=checkpoint.processing_count,
                persona_update_request=checkpoint.persona_update_request or "",
                updated_at=datetime.now(timezone.utc),
            ))
            await db.commit()

    @staticmethod
    def _cp_to_pydantic(orm: OrmL2Checkpoint) -> L2Checkpoint:
        return L2Checkpoint(
            user_id=str(orm.user_id),
            agent_id=orm.agent_id or "",
            last_memory_at=orm.last_memory_at,
            last_run_at=orm.last_run_at,
            processing_count=orm.processing_count or 0,
            persona_update_request=orm.persona_update_request or "",
        )


# ═══════════════════════════════════════════════════════════════
# L3PersonaRepo（记忆模块：画像）
# ═══════════════════════════════════════════════════════════════

class PgL3PersonaRepo(L3PersonaRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    @staticmethod
    def _coerce_uuid(value) -> _uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, _uuid.UUID) else _uuid.UUID(str(value))

    @staticmethod
    def _to_persona(orm: OrmL3Persona) -> L3Persona:
        return L3Persona(
            user_id=str(orm.user_id),
            agent_id=orm.agent_id or "",
            content=orm.content,
            version=orm.version or 1,
            memory_count_at_generation=orm.memory_count_at_generation or 0,
            created_at=orm.created_at,
            updated_at=orm.updated_at,
        )

    async def get(self, user_id: str, agent_id: str) -> L3Persona | None:
        async with self._sf() as db:
            orm = await db.get(
                OrmL3Persona, (self._coerce_uuid(user_id), agent_id or ""),
            )
            return self._to_persona(orm) if orm else None

    async def upsert(self, persona: L3Persona) -> None:
        """有旧行则覆盖正文、版本 +1，无旧行则插入。**沿用旧 created_at**。

        刻意不用 db.merge：画像的 version 是"每次重写 +1"，而调用方（生成侧）拿不到旧版本号，
        merge 会把默认的 1 写回去、版本号永远不动。先读旧行再决定插/改，语义才落在库里。
        """
        now = datetime.now(timezone.utc)
        async with self._sf() as db:
            old = await db.get(
                OrmL3Persona, (self._coerce_uuid(persona.user_id), persona.agent_id or ""),
            )
            if old is None:
                db.add(OrmL3Persona(
                    user_id=self._coerce_uuid(persona.user_id),
                    agent_id=persona.agent_id or "",
                    content=persona.content,
                    version=1,
                    memory_count_at_generation=persona.memory_count_at_generation,
                    created_at=persona.created_at or now,
                    updated_at=now,
                ))
            else:
                old.content = persona.content
                old.version = (old.version or 1) + 1
                old.memory_count_at_generation = persona.memory_count_at_generation
                old.updated_at = now
            await db.commit()

    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> tuple[list[L3Persona], int]:
        async with self._sf() as db:
            stmt = select(OrmL3Persona).where(
                OrmL3Persona.user_id == self._coerce_uuid(user_id),
            )
            if agent_id is not None:
                stmt = stmt.where(OrmL3Persona.agent_id == agent_id)
            total = (await db.execute(
                select(func.count()).select_from(stmt.subquery())
            )).scalar() or 0
            result = await db.execute(
                stmt.order_by(OrmL3Persona.updated_at.desc()).offset(offset).limit(limit)
            )
            return [self._to_persona(r) for r in result.scalars()], int(total)

    async def delete(self, user_id: str, agent_id: str) -> bool:
        async with self._sf() as db:
            orm = await db.get(
                OrmL3Persona, (self._coerce_uuid(user_id), agent_id or ""),
            )
            if orm is None:
                return False
            await db.delete(orm)
            await db.commit()
            return True


# ═══════════════════════════════════════════════════════════════
# UserSkillRepo
# ═══════════════════════════════════════════════════════════════

class UserSkillRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    def _to_pydantic(self, orm: OrmUserSkill):
        return orm.to_skill_definition()

    async def install(self, user_id: _uuid.UUID, skill) -> None:
        """安装 Skill（从 Hub 条目创建 user_skill 记录）。"""
        from server.skills.skill_registry import SkillDefinition
        async with self._sf() as db:
            existing = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.skill_id == skill.skill_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.is_installed = True
            else:
                db.add(OrmUserSkill(
                    user_id=user_id,
                    skill_id=skill.skill_id,
                    skill_name=skill.skill_name,
                    description=skill.description,
                    folder_path=skill.folder_path,
                    version=skill.version,
                    category=skill.category,
                    icon=skill.icon,
                    author=skill.author,
                    tags=skill.tags if isinstance(skill.tags, list) else [],
                    is_installed=True,
                    is_custom=False,
                ))
            await db.commit()

    async def uninstall(self, user_id: _uuid.UUID, skill_id: str) -> None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.skill_id == skill_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                if row.is_custom:
                    await db.delete(row)
                else:
                    row.is_installed = False
                await db.commit()

    async def get_installed(self, user_id: _uuid.UUID) -> list:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.is_installed == True,
                )
            )
            return [row.to_skill_definition() for row in result.scalars()]

    async def is_installed(self, user_id: _uuid.UUID, skill_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.skill_id == skill_id,
                    OrmUserSkill.is_installed == True,
                )
            )
            return result.scalar_one_or_none() is not None

    async def create_custom(self, user_id: _uuid.UUID, skill) -> str:
        """创建自定义 Skill，返回生成的 csN ID。"""
        from server.skills.skill_registry import SkillDefinition
        async with self._sf() as db:
            # 找最大 cs 编号
            result = await db.execute(
                select(OrmUserSkill.skill_id).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.is_custom == True,
                )
            )
            max_idx = 0
            for (sid,) in result:
                if sid.startswith("cs"):
                    try:
                        max_idx = max(max_idx, int(sid[2:]))
                    except ValueError:
                        pass
            new_id = f"cs{max_idx + 1}"
            skill.skill_id = new_id
            db.add(OrmUserSkill(
                user_id=user_id,
                skill_id=skill.skill_id,
                skill_name=skill.skill_name,
                description=skill.description,
                folder_path=skill.folder_path,
                version=skill.version,
                category=skill.category,
                icon=skill.icon,
                author=skill.author,
                tags=skill.tags if isinstance(skill.tags, list) else [],
                is_installed=True,
                is_custom=True,
            ))
            await db.commit()
            return new_id

    async def update_custom(self, user_id: _uuid.UUID, skill_id: str, skill) -> bool:
        """更新自定义 Skill。不存在返回 False。"""
        from server.skills.skill_registry import SkillDefinition
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.skill_id == skill_id,
                    OrmUserSkill.is_custom == True,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            row.skill_name = skill.skill_name
            row.description = skill.description
            row.folder_path = skill.folder_path
            if skill.version:
                row.version = skill.version
            row.category = skill.category
            row.icon = skill.icon
            row.tags = skill.tags if isinstance(skill.tags, list) else row.tags
            row.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def delete_custom(self, user_id: _uuid.UUID, skill_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.skill_id == skill_id,
                    OrmUserSkill.is_custom == True,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def get_custom(self, user_id: _uuid.UUID) -> list:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserSkill).where(
                    OrmUserSkill.user_id == user_id,
                    OrmUserSkill.is_custom == True,
                )
            )
            return [row.to_skill_definition() for row in result.scalars()]


# ═══════════════════════════════════════════════════════════════
# UserMcpRepo
# ═══════════════════════════════════════════════════════════════

class UserMcpRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def install(self, user_id: _uuid.UUID, entry: dict) -> None:
        async with self._sf() as db:
            server_id = entry.get("server_id") or entry.get("server_name", "")
            existing = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.server_id == server_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row:
                row.is_installed = True
            else:
                db.add(OrmUserMcpServer(
                    user_id=user_id,
                    server_id=server_id,
                    server_name=entry.get("server_name", ""),
                    description=entry.get("description", ""),
                    icon=entry.get("icon", "🔌"),
                    category=entry.get("category", "通用"),
                    transport=entry.get("transport", "stdio"),
                    command=entry.get("command"),
                    args=entry.get("args", []) if isinstance(entry.get("args"), list) else [],
                    url=entry.get("url"),
                    env=entry.get("env", {}) if isinstance(entry.get("env"), dict) else {},
                    is_installed=True,
                    is_custom=False,
                ))
            await db.commit()

    async def uninstall(self, user_id: _uuid.UUID, server_id: str) -> None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.server_id == server_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                if row.is_custom:
                    await db.delete(row)
                else:
                    row.is_installed = False
                await db.commit()

    async def save_tools(self, user_id: _uuid.UUID, server_id: str, tools: list[dict]) -> None:
        """保存/更新某 MCP 服务的工具列表。传空 list 即清空。"""
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.server_id == server_id,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                row.tools = tools
                await db.commit()

    async def get_user_tools(self, user_id: _uuid.UUID) -> list[dict]:
        """获取某用户所有已安装 MCP 服务的工具列表（带前缀的扁平列表）。"""
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.is_installed == True,
                )
            )
            all_tools: list[dict] = []
            for row in result.scalars():
                for tool in (row.tools or []):
                    tool_copy = dict(tool)
                    tool_copy["name"] = f"{row.server_id}_{tool['name']}"
                    all_tools.append(tool_copy)
            return all_tools

    async def get_installed(self, user_id: _uuid.UUID) -> list[dict]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.is_installed == True,
                )
            )
            return [row.to_dict() for row in result.scalars()]

    async def is_installed(self, user_id: _uuid.UUID, server_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.server_id == server_id,
                    OrmUserMcpServer.is_installed == True,
                )
            )
            return result.scalar_one_or_none() is not None

    async def create_custom(self, user_id: _uuid.UUID, entry: dict) -> dict:
        async with self._sf() as db:
            # 找最大 cm 编号
            result = await db.execute(
                select(OrmUserMcpServer.server_id).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.is_custom == True,
                )
            )
            max_idx = 0
            for (sid,) in result:
                if sid.startswith("cm"):
                    try:
                        max_idx = max(max_idx, int(sid[2:]))
                    except ValueError:
                        pass
            server_id = f"cm{max_idx + 1}"
            orm = OrmUserMcpServer(
                user_id=user_id,
                server_id=server_id,
                server_name=entry.get("server_name", ""),
                description=entry.get("description", ""),
                icon=entry.get("icon", "🔌"),
                category=entry.get("category", "自定义"),
                transport=entry.get("transport", "stdio"),
                command=entry.get("command"),
                args=entry.get("args", []) if isinstance(entry.get("args"), list) else [],
                url=entry.get("url"),
                env=entry.get("env", {}) if isinstance(entry.get("env"), dict) else {},
                is_installed=True,
                is_custom=True,
            )
            db.add(orm)
            await db.commit()
            return orm.to_dict()

    async def delete_custom(self, user_id: _uuid.UUID, server_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.server_id == server_id,
                    OrmUserMcpServer.is_custom == True,
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True

    async def get_custom(self, user_id: _uuid.UUID) -> list[dict]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmUserMcpServer).where(
                    OrmUserMcpServer.user_id == user_id,
                    OrmUserMcpServer.is_custom == True,
                )
            )
            return [row.to_dict() for row in result.scalars()]


# ═══════════════════════════════════════════════════════════════
# SkillHubRepo
# ═══════════════════════════════════════════════════════════════

class SkillHubRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def list_all(self) -> list:
        async with self._sf() as db:
            result = await db.execute(select(OrmSkillHub).order_by(OrmSkillHub.skill_id))
            return [row.to_skill_definition() for row in result.scalars()]

    async def get_by_id(self, skill_id: str):
        async with self._sf() as db:
            result = await db.execute(
                select(OrmSkillHub).where(OrmSkillHub.skill_id == skill_id)
            )
            row = result.scalar_one_or_none()
            return row.to_skill_definition() if row else None

    async def create(self, skill) -> str:
        async with self._sf() as db:
            orm = OrmSkillHub(
                skill_id=skill.skill_id,
                skill_name=skill.skill_name,
                description=skill.description,
                folder_path=skill.folder_path,
                version=skill.version,
                category=skill.category,
                icon=skill.icon,
                author=skill.author,
                tags=skill.tags if isinstance(skill.tags, list) else [],
            )
            db.add(orm)
            await db.commit()
            return skill.skill_id

    async def update(self, skill_id: str, skill) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmSkillHub).where(OrmSkillHub.skill_id == skill_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            row.skill_name = skill.skill_name
            row.description = skill.description
            row.folder_path = skill.folder_path
            row.version = skill.version
            row.category = skill.category
            row.icon = skill.icon
            row.author = skill.author
            row.tags = skill.tags if isinstance(skill.tags, list) else row.tags
            row.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def delete(self, skill_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmSkillHub).where(OrmSkillHub.skill_id == skill_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True


# ═══════════════════════════════════════════════════════════════
# McpHubRepo
# ═══════════════════════════════════════════════════════════════

class McpHubRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def list_all(self) -> list[dict]:
        async with self._sf() as db:
            result = await db.execute(select(OrmMcpHub).order_by(OrmMcpHub.server_id))
            return [row.to_dict() for row in result.scalars()]

    async def get_by_id(self, server_id: str) -> dict | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMcpHub).where(OrmMcpHub.server_id == server_id)
            )
            row = result.scalar_one_or_none()
            return row.to_dict() if row else None

    async def create(self, entry: dict) -> dict:
        async with self._sf() as db:
            orm = OrmMcpHub(
                server_id=entry.get("server_id", ""),
                server_name=entry.get("server_name", ""),
                description=entry.get("description", ""),
                icon=entry.get("icon", "🔌"),
                category=entry.get("category", "通用"),
                transport=entry.get("transport", "stdio"),
                command=entry.get("command"),
                args=entry.get("args", []) if isinstance(entry.get("args"), list) else [],
                url=entry.get("url"),
                env=entry.get("env", {}) if isinstance(entry.get("env"), dict) else {},
            )
            db.add(orm)
            await db.commit()
            return orm.to_dict()

    async def update(self, server_id: str, entry: dict) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMcpHub).where(OrmMcpHub.server_id == server_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            if "server_name" in entry:
                row.server_name = entry["server_name"]
            if "description" in entry:
                row.description = entry["description"]
            if "icon" in entry:
                row.icon = entry["icon"]
            if "category" in entry:
                row.category = entry["category"]
            if "transport" in entry:
                row.transport = entry["transport"]
            if "command" in entry:
                row.command = entry["command"]
            if "args" in entry:
                row.args = entry["args"]
            if "url" in entry:
                row.url = entry["url"]
            if "env" in entry:
                row.env = entry["env"]
            row.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def delete(self, server_id: str) -> bool:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmMcpHub).where(OrmMcpHub.server_id == server_id)
            )
            row = result.scalar_one_or_none()
            if not row:
                return False
            await db.delete(row)
            await db.commit()
            return True


# ═══════════════════════════════════════════════════════════════
# ExpertHubRepo
# ═══════════════════════════════════════════════════════════════

class ExpertHubRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get_by_id(self, id: _uuid.UUID) -> OrmExpertHub | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmExpertHub).where(OrmExpertHub.id == id)
            )
            return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> OrmExpertHub | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmExpertHub).where(OrmExpertHub.name == name)
            )
            return result.scalar_one_or_none()

    async def list_all(self) -> list[OrmExpertHub]:
        async with self._sf() as db:
            result = await db.execute(select(OrmExpertHub).order_by(OrmExpertHub.name))
            return list(result.scalars())


# ═══════════════════════════════════════════════════════════════
# TeamHubRepo
# ═══════════════════════════════════════════════════════════════

class TeamHubRepo:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get_by_id(self, id: _uuid.UUID) -> OrmExpertTeamHub | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmExpertTeamHub).where(OrmExpertTeamHub.id == id)
            )
            return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> OrmExpertTeamHub | None:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmExpertTeamHub).where(OrmExpertTeamHub.name == name)
            )
            return result.scalar_one_or_none()

    async def list_all(self) -> list[OrmExpertTeamHub]:
        async with self._sf() as db:
            result = await db.execute(select(OrmExpertTeamHub).order_by(OrmExpertTeamHub.name))
            return list(result.scalars())


# ═══════════════════════════════════════════════════════════════
# ToolInvocationRepo（阶段 C-1 工具账本）
# ═══════════════════════════════════════════════════════════════

class ToolInvocationRepo(ToolInvocationRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    @staticmethod
    def _coerce_inv(invocation_id) -> _uuid.UUID:
        if isinstance(invocation_id, _uuid.UUID):
            return invocation_id
        return _uuid.UUID(str(invocation_id))

    @staticmethod
    def _coerce_sid(session_id) -> _uuid.UUID:
        if isinstance(session_id, str):
            return _uuid.UUID(session_id)
        return session_id

    async def create(self, invocation: ToolInvocation) -> None:
        async with self._sf() as db:
            existing = (await db.execute(
                select(OrmToolInvocation.invocation_id).where(
                    OrmToolInvocation.session_id == self._coerce_sid(invocation.session_id),
                    OrmToolInvocation.invocation_id == invocation.invocation_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                return  # 幂等：同 (session, invocation_id) 已存在则忽略
            db.add(OrmToolInvocation(
                invocation_id=invocation.invocation_id,
                session_id=self._coerce_sid(invocation.session_id),
                message_id=invocation.message_id,
                turn=invocation.turn,
                attempt=invocation.attempt,
                tool_name=invocation.tool_name,
                location=invocation.location,
                state=invocation.state.value,
                input=invocation.input or {},
                result=invocation.result,
                side_effect=invocation.side_effect,
                idempotency=invocation.idempotency,
                requires_approval=invocation.requires_approval,
                error=invocation.error,
            ))
            await db.commit()

    async def get(self, session_id, invocation_id) -> ToolInvocation | None:
        async with self._sf() as db:
            orm = (await db.execute(
                select(OrmToolInvocation).where(
                    OrmToolInvocation.session_id == self._coerce_sid(session_id),
                    OrmToolInvocation.invocation_id == self._coerce_inv(invocation_id),
                )
            )).scalar_one_or_none()
            return orm.to_pydantic() if orm else None

    async def mark(
        self, session_id, invocation_id, state: str,
        *, result: dict | None = None, error: str | None = None,
    ) -> bool:
        async with self._sf() as db:
            orm = (await db.execute(
                select(OrmToolInvocation).where(
                    OrmToolInvocation.session_id == self._coerce_sid(session_id),
                    OrmToolInvocation.invocation_id == self._coerce_inv(invocation_id),
                )
            )).scalar_one_or_none()
            if orm is None:
                return False
            if orm.state == "completed":
                return state == "completed"  # completed 不可回退；重复 mark completed 幂等
            if state == "issued":
                return False  # 不可回到 issued
            orm.state = state
            if state == "completed":
                orm.result = result
                orm.error = None  # completed 即已如实落 result；error 只表状态机异常，成功与否看 result.success
            else:
                orm.error = error
                if result is not None:
                    orm.result = result
            orm.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return True

    async def list_issued_by_message(
        self, session_id, message_id,
    ) -> list[ToolInvocation]:
        async with self._sf() as db:
            result = await db.execute(
                select(OrmToolInvocation).where(
                    OrmToolInvocation.session_id == self._coerce_sid(session_id),
                    OrmToolInvocation.message_id == self._coerce_sid(message_id),
                    OrmToolInvocation.state == "issued",
                ).order_by(OrmToolInvocation.created_at)
            )
            return [r.to_pydantic() for r in result.scalars()]

    async def max_attempt(self, session_id, message_id) -> int:
        async with self._sf() as db:
            v = (await db.execute(
                select(func.coalesce(func.max(OrmToolInvocation.attempt), 0)).where(
                    OrmToolInvocation.session_id == self._coerce_sid(session_id),
                    OrmToolInvocation.message_id == self._coerce_sid(message_id),
                )
            )).scalar_one()
            return int(v or 0)

    async def list_invocations(
        self, session_id, message_id=None,
    ) -> list[ToolInvocation]:
        """全部受账工具调用（D 工具动作账本）：按时间升序，可选按消息过滤。

        不限 state / side_effect——四态与读/写都返回，由展示侧分辨。
        """
        cond = [OrmToolInvocation.session_id == self._coerce_sid(session_id)]
        if message_id is not None:
            cond.append(
                OrmToolInvocation.message_id == self._coerce_sid(message_id),
            )
        async with self._sf() as db:
            result = await db.execute(
                select(OrmToolInvocation).where(*cond)
                .order_by(OrmToolInvocation.created_at)
            )
            return [r.to_pydantic() for r in result.scalars()]
