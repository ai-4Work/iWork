from __future__ import annotations
import asyncio
from uuid import UUID
from server.models.session import Session, SessionStatus
from server.models.message import Message, MessageStatus
from server.models.mail import RUNNABLE_MSG_TYPES, RESULT_MSG_TYPES
from server.models.tool_invocation import ToolInvocation, InvocationState
from server.storage.base import SessionRepository, MessageRepository, ToolInvocationRepository


class InMemorySessionRepo(SessionRepository):
    """会话仓储内存实现。单进程下安全，后续换 PG 接口不变。"""

    def __init__(self):
        self._sessions: dict[UUID, Session] = {}

    async def create(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: UUID) -> Session | None:
        return self._sessions.get(session_id)

    async def update(self, session: Session) -> Session:
        self._sessions[session.id] = session
        return session

    async def list_active(self, user_id: str | None = None) -> list[Session]:
        sessions = [s for s in self._sessions.values() if s.status == SessionStatus.ACTIVE]
        if user_id:
            sessions = [s for s in sessions if s.user_id == user_id]
        return sessions

    async def list_by_user(
        self, user_id: str, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Session], int]:
        sessions = [s for s in self._sessions.values() if s.user_id == user_id]
        if status and status != "all":
            sessions = [s for s in sessions if s.status == status]
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        total = len(sessions)
        return sessions[offset:offset + limit], total

    async def archive(self, session_id: UUID) -> None:
        s = self._sessions.get(session_id)
        if s:
            s.status = SessionStatus.ARCHIVED

    async def list_by_parent(self, parent_id: UUID) -> list[Session]:
        return [s for s in self._sessions.values() if s.parent_id == parent_id]

    async def get_by_path(
        self, root_session_id: UUID, agent_path: str,
    ) -> Session | None:
        for s in self._sessions.values():
            if s.root_session_id == root_session_id and s.agent_path == agent_path:
                return s
        return None


class InMemoryMessageRepo(MessageRepository):
    """消息仓储内存实现。使用 asyncio.Lock 保证入队/出队原子性。"""

    def __init__(self):
        self._messages: dict[UUID, Message] = {}
        self._lock = asyncio.Lock()
        self._history: dict[UUID, list[dict]] = {}
        self._history_seq: dict[UUID, int] = {}

    async def create(self, message: Message) -> Message:
        self._messages[message.id] = message
        return message

    async def get(self, message_id: UUID) -> Message | None:
        return self._messages.get(message_id)

    async def get_by_client_message_id(
        self, session_id: UUID, client_message_id: str,
    ) -> Message | None:
        for m in self._messages.values():
            if m.session_id == session_id and m.client_message_id == client_message_id:
                return m
        return None

    async def update(self, message: Message) -> Message:
        self._messages[message.id] = message
        return message

    async def dequeue_next(self, session_id: UUID) -> Message | None:
        async with self._lock:
            pending = [
                m for m in self._messages.values()
                if m.session_id == session_id
                and m.status == MessageStatus.PENDING
                and m.msg_type in RUNNABLE_MSG_TYPES
            ]
            if not pending:
                return None
            pending.sort(key=lambda m: m.queue_position or 999)
            msg = pending[0]
            msg.status = MessageStatus.PROCESSING
            msg.queue_position = None
            await self.renumber_queue(session_id)
            return msg

    async def list_pending(self, session_id: UUID) -> list[Message]:
        return [
            m for m in self._messages.values()
            if m.session_id == session_id
            and m.status == MessageStatus.PENDING
            and m.msg_type in RUNNABLE_MSG_TYPES
        ]

    async def list_pending_results(
        self, session_id: UUID, recipient_agent_id: str,
    ) -> list[Message]:
        rows = [
            m for m in self._messages.values()
            if m.session_id == session_id
            and m.status == MessageStatus.PENDING
            and m.msg_type in RESULT_MSG_TYPES
            and m.recipient_agent_id == recipient_agent_id
        ]
        rows.sort(key=lambda m: m.created_at)
        return rows

    async def count_pending(self, session_id: UUID) -> int:
        return len(await self.list_pending(session_id))

    async def cancel_pending(self, message_id: UUID, session_id: UUID) -> bool:
        msg = self._messages.get(message_id)
        if msg and msg.session_id == session_id and msg.status == MessageStatus.PENDING:
            msg.status = MessageStatus.CANCELLED
            msg.queue_position = None
            await self.renumber_queue(session_id)
            return True
        return False

    async def renumber_queue(self, session_id: UUID) -> None:
        pending = sorted(
            [m for m in self._messages.values()
             if m.session_id == session_id
             and m.status == MessageStatus.PENDING
             and m.msg_type in RUNNABLE_MSG_TYPES],
            key=lambda m: m.queue_position or 999
        )
        for i, m in enumerate(pending, start=1):
            m.queue_position = i

    async def list_processing(self) -> list[Message]:
        return [m for m in self._messages.values() if m.status == MessageStatus.PROCESSING]

    # ── Conversation history (ContextManager 使用) ──

    async def append_message(self, session_id: UUID, msg: dict) -> None:
        seq = self._history_seq.get(session_id, 0) + 1
        self._history_seq[session_id] = seq
        entry = dict(msg)
        entry.setdefault("sequence", seq)
        self._history.setdefault(session_id, []).append(entry)

    async def get_history(self, session_id: UUID) -> list[dict]:
        return list(self._history.get(session_id, []))

    async def append_tool_call_to_last_assistant(
        self, session_id: UUID, tool_call_block: dict,
    ) -> None:
        history = self._history.get(session_id, [])
        for row in reversed(history):
            if row.get("role") == "assistant":
                row.setdefault("tool_calls", []).append(tool_call_block)
                return
        await self.append_message(session_id, {
            "role": "assistant", "content": None, "tool_calls": [tool_call_block],
        })

    async def clear_history(self, session_id: UUID) -> None:
        self._history.pop(session_id, None)
        self._history_seq.pop(session_id, None)

    async def list_rows(self, session_id: UUID) -> list[dict]:
        """列出会话全部历史行（含 message_id/turn/sequence 元数据），按 sequence 升序。"""
        return [
            {
                "sequence": e.get("sequence"),
                "role": e.get("role"),
                "content": e.get("content"),
                "reasoning_content": e.get("reasoning_content"),
                "tool_calls": e.get("tool_calls"),
                "tool_call_id": e.get("tool_call_id"),
                "message_id": str(e.get("message_id")) if e.get("message_id") else None,
                "turn": e.get("turn"),
                "created_at": None,
            }
            for e in list(self._history.get(session_id, []))
        ]

    async def truncate_after(self, session_id: UUID, boundary_sequence: int) -> None:
        """删除 sequence > boundary_sequence 的所有历史行（重跑/截断用）。"""
        rows = self._history.get(session_id, [])
        kept = [e for e in rows if (e.get("sequence") or 0) <= boundary_sequence]
        self._history[session_id] = kept

    async def truncate_message_after(
        self, session_id: UUID, message_id, boundary_sequence: int,
    ) -> None:
        """删除本消息在 boundary 之后的历史行（M4 regenerate：只清理该消息自身旧回复/工具痕迹）。

        保留：sequence <= boundary 的所有行（含本消息 user 行锚点与更早消息），以及
        message_id != 本消息 的行（后续消息不受影响）。
        """
        mid = str(message_id)
        rows = self._history.get(session_id, [])
        kept = [
            e for e in rows
            if (e.get("sequence") or 0) <= boundary_sequence
            or e.get("message_id") != mid
        ]
        self._history[session_id] = kept


