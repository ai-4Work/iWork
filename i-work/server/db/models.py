from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, BigInteger, Boolean, DateTime, ForeignKey,
    UniqueConstraint, Index, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════
# 1. Users
# ═══════════════════════════════════════════════════════════════

class OrmUser(Base):
    __tablename__ = "users"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="用户唯一标识",
    )
    username: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True,
        comment="登录用户名",
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(200), comment="显示名称",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )


# ═══════════════════════════════════════════════════════════════
# 2. Sessions
# ═══════════════════════════════════════════════════════════════

class OrmSession(Base):
    __tablename__ = "sessions"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        comment="会话唯一标识（客户端生成）",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    title: Mapped[str] = mapped_column(
        String(500), nullable=False, default="新建任务",
        comment="会话标题（首条消息自动设置）",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active",
        comment="状态：active | archived",
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="build",
        comment="模式：ask | plan | build",
    )
    scene_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="office",
        comment="场景：office | code",
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="",
        comment="使用的 LLM 模型",
    )
    workspace: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="工作目录路径",
    )
    shell_env: Mapped[str] = mapped_column(
        String(30), nullable=False, default="",
        comment="客户端执行环境：powershell | zsh | bash（空 = 未上报，不注入 Shell 提示词）",
    )
    client_tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="客户端注册的内置工具列表",
    )
    client_mcp_tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="客户端上报的 MCP 工具清单",
    )
    parent_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True, comment="父会话 ID（子 agent 会话指向主会话）",
    )
    agent_path: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="/root",
        comment="agent 树路径：顶层会话 /root，子会话 /root/{member_id}（§9.11.4）",
    )
    root_session_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="agent 树顶层会话 ID。刻意不加 FK：会话只归档不硬删（§9.11.12 #7）",
    )
    agents: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="多 Agent 配置 [{agent_id, agent_type, role, model, mode, workspace}]",
    )
    current_message_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), comment="当前正在处理的消息 ID",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_user_status", "user_id", "status"),
        Index("idx_sessions_updated_at", "updated_at"),
        # 同一个 agent 树里路径唯一。NULL root（历史行迁移前）在 PG 下互不冲突。
        Index("uq_sessions_root_agent_path", "root_session_id", "agent_path",
              unique=True),
        {"comment": "会话表：每个用户的一次对话会话"},
    )

    # ── Pydantic 转换 ──────────────────────────────────

    def to_pydantic(self):
        from server.models.session import Session, SessionStatus, AgentConfig
        from server.models.session import ClientTool
        return Session(
            id=self.id,
            user_id=str(self.user_id),
            title=self.title,
            status=SessionStatus(self.status),
            mode=self.mode,
            scene_mode=self.scene_mode,
            model=self.model,
            workspace=self.workspace,
            shell_env=self.shell_env or "",
            client_tools=[ClientTool(**t) for t in (self.client_tools or [])],
            client_mcp_tools=list(self.client_mcp_tools or []),
            parent_id=self.parent_id,
            agent_path=self.agent_path,
            root_session_id=self.root_session_id,
            agents=[AgentConfig(**a) for a in (self.agents or [])],
            current_message_id=self.current_message_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @staticmethod
    def from_pydantic(s) -> "OrmSession":
        return OrmSession(
            id=s.id,
            user_id=_uuid.UUID(s.user_id) if isinstance(s.user_id, str) else s.user_id,
            title=s.title,
            status=s.status.value if hasattr(s.status, "value") else s.status,
            mode=s.mode,
            scene_mode=s.scene_mode,
            model=s.model,
            workspace=s.workspace,
            shell_env=s.shell_env or "",
            client_tools=[t.model_dump() for t in (s.client_tools or [])],
            client_mcp_tools=list(s.client_mcp_tools or []),
            parent_id=s.parent_id,
            agent_path=s.agent_path,
            root_session_id=s.root_session_id,
            agents=[a.model_dump() for a in (s.agents or [])],
            current_message_id=s.current_message_id,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )


# ═══════════════════════════════════════════════════════════════
# 3. Messages
# ═══════════════════════════════════════════════════════════════

class OrmMessage(Base):
    __tablename__ = "messages"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        comment="消息唯一标识",
    )
    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, comment="所属会话",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="发送者",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="用户输入文本",
    )
    scene_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="场景模式（记录发送时的快照）",
    )
    workspace: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="工作目录",
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="指定模型",
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="对话模式",
    )
    agent_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="目标 agent id",
    )
    agent_type: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, comment="expert | team",
    )
    files: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="@ 引用的文件路径列表",
    )
    skill_invocations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="/ 调用的 skill 列表 [{skill_id, skill_name, skill_md}]",
    )
    mcp_servers: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="本次消息附加的 MCP 服务 [{server_id, server_name, enabled_tools}]",
    )
    client_message_id: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
        comment="客户端幂等键；(session_id, client_message_id) 唯一，NULL = 未启用去重",
    )
    sender_agent_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="发件人 agent_path（用户直接输入的消息为空）",
    )
    recipient_agent_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="收件人 agent_path（邮箱按它过滤）",
    )
    msg_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default="user",
        comment="user | task | followup | result | status；只有前三种会被出队跑回合",
    )
    cid: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, comment="关联 id：把 result 对回它那条 task",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        comment="处理状态：pending | processing | completed | error | cancelled",
    )
    queue_position: Mapped[Optional[int]] = mapped_column(
        Integer, comment="队列位置（1-based），处理中为 NULL",
    )
    turn_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="LLM 已执行轮数",
    )
    tokens_in: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计输入 token 数",
    )
    tokens_out: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="累计输出 token 数",
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="总执行耗时（毫秒）",
    )
    tool_calls_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="工具调用总次数",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, comment="错误信息",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="入队时间",
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="引擎开始处理时间",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="处理完成时间",
    )

    __table_args__ = (
        Index("idx_messages_session_id", "session_id"),
        Index("idx_messages_session_status", "session_id", "status"),
        Index("idx_messages_session_queue", "session_id", "queue_position",
              postgresql_where=(queue_position.isnot(None))),
        Index("idx_messages_status", "status"),
        Index("uq_messages_session_client_msg", "session_id", "client_message_id",
              unique=True, postgresql_where=(client_message_id.isnot(None))),
        # 邮箱取信：该会话里发给我、还没被取走的信封（§9.11.7）
        Index("idx_messages_mailbox", "session_id", "recipient_agent_id", "status"),
        {"comment": "消息表：用户发送的请求记录，含处理状态和统计元数据"},
    )

    def to_pydantic(self):
        from server.models.message import Message, MessageStatus, SkillInvocation, MCPServerConfig
        return Message(
            id=self.id,
            session_id=self.session_id,
            user_id=str(self.user_id),
            content=self.content,
            scene_mode=self.scene_mode,
            workspace=self.workspace,
            model=self.model,
            mode=self.mode,
            agent_id=self.agent_id,
            agent_type=self.agent_type,
            files=list(self.files or []),
            skill_invocations=[SkillInvocation(**si) for si in (self.skill_invocations or [])],
            mcp_servers=[MCPServerConfig(**ms) for ms in (self.mcp_servers or [])],
            client_message_id=self.client_message_id,
            sender_agent_id=self.sender_agent_id,
            recipient_agent_id=self.recipient_agent_id,
            msg_type=self.msg_type,
            cid=self.cid,
            status=MessageStatus(self.status),
            queue_position=self.queue_position,
            turn_count=self.turn_count,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            duration_ms=self.duration_ms,
            tool_calls_count=self.tool_calls_count,
            error_message=self.error_message,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

    @staticmethod
    def from_pydantic(m) -> "OrmMessage":
        return OrmMessage(
            id=m.id,
            session_id=m.session_id,
            user_id=_uuid.UUID(m.user_id) if isinstance(m.user_id, str) else m.user_id,
            content=m.content,
            scene_mode=m.scene_mode,
            workspace=m.workspace,
            model=m.model,
            mode=m.mode,
            agent_id=m.agent_id,
            agent_type=m.agent_type,
            files=list(m.files or []),
            skill_invocations=[si.model_dump() for si in (m.skill_invocations or [])],
            mcp_servers=[ms.model_dump() for ms in (m.mcp_servers or [])],
            client_message_id=m.client_message_id,
            sender_agent_id=m.sender_agent_id,
            recipient_agent_id=m.recipient_agent_id,
            msg_type=m.msg_type,
            cid=m.cid,
            status=m.status.value if hasattr(m.status, "value") else m.status,
            queue_position=m.queue_position,
            turn_count=m.turn_count,
            tokens_in=m.tokens_in,
            tokens_out=m.tokens_out,
            duration_ms=m.duration_ms,
            tool_calls_count=m.tool_calls_count,
            error_message=m.error_message,
            created_at=m.created_at,
            started_at=m.started_at,
            completed_at=m.completed_at,
        )


# ═══════════════════════════════════════════════════════════════
# 4. Conversation History
# ═══════════════════════════════════════════════════════════════

class OrmConversationHistory(Base):
    __tablename__ = "conversation_history"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, comment="所属会话",
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="角色：user | assistant | tool",
    )
    content: Mapped[Optional[str]] = mapped_column(
        Text, comment="消息文本内容",
    )
    reasoning_content: Mapped[Optional[str]] = mapped_column(
        Text, comment="推理内容（DeepSeek 等模型的 thinking）",
    )
    tool_calls: Mapped[Optional[list]] = mapped_column(
        JSONB, comment="工具调用列表 [{id, type, function: {name, arguments}}]",
    )
    tool_call_id: Mapped[Optional[str]] = mapped_column(
        String(100), comment="工具调用 ID（role=tool 时关联对应的 tool_call）",
    )
    message_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        comment="归属的消息 ID（该消息第一行 = user 行，为消息边界锚点；可空 = 老行）",
    )
    turn: Mapped[Optional[int]] = mapped_column(
        Integer, comment="归属的 turn 序号（可空 = 老行未标注）",
    )
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False,
        comment="消息序号（会话内单调递增，保证顺序）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )

    __table_args__ = (
        Index("idx_conv_history_session", "session_id", "sequence"),
        Index("idx_conv_history_message", "session_id", "message_id", "sequence"),
        {"comment": "对话历史表：LLM 可见的完整消息流，OpenAI 兼容格式"},
    )

    def to_dict(self) -> dict:
        """转换为 OpenAI 兼容格式的 dict。"""
        msg: dict = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.reasoning_content is not None:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        return msg


