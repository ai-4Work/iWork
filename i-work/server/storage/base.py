from __future__ import annotations
from abc import ABC, abstractmethod
from uuid import UUID
from server.models.session import Session
from server.models.message import Message
from server.models.tool_invocation import ToolInvocation


class SessionRepository(ABC):
    """会话仓储抽象。当前用 InMemory，后续换 PgSessionRepo 接口不变。"""

    @abstractmethod
    async def create(self, session: Session) -> Session: ...

    @abstractmethod
    async def get(self, session_id: UUID) -> Session | None: ...

    @abstractmethod
    async def update(self, session: Session) -> Session: ...

    @abstractmethod
    async def list_active(self, user_id: str | None = None) -> list[Session]: ...

    @abstractmethod
    async def list_by_user(
        self, user_id: str, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> tuple[list[Session], int]: ...

    @abstractmethod
    async def archive(self, session_id: UUID) -> None: ...

    @abstractmethod
    async def list_by_parent(self, parent_id: UUID) -> list[Session]: ...

    @abstractmethod
    async def get_by_path(
        self, root_session_id: UUID, agent_path: str,
    ) -> Session | None:
        """按 (agent 树顶层会话, 路径) 寻址子会话（§9.11.4）。

        派发的 task 只带路径、不带 session id —— 同一路径的 followup 复用既有子
        会话就是靠这个查回来的。
        """
        ...


class MessageRepository(ABC):
    """消息仓储抽象。消息队列以存储为数据源，引擎通过 dequeue_next 原子出队。
    SKIP LOCKED 等并发控制由具体实现（PgMessageRepo）负责。"""

    @abstractmethod
    async def create(self, message: Message) -> Message: ...

    @abstractmethod
    async def get(self, message_id: UUID) -> Message | None: ...

    @abstractmethod
    async def get_by_client_message_id(
        self, session_id: UUID, client_message_id: str,
    ) -> Message | None:
        """按 (session_id, client_message_id) 幂等键查已存在消息（摄入幂等用）。"""
        ...

    @abstractmethod
    async def update(self, message: Message) -> Message: ...

    @abstractmethod
    async def dequeue_next(self, session_id: UUID) -> Message | None:
        """原子出队：队首 pending → processing，返回消息或 None"""
        ...

    @abstractmethod
    async def list_pending(self, session_id: UUID) -> list[Message]:
        """查询排队中消息，按 queue_position 排序。

        只含**可跑**类型（user/task/followup）——result/status 信封不占队列位，
        不能出现在 /queue 里，否则前端会渲染出幻影队列项（§9.11.12 #2）。
        """
        ...

    @abstractmethod
    async def list_pending_results(
        self, session_id: UUID, recipient_agent_id: str,
    ) -> list[Message]:
        """取收件人邮箱里待消费的结果信封（result/status），按入队时间升序。

        与 list_pending 是一对互斥视图：信封在 list_pending 里看不到，队列消息
        在这里看不到。父 agent 挂起等子时用的就是这个（§9.11.8）。
        """
        ...

    @abstractmethod
    async def count_pending(self, session_id: UUID) -> int: ...

    @abstractmethod
    async def cancel_pending(self, message_id: UUID, session_id: UUID) -> bool:
        """用户手动取消排队中的消息，仅对 pending 状态有效"""
        ...

    @abstractmethod
    async def renumber_queue(self, session_id: UUID) -> None:
        """取消/出队后重排剩余消息的 queue_position 为 1,2,3...

        同样只数可跑类型——信封拿到 queue_position 会让用户消息后面凭空多出排位。
        """
        ...

    @abstractmethod
    async def list_processing(self) -> list[Message]:
        """查询所有正在处理的消息（引擎恢复用）"""
        ...


class ToolInvocationRepository(ABC):
    """工具调用账本仓储抽象（阶段 C-1）。

    状态机：issued → completed / skipped / superseded。
    completed 为一次性终态不可回退；skipped/superseded 可从 issued 覆盖，
    但不可覆盖 completed。重复 mark completed 幂等返回 True。
    """

    @abstractmethod
    async def create(self, invocation: "ToolInvocation") -> None:
        """落 issued 记录；同 (session, invocation_id) 已存在则忽略（幂等）。"""
        ...

    @abstractmethod
    async def get(self, session_id: UUID, invocation_id) -> "ToolInvocation | None":
        """按 (session_id, invocation_id) 查账。invocation_id 接受 str 或 UUID。"""
        ...

    @abstractmethod
    async def mark(
        self,
        session_id: UUID,
        invocation_id,
        state: str,
        *,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        """按状态机规则更新。返回是否实际变更（False = unknown/completed 不可回退）。"""
        ...

    @abstractmethod
    async def list_issued_by_message(
        self, session_id: UUID, message_id: UUID,
    ) -> list["ToolInvocation"]:
        """列出某消息下所有 issued 的调用（重跑前 C-1 裁决用）。"""
        ...

    @abstractmethod
    async def max_attempt(
        self, session_id: UUID, message_id: UUID,
    ) -> int:
        """该消息当前最大运行序号 attempt（D）；无任何记录返回 0。"""
        ...

    @abstractmethod
    async def list_invocations(
        self, session_id: UUID, message_id: UUID | None = None,
    ) -> list["ToolInvocation"]:
        """工具动作账本（阶段 D）：该会话全部受账调用，不限 state / side_effect。

        message_id 为空时返回全会话；否则仅该消息。按 created_at 升序。
        每行自带 state 与 side_effect，读/写与四态由展示侧分辨。
        """
        ...
