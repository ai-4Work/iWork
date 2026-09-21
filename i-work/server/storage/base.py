from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID
from server.memory.types import L1Memory, L1Checkpoint
from server.memory.l2.types import L2Scene, L2Checkpoint
from server.memory.l3.types import L3Persona
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


class L1MemoryRepository(ABC):
    """L1 原子记忆仓储抽象（记忆模块）。

    写入只有一条路径 apply_batch：抽取结果的一批 insert 与被 update/merge 取代的
    一批 supersede 必须在同一事务里落地，否则会出现"新行已进、旧行未软删"的
    双份可检索状态。删除（硬删）只留给用户在客户端手工删除。

    作用域是 user_id + agent_id（设计文档的 teamId 缺省降级分支）。
    """

    @abstractmethod
    async def apply_batch(
        self, inserts: list[L1Memory], supersede_ids: list[str],
    ) -> None:
        """原子落地一批抽取结果：插入新行 + 把被取代的旧行 retrievable 置 false。"""
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> L1Memory | None: ...

    @abstractmethod
    async def list_by_scope(
        self, user_id: str, agent_id: str, *, retrievable_only: bool = True,
    ) -> list[L1Memory]:
        """取某作用域下的全部记忆，供检索侧建索引。"""
        ...

    @abstractmethod
    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        memory_type: str | None = None, limit: int = 200, offset: int = 0,
    ) -> tuple[list[L1Memory], int]:
        """分页列出（客户端展示用）。只返回 retrievable=true，按 updated_at 降序。"""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """硬删（用户手删：这条记忆本就不该存在）。不存在返回 False。"""
        ...

    # ── L2 整合侧的两个读口 ─────────────────────────────────────

    @abstractmethod
    async def list_scopes(self) -> list[tuple[str, str]]:
        """有可检索记忆的 distinct (user_id, agent_id) 作用域，L2 sweep 巡检用。"""
        ...

    @abstractmethod
    async def list_since(
        self, user_id: str, agent_id: str, since: datetime | None,
        limit: int = 20,
    ) -> list[L1Memory]:
        """取某作用域下 updated_at > since 的记忆，按 updated_at 升序（L2 的增量输入）。

        since 为 None 表示冷启动（无游标），从最早一条起算。
        """

    @abstractmethod
    async def count_by_scope(self, user_id: str, agent_id: str) -> int:
        """某作用域下可检索记忆的条数（L3 的 P4 阈值据它算"自上次画像以来的增量"）。"""

        ...


class L2SceneRepository(ABC):
    """L2 场景仓储抽象（记忆模块）。

    写入只有一条路径 apply_batch：一批动作产出的 insert 与被 merge 取代的 supersede 必须在
    同一事务里落地，否则会出现"新场景已进、旧场景未软删"的双份可导航状态（同 L1 的告诫）。

    作用域是 user_id + agent_id（设计文档 teamId 缺省降级分支）。名字在作用域内唯一
    （仅约束 retrievable 行），因为 LLM 用名字引用场景，工程侧要把名字解析成行。
    """

    @abstractmethod
    async def apply_batch(
        self, inserts: list[L2Scene], supersede_ids: list[str],
    ) -> None:
        """原子落地一批整合结果：插入新行 + 把被取代的旧行 retrievable 置 false。"""
        ...

    @abstractmethod
    async def get(self, scene_id: str) -> L2Scene | None: ...

    @abstractmethod
    async def get_by_name(
        self, user_id: str, agent_id: str, name: str,
    ) -> L2Scene | None:
        """按名字取（scene_read 工具与 LLM 动作的目标解析都用它）。只找可检索的行。"""
        ...

    @abstractmethod
    async def list_by_scope(
        self, user_id: str, agent_id: str, *, retrievable_only: bool = True,
    ) -> list[L2Scene]:
        """取某作用域下的全部场景，供导航渲染与候选选取。"""
        ...

    @abstractmethod
    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> tuple[list[L2Scene], int]:
        """分页列出（客户端展示用）。只返回 retrievable=true，按热度降序。"""
        ...

    @abstractmethod
    async def delete(self, scene_id: str) -> bool:
        """硬删（用户手删：这个场景本就不该存在）。不存在返回 False。"""
        ...

    # ── 整合游标 ────────────────────────────────────────────────

    @abstractmethod
    async def get_checkpoint(self, user_id: str, agent_id: str) -> L2Checkpoint | None: ...

    @abstractmethod
    async def list_checkpoints(self) -> list[L2Checkpoint]:
        """全量游标，sweep 巡检用。"""
        ...

    @abstractmethod
    async def upsert_checkpoint(self, checkpoint: L2Checkpoint) -> None: ...


class L3PersonaRepository(ABC):
    """L3 画像仓储抽象（记忆模块）。

    写入只有一条路径 upsert：一行画像一个作用域，有旧行则覆盖正文、版本 +1，无旧行则插入。
    没有游标表 —— 画像行本身既是产物也是游标（`updated_at` 供筛变化场景，
    `memory_count_at_generation` 供算 P4 增量），因此这套接口没有 checkpoint 三件套。

    作用域是 user_id + agent_id（同 L1 / L2 的 teamId 降级分支）。
    """

    @abstractmethod
    async def get(self, user_id: str, agent_id: str) -> L3Persona | None:
        """读某作用域的画像行。返回 None = 从未生成过（首次 / P2 冷启动据此判断）。"""
        ...

    @abstractmethod
    async def upsert(self, persona: L3Persona) -> None:
        """原子落地一行画像：有旧行则覆盖 content、version +1、刷新 memory_count 与
        updated_at（**沿用旧 created_at**）；无旧行则插入（version = 1）。"""
        ...

    @abstractmethod
    async def list_page(
        self, user_id: str, *, agent_id: str | None = None,
        limit: int = 200, offset: int = 0,
    ) -> tuple[list[L3Persona], int]:
        """分页列出（客户端展示用），按 updated_at 降序。"""
        ...

    @abstractmethod
    async def delete(self, user_id: str, agent_id: str) -> bool:
        """硬删（用户手删：这份画像本就不该存在）。不存在返回 False。"""
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