# ═══════════════════════════════════════════════════════════════
# 4b. Tool Invocations（阶段 C-1 工具执行账本）
# ═══════════════════════════════════════════════════════════════

class OrmToolInvocation(Base):
    __tablename__ = "tool_invocations"

    invocation_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="调用 ID（= 客户端工具下发 request_id）",
    )
    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, comment="所属会话",
    )
    message_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        comment="归属消息 ID（可空 = 老记录/消息未标注）",
    )
    turn: Mapped[Optional[int]] = mapped_column(
        Integer, comment="归属 turn 序号",
    )
    attempt: Mapped[Optional[int]] = mapped_column(
        Integer, comment="运行序号：首跑/regenerate 递增开新块，continue 复用当前",
    )
    tool_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="工具名",
    )
    location: Mapped[str] = mapped_column(
        String(20), nullable=False, default="server",
        comment="执行位置：client | server",
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="issued",
        comment="状态：issued | completed | skipped | superseded",
    )
    input: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict,
        comment="工具输入参数 JSON",
    )
    result: Mapped[Optional[dict]] = mapped_column(
        JSONB, comment="工具结果 JSON（completed 时写入）",
    )
    side_effect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否含副作用",
    )
    idempotency: Mapped[str] = mapped_column(
        String(20), nullable=False, default="non-idempotent",
        comment="幂等档：read-only | idempotent | non-idempotent（C-3）",
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否需用户审批",
    )
    error: Mapped[Optional[str]] = mapped_column(
        Text, comment="失败/中止原因",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="记录创建（issued）时间",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="终态落定时间",
    )

    __table_args__ = (
        Index("idx_tool_invocations_session_message", "session_id", "message_id"),
        Index("idx_tool_invocations_session_state", "session_id", "state"),
        Index("uq_tool_invocations_session_id", "session_id", "invocation_id", unique=True),
        {"comment": "工具调用账本：工具动作至多一次生效、结果不重复消费"},
    )

    def to_pydantic(self):
        from server.models.tool_invocation import ToolInvocation, InvocationState
        return ToolInvocation(
            invocation_id=self.invocation_id,
            session_id=self.session_id,
            message_id=self.message_id,
            turn=self.turn,
            attempt=self.attempt,
            tool_name=self.tool_name,
            location=self.location,
            state=InvocationState(self.state),
            input=self.input or {},
            result=self.result,
            side_effect=self.side_effect,
            idempotency=self.idempotency,
            requires_approval=self.requires_approval,
            error=self.error,
            created_at=self.created_at,
            completed_at=self.completed_at,
        )