class InMemoryToolInvocationRepo(ToolInvocationRepository):
    """工具调用账本内存实现（测试 / 无 PG 环境）。"""

    def __init__(self):
        self._rows: dict[tuple[UUID, UUID], ToolInvocation] = {}

    @staticmethod
    def _key(session_id, invocation_id) -> tuple[UUID, UUID]:
        sid = session_id if isinstance(session_id, UUID) else UUID(session_id)
        inv = invocation_id if isinstance(invocation_id, UUID) else UUID(str(invocation_id))
        return sid, inv

    async def create(self, invocation: ToolInvocation) -> None:
        key = self._key(invocation.session_id, invocation.invocation_id)
        if key not in self._rows:
            self._rows[key] = invocation

    async def get(self, session_id, invocation_id) -> ToolInvocation | None:
        return self._rows.get(self._key(session_id, invocation_id))

    async def mark(
        self, session_id, invocation_id, state: str,
        *, result: dict | None = None, error: str | None = None,
    ) -> bool:
        from datetime import datetime, timezone
        inv = self._rows.get(self._key(session_id, invocation_id))
        if inv is None:
            return False
        if inv.state == InvocationState.COMPLETED:
            return state == InvocationState.COMPLETED  # completed 不可回退；重复 mark completed 幂等
        if state == InvocationState.ISSUED:
            return False  # 不可回到 issued
        inv.state = InvocationState(state)
        if state == InvocationState.COMPLETED:
            inv.result = result
            inv.error = None  # completed 即已如实落 result，成功与否看 result.success；error 只表状态机异常
            inv.completed_at = datetime.now(timezone.utc)
        else:
            inv.error = error
            if result is not None:
                inv.result = result
            inv.completed_at = datetime.now(timezone.utc)
        return True

    async def list_issued_by_message(self, session_id, message_id) -> list[ToolInvocation]:
        sid = session_id if isinstance(session_id, UUID) else UUID(session_id)
        mid = message_id if isinstance(message_id, UUID) else UUID(message_id)
        return [
            inv for (s, _), inv in self._rows.items()
            if s == sid and inv.message_id == mid and inv.state == InvocationState.ISSUED
        ]

    async def max_attempt(self, session_id, message_id) -> int:
        sid = session_id if isinstance(session_id, UUID) else UUID(session_id)
        mid = message_id if isinstance(message_id, UUID) else UUID(message_id)
        return max(
            (
                inv.attempt or 0
                for (s, _), inv in self._rows.items()
                if s == sid and inv.message_id == mid
            ),
            default=0,
        )

    async def list_invocations(
        self, session_id, message_id=None,
    ) -> list[ToolInvocation]:
        sid = session_id if isinstance(session_id, UUID) else UUID(session_id)
        mid = message_id if isinstance(message_id, UUID) else (
            UUID(message_id) if message_id is not None else None
        )
        rows = [
            inv for (s, _), inv in self._rows.items()
            if s == sid and (mid is None or inv.message_id == mid)
        ]
        rows.sort(key=lambda inv: inv.created_at)
        return rows


class InMemoryOffloadedBlocksRepo:
    """卸载块仓储内存实现（测试用）。接口与 OffloadedBlocksRepo 对齐。"""

    def __init__(self):
        self._blocks: dict[UUID, list] = {}

    async def insert(self, session_id: UUID, block) -> None:
        self._blocks.setdefault(session_id, []).append(block)

    async def list_by_session(self, session_id: UUID) -> list:
        return list(self._blocks.get(session_id, []))

    async def delete_by_session(self, session_id: UUID) -> None:
        self._blocks.pop(session_id, None)