# ═══════════════════════════════════════════════════════════════
# 5. User Skills
# ═══════════════════════════════════════════════════════════════

class OrmUserSkill(Base):
    __tablename__ = "user_skills"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    skill_id: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="Skill 标识（hub: sk1.., custom: cs1..）",
    )
    skill_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="Skill 名称",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    folder_path: Mapped[str] = mapped_column(
        String(500), nullable=False, default="",
        comment="文件夹名（位于 skills/definitions/ 下）",
    )
    version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0.0", comment="版本号",
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="通用", comment="分类",
    )
    icon: Mapped[str] = mapped_column(
        String(10), nullable=False, default="⚡", comment="图标",
    )
    author: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", comment="作者",
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="标签列表",
    )
    is_installed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否已安装",
    )
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否自定义 Skill",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="安装/创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "skill_id", name="uq_user_skills"),
        Index("idx_user_skills_user", "user_id"),
        Index("idx_user_skills_installed", "user_id", "is_installed"),
        {"comment": "用户 Skill 表：记录每个用户安装的 Skill 和自定义 Skill"},
    )

    def to_skill_definition(self):
        from server.skills.skill_registry import SkillDefinition
        return SkillDefinition(
            skill_id=self.skill_id,
            skill_name=self.skill_name,
            description=self.description,
            folder_path=self.folder_path,
            version=self.version,
            category=self.category,
            icon=self.icon,
            author=self.author,
            tags=list(self.tags or []),
        )


# ═══════════════════════════════════════════════════════════════
# 6. User MCP Servers
# ═══════════════════════════════════════════════════════════════

class OrmUserMcpServer(Base):
    __tablename__ = "user_mcp_servers"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    server_id: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="MCP 服务标识（hub: mh6.., custom: cm1..）",
    )
    server_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="MCP 服务名称",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    icon: Mapped[str] = mapped_column(
        String(10), nullable=False, default="🔌", comment="图标",
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="自定义", comment="分类",
    )
    transport: Mapped[str] = mapped_column(
        String(30), nullable=False, default="stdio",
        comment="传输方式：stdio | streamable-http | sse",
    )
    command: Mapped[Optional[str]] = mapped_column(
        String(500), comment="启动命令（stdio 传输时使用）",
    )
    args: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="命令参数列表",
    )
    url: Mapped[Optional[str]] = mapped_column(
        String(1000), comment="服务 URL（HTTP/SSE 传输时使用）",
    )
    env: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=dict, comment="环境变量",
    )
    is_installed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否已安装",
    )
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否自定义 MCP 服务",
    )
    tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list,
        comment="该 MCP 服务提供的工具列表 [{name, description, inputSchema}]",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="安装/创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "server_id", name="uq_user_mcp"),
        Index("idx_user_mcp_user", "user_id"),
        Index("idx_user_mcp_installed", "user_id", "is_installed"),
        {"comment": "用户 MCP 服务表：记录每个用户安装的 MCP 服务和自定义 MCP 服务"},
    )

    def to_dict(self) -> dict:
        return {
            "server_id": self.server_id,
            "server_name": self.server_name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args or []),
            "url": self.url,
            "env": dict(self.env or {}),
            "is_installed": self.is_installed,
            "is_custom": self.is_custom,
            "tools": list(self.tools or []),
        }


# ═══════════════════════════════════════════════════════════════
# 7. Skill Hub
# ═══════════════════════════════════════════════════════════════

class OrmSkillHub(Base):
    __tablename__ = "skill_hub"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    skill_id: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="Skill 唯一标识（sk1, sk2, ...）",
    )
    skill_name: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, comment="Skill 名称（唯一）",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    folder_path: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="文件夹名（位于 skills/definitions/ 下）",
    )
    version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0.0", comment="版本号",
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="通用", comment="分类",
    )
    icon: Mapped[str] = mapped_column(
        String(10), nullable=False, default="⚡", comment="图标",
    )
    author: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", comment="作者",
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="标签列表",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        {"comment": "Skill 目录表：全局可安装的 Skill 列表，管理员通过 API 维护"},
    )

    def to_skill_definition(self):
        from server.skills.skill_registry import SkillDefinition
        return SkillDefinition(
            skill_id=self.skill_id,
            skill_name=self.skill_name,
            description=self.description,
            folder_path=self.folder_path,
            version=self.version,
            category=self.category,
            icon=self.icon,
            author=self.author,
            tags=list(self.tags or []),
        )


# ═══════════════════════════════════════════════════════════════
# 8. MCP Hub
# ═══════════════════════════════════════════════════════════════

class OrmMcpHub(Base):
    __tablename__ = "mcp_hub"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    server_id: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="MCP 服务唯一标识（mh6, mh7, ...）",
    )
    server_name: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, comment="MCP 服务名称（唯一）",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    icon: Mapped[str] = mapped_column(
        String(10), nullable=False, default="🔌", comment="图标",
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="通用", comment="分类",
    )
    transport: Mapped[str] = mapped_column(
        String(30), nullable=False, default="stdio",
        comment="传输方式：stdio | streamable-http | sse",
    )
    command: Mapped[Optional[str]] = mapped_column(
        String(500), comment="启动命令（stdio 传输时使用）",
    )
    args: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="命令参数列表",
    )
    url: Mapped[Optional[str]] = mapped_column(
        String(1000), comment="服务 URL（HTTP/SSE 传输时使用）",
    )
    env: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=dict, comment="环境变量",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        {"comment": "MCP 目录表：全局可安装的 MCP 服务列表，管理员通过 API 维护"},
    )

    def to_dict(self) -> dict:
        return {
            "server_id": self.server_id,
            "server_name": self.server_name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args or []),
            "url": self.url,
            "env": dict(self.env or {}),
        }


# ═══════════════════════════════════════════════════════════════
# 9. Expert Hub
# ═══════════════════════════════════════════════════════════════

class OrmExpertHub(Base):
    __tablename__ = "expert_hub"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="专家唯一标识",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True,
        comment="专家唯一标识名",
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="展示名称",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    plugin_path: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="本地插件目录绝对路径",
    )
    version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0.0", comment="插件版本号",
    )
    max_turn: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="最大轮次（覆盖 .md 默认值）",
    )
    max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="最大 token（覆盖 .md 默认值）",
    )
    timeout_seconds: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="超时秒数（覆盖 .md 默认值）",
    )
    permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="工具权限列表",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        Index("idx_expert_hub_name", "name"),
        {"comment": "Expert Hub 表：全局可用的专家 Agent 列表"},
    )


# ═══════════════════════════════════════════════════════════════
# 10. Expert Team Hub
# ═══════════════════════════════════════════════════════════════

class OrmExpertTeamHub(Base):
    __tablename__ = "expert_team_hub"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="团队唯一标识",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True,
        comment="团队唯一标识名",
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="展示名称",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="功能描述",
    )
    plugin_path: Mapped[str] = mapped_column(
        String(1000), nullable=False, comment="本地插件目录绝对路径",
    )
    version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="1.0.0", comment="插件版本号",
    )
    lead_agent_id: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="领队 agent 名称",
    )
    members: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="成员列表 [{id,display_name,profession,role}]",
    )
    skills: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="skill 路径列表",
    )
    mcp: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, comment="MCP 配置列表",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        Index("idx_team_hub_name", "name"),
        {"comment": "Expert Team Hub 表：全局可用的专家团队列表"},
    )


# ═══════════════════════════════════════════════════════════════
# 9. Memories
# ═══════════════════════════════════════════════════════════════

class OrmMemory(Base):
    __tablename__ = "memories"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="记忆唯一标识",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="记忆名称（唯一标识），如 user_role",
    )
    description: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="一行描述，用于 MEMORY.md 索引",
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="user | feedback | project | reference",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="记忆正文（Markdown）",
    )
    protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
        comment="true 时 AI 不可修改或删除",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_memories_user_name"),
        Index("idx_memories_user", "user_id", "updated_at"),
        Index("idx_memories_type", "user_id", "type"),
        {"comment": "记忆表：用户长期记忆，AI 自动写入 + 用户手动管理"},
    )


# ═══════════════════════════════════════════════════════════════
# 10. Rules
# ═══════════════════════════════════════════════════════════════

class OrmRule(Base):
    __tablename__ = "rules"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="规则唯一标识",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="规则名称（唯一标识），如 always-typescript",
    )
    description: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="一行描述，用于索引",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="规则正文（Markdown）",
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="优先级，数字越大越靠前",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_rules_user_name"),
        Index("idx_rules_user", "user_id", "priority"),
        {"comment": "规则表：用户手动定义的强制性约束，AI 只读"},
    )


# ═══════════════════════════════════════════════════════════════
# 11. 审计日志
# ═══════════════════════════════════════════════════════════════

class OrmAuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="审计记录唯一标识",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="事件发生时间",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="操作者",
    )
    session_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), comment="关联会话",
    )
    message_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), comment="关联消息",
    )
    action: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="审计动作类型",
    )
    resource: Mapped[Optional[str]] = mapped_column(
        String(100), comment="操作资源标识",
    )
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, comment="事件详情",
    )
    client_ip: Mapped[Optional[str]] = mapped_column(
        String(45), comment="客户端 IP",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text, comment="客户端 User-Agent",
    )

    __table_args__ = (
        Index("idx_audit_user", "user_id", "created_at"),
        Index("idx_audit_action", "action", "created_at"),
        Index("idx_audit_session", "session_id", "created_at"),
        {"comment": "审计日志表：记录敏感操作，独立于 Grafana 体系，保证长期保留"},
    )


# ═══════════════════════════════════════════════════════════════
# 12. 流事件持久化
# ═══════════════════════════════════════════════════════════════

class OrmStreamEvent(Base):
    __tablename__ = "stream_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, comment="会话 ID",
    )
    message_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, comment="消息 ID",
    )
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="数据块序号",
    )
    chunk: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="NDJSON 数据块",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )

    __table_args__ = (
        Index("idx_stream_session_seq", "session_id", "seq"),
        {"comment": "流事件表：持久化 NDJSON 数据块，支持会话回放"},
    )


# ═══════════════════════════════════════════════════════════════
# 13. 卸载块（10.5.3 外部记忆）
# ═══════════════════════════════════════════════════════════════

class OrmOffloadedBlock(Base):
    __tablename__ = "offloaded_blocks"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True,
        comment="自增主键",
    )
    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, comment="所属会话",
    )
    block_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="源 InfoBlock 的 block_id",
    )
    turn: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="块创建轮次",
    )
    label: Mapped[str] = mapped_column(
        String(100), nullable=False, default="", comment="索引标签（title/artifact/截断正文）",
    )
    precision: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DERIVED", comment="CONFIRMED | DERIVED | OBSOLETE | PENDING",
    )
    artifact: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="归一化 artifact 名",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="卸载块原文/摘要",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间",
    )

    __table_args__ = (
        Index("idx_offloaded_session", "session_id"),
        Index("idx_offloaded_session_block", "session_id", "block_id"),
        {"comment": "卸载块表：会话上下文压缩时卸载的外部记忆，TF-IDF 召回用"},
    )
