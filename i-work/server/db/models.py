from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, BigInteger, SmallInteger, Boolean, DateTime, ForeignKey,
    Float, UniqueConstraint, Index, func, text, true as sa_true, false as sa_false,
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
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(200), comment="bcrypt 哈希；NULL = 不可密码登录",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active",
        comment="active | disabled；仅管理员态，到期自解的自动锁定走 locked_until",
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
        comment="连续登录失败次数；登录成功或锁定期满即清零",
    )
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), comment="自动锁定截止时间；NULL = 未锁定",
    )
    dept_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("sys_dept.id", ondelete="RESTRICT"),
        comment="所属部门；种子把 NULL 回填成默认部门（doc 19-2.2）",
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
# 1b. Refresh Tokens（doc 18-12.3）
# ═══════════════════════════════════════════════════════════════

class OrmRefreshToken(Base):
    """refresh token 表：不透明随机串，存 sha256 哈希。

    只用 sha256 不用 bcrypt：32 字节随机串熵已足够，慢哈希只会白白拖慢每次刷新。
    刻意不设 `revoked` 布尔 —— `revoked_at IS NULL` 就是"未吊销"，
    两者并存会出现互相矛盾的状态。
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4,
        comment="token ID",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="所属用户",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True,
        comment="sha256 哈希后的 token（明文绝不落库）",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间",
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        comment="吊销时间；NULL = 未吊销。宽限期据它判断（doc 8.4）",
    )
    rotated_to: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), comment="轮换后的新 token id，只作轮换链审计",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="签发时间",
    )

    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
        {"comment": "Refresh Token 表：可吊销的长期凭证，一次一换"},
    )


# ═══════════════════════════════════════════════════════════════
# 1c. 登录日志（doc 18-12.4）
# ═══════════════════════════════════════════════════════════════

class OrmLoginLog(Base):
    """登录审计日志。

    **本表只做审计，锁定判断不依赖它** —— 锁定状态由 users.failed_login_count /
    locked_until 承载。写入是 best-effort，失败不能阻断登录。
    """

    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键",
    )
    user_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, comment="用户；密码错或账号不存在时为 NULL",
    )
    attempt_username: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="",
        comment="本次尝试的账号（用于排查爆破）",
    )
    ip: Mapped[Optional[str]] = mapped_column(
        String(45), comment="来源 IP",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text, comment="客户端信息",
    )
    result: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="success | fail",
    )
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="", comment="失败原因（错误码）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="时间",
    )

    __table_args__ = (
        Index("idx_login_logs_created", "created_at"),
        Index("idx_login_logs_user", "user_id", "created_at"),
        {"comment": "登录日志表：只做审计，锁定判断不依赖它"},
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
# 9. Rules
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
# 10. L1 原子记忆
# ═══════════════════════════════════════════════════════════════

class OrmL1Memory(Base):
    """L1 原子记忆：从对话自动抽取的结构化事实碎片。

    行永不硬删；被 update/merge 取代的旧行把 retrievable 置 false（软删），
    既保留事实源与血缘，又不进检索。
    """

    __tablename__ = "l1_memories"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="m_<epoch_ms>_<hex8>，跨库唯一",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="作用域：所属用户",
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
        comment="作用域：agent 标识（顶层为空串）",
    )
    session_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="抽取来源会话（跨会话累积，仅作溯源）",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="自包含的记忆陈述",
    )
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="persona | episodic | instruction",
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
        comment="重要度打分；各类型有各自的丢弃阈值",
    )
    scene_name: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="",
        comment="情境名：我（AI）在和xxx做xxx",
    )
    source_message_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="血缘：产出该记忆的 L0 消息 ID",
    )
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="类型专属元数据；episodic 带活动起止时间",
    )
    timestamps: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="时间轨迹，merge 时并集去重排序",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="版本号；update/merge 时为目标最大版本 + 1",
    )
    retrievable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_true(),
        comment="false = 已被 update/merge 取代（软删），不进检索",
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
        Index("idx_l1_memories_scope", "user_id", "agent_id", "retrievable"),
        Index("idx_l1_memories_session", "session_id"),
        Index("idx_l1_memories_updated", "updated_at"),
        {"comment": "L1 原子记忆表：自动抽取的结构化事实碎片"},
    )


class OrmL1Checkpoint(Base):
    """L1 抽取游标：每条会话一条，落库以便 sweep 重启后续抽。"""

    __tablename__ = "l1_checkpoints"

    session_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        primary_key=True, comment="所属会话",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
        comment="冗余的作用域字段，sweep 单查即可分组",
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
        comment="作用域：agent 标识",
    )
    last_cursor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
        comment="已处理到的 conversation_history.sequence",
    )
    last_scene_name: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="",
        comment="上一个情境名，供下次抽取判断是否切换",
    )
    last_extracted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="上次成功抽取时间，空闲兜底的判据",
    )

    __table_args__ = (
        Index("idx_l1_checkpoints_scope", "user_id", "agent_id"),
        {"comment": "L1 抽取游标：每条会话一条，落库以便重启后续抽"},
    )


# ═══════════════════════════════════════════════════════════════
# 11. L2 场景记忆
# ═══════════════════════════════════════════════════════════════

class OrmL2Scene(Base):
    """L2 场景记忆：一批 L1 原子记忆整合出的跨会话叙事。

    设计文档把场景当磁盘上的 `.md` 文件（LLM 用 read/write/edit 工具操作），本仓库落库 ——
    于是文档里服务文件系统的机制全部消失（备份/还原 → 事务，`[DELETED]` 标记 → 动作的
    sources 列表，重建索引 → 表本身就是索引，记忆库镜像 → 本来就在库里），
    `-----META-START-----` 文件头里的字段变成列。

    与 L1 同款软删：被 merge 取代的场景把 retrievable 置 false，行与血缘保留，
    唯一的硬删路径是用户在客户端手工删除。
    """

    __tablename__ = "l2_scenes"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="s_<epoch_ms>_<hex8>，跨库唯一",
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, comment="作用域：所属用户",
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
        comment="作用域：agent 标识（顶层为空串）",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="场景名（原文件名），作用域内唯一，导航里对外的键",
    )
    summary: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default="",
        comment="30-40 字摘要，场景导航用",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="场景叙事正文（markdown，不含 META 头）",
    )
    heat: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="热度：新建 1 / 更新 旧+1 / 合并 Σ相关+1（doc L2-2.5）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="版本号；update/merge 时为目标最大版本 + 1",
    )
    source_memory_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
        comment="血缘：产出该场景的 L1 记忆 ID",
    )
    retrievable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sa_true(),
        comment="false = 已被 merge 取代（软删），不进导航",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="创建时间（update/merge 时沿用目标场景最早的创建时间）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        Index("idx_l2_scenes_scope", "user_id", "agent_id", "retrievable"),
        Index("idx_l2_scenes_heat", "user_id", "agent_id", "heat"),
        # 名字是 LLM 引用场景的键，必须唯一 —— 但只约束可检索的行：merge 掉旧场景后
        # 同名重建不能撞索引（文档"更新现有文件时沿用清单里给的文件名"）。
        Index(
            "uq_l2_scenes_scope_name", "user_id", "agent_id", "name",
            unique=True, postgresql_where=text("retrievable"),
        ),
        {"comment": "L2 场景记忆表：L1 原子记忆整合出的跨会话叙事"},
    )


class OrmL2Checkpoint(Base):
    """L2 整合游标：每个 (user_id, agent_id) 作用域一条。

    维度与 L1 不同 —— L1 是 per 会话，L2 是 per 作用域（doc L2-1.1：刻意忽略用户、会话、
    任务维度，跨会话累积）。落库而不是放内存，sweep 重启后不丢断点。
    """

    __tablename__ = "l2_checkpoints"

    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, comment="作用域：所属用户",
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, server_default="",
        comment="作用域：agent 标识（顶层为空串）",
    )
    last_memory_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="游标：已处理到的 l1_memories.updated_at",
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="上次成功整合时间；最小间隔闸门与保底轮询的判据",
    )
    processing_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
        comment="单调处理计数（doc L2-2.8；L2 内部只自增，供观测）",
    )
    persona_update_request: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="",
        comment="LLM 请求刷新 L3 画像的原因（doc L2-2.6 第 4 步）；由 L3 生成侧消费后清空",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后更新时间",
    )

    __table_args__ = (
        {"comment": "L2 整合游标：每个作用域一条，落库以便重启后续整合"},
    )


# ═══════════════════════════════════════════════════════════════
# 12. L3 画像记忆
# ═══════════════════════════════════════════════════════════════

class OrmL3Persona(Base):
    """L3 画像记忆：一行 = 一个作用域的画像（doc L3-2.6）。

    只有这一张表，**没有 l3_checkpoints** —— 画像行本身既是产物也是游标：
    `updated_at` 就是"上次画像生成时间"（L3-2.2 据此筛变化场景），行的存在与否就是
    "有没有画像"（L3-2.3 首次/增量、P2 冷启动据此判断），`memory_count_at_generation`
    是 P4 阈值算增量的快照。

    内容只有正文：场景导航由 L2 侧的表字段渲染，两者从不混存，因此没有"剥导航"这道工序。
    """

    __tablename__ = "l3_personas"

    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True, comment="作用域：所属用户",
    )
    agent_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, server_default="",
        comment="作用域：agent 标识（顶层为空串）",
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="画像正文（后处理之后的最终内容）",
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1",
        comment="版本号：每次重写 +1，首次插入为 1",
    )
    memory_count_at_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
        comment="本次生成时的 L1 记忆总数快照；P4 阈值据『当前总数 − 它』算增量",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="首次生成时间（增量重写时沿用）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        comment="最后生成时间；L3-2.2 据此筛变化场景",
    )

    __table_args__ = (
        {"comment": "L3 画像记忆表：L2 场景叙事综合出的身份文档"},
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


# ═══════════════════════════════════════════════════════════════
# 13b. 组织架构（doc 19-2.2）
# ═══════════════════════════════════════════════════════════════

class OrmDept(Base):
    """部门树：用户通过 `users.dept_id` 归属到一个部门（doc 19-2.2）。

    刻意不存 `ancestors` 路径列 —— 当前没有任何「按子树查成员」的需求，
    要用时再加。`parent_id = 0` 表示顶级。
    """

    __tablename__ = "sys_dept"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="部门ID",
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0", comment="父级ID（0=顶级）",
    )
    dept_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="部门名称",
    )
    dept_key: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True,
        comment="业务键；只有代码要按它认行的字典行才填（默认部门=default），用户建的部门填 NULL",
    )
    order_num: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="排序号",
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1", comment="1=正常 0=禁用",
    )

    __table_args__ = (
        Index("idx_sys_dept_parent", "parent_id"),
        {"comment": "部门表：组织架构树；种子保证「默认部门」始终存在"},
    )


# ═══════════════════════════════════════════════════════════════
# 14. RBAC 权限（doc 19）
# ═══════════════════════════════════════════════════════════════

class OrmRole(Base):
    """角色表：一组权限点，外加一条数据范围（doc 19-2.2）。

    角色是「身份/岗位」，用户通过 sys_user_role 挂角色，权限是角色的并集。
    """

    __tablename__ = "sys_role"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="角色ID",
    )
    role_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="角色名称（如：管理员）",
    )
    role_key: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, comment="角色标识（如：admin）",
    )
    data_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="SELF",
        comment="数据范围 ALL | DEPT | SELF（DEPT = 本部门及下级，doc 19-5.3）",
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1",
        comment="1=正常 0=禁用；禁用后其授权不参与计算（doc 19-5.1）",
    )

    __table_args__ = (
        {"comment": "角色表：权限集合 + 数据范围；data_scope 默认 SELF 是陷阱（doc 19-2.2）"},
    )


class OrmPermission(Base):
    """目录 / 菜单 / 按钮权限树（doc 19-2.2）。

    **只存「有哪些权限点」**，权限点与 API 的绑定单独放 sys_permission_api。
    `perms` 为 NULL + UNIQUE 而非 DEFAULT ''：目录行本来就没有权限标识，
    都填空串会在 UNIQUE 下互相冲突、也认不出谁是谁；NULL 之间不冲突。
    """

    __tablename__ = "sys_permission"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="权限ID",
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0", comment="父级ID（0=顶级）",
    )
    permission_name: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="菜单/按钮名称",
    )
    permission_type: Mapped[str] = mapped_column(
        String(1), nullable=False, comment="M=目录 C=菜单 F=按钮",
    )
    path: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="",
        comment="前端路由地址（区别于 sys_permission_api.path 的后端接口路径）",
    )
    component: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="", comment="前端组件路径",
    )
    perms: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True,
        comment="权限标识（前后端共用）；目录行填 NULL",
    )
    icon: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="", comment="图标",
    )
    order_num: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", comment="排序号",
    )
    visible: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1", comment="1=显示 0=隐藏",
    )
    status: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1", comment="1=正常 0=禁用",
    )

    __table_args__ = (
        Index("idx_sys_permission_parent", "parent_id"),
        {"comment": "权限字典表：目录/菜单/按钮；由 catalog.py 启动对账生成，不手写 SQL"},
    )


class OrmUserRole(Base):
    """用户-角色关联表（doc 19-2.2）。多对多，用户权限是其所有 status=1 角色的并集。"""

    __tablename__ = "sys_user_role"

    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True, comment="用户ID",
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sys_role.id", ondelete="CASCADE"),
        primary_key=True, comment="角色ID",
    )

    __table_args__ = (
        Index("idx_sys_user_role_role", "role_id"),
        {"comment": "用户-角色关联表"},
    )


class OrmRolePermission(Base):
    """角色-权限关联表（doc 19-2.2）。admin 角色不走此表——它在代码里短路成全集。"""

    __tablename__ = "sys_role_permission"

    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sys_role.id", ondelete="CASCADE"),
        primary_key=True, comment="角色ID",
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sys_permission.id", ondelete="CASCADE"),
        primary_key=True, comment="权限ID",
    )

    __table_args__ = (
        {"comment": "角色-权限关联表；由角色权限配置页维护"},
    )


class OrmPermissionApi(Base):
    """权限点-API 映射表（doc 19-2.6）。

    库里是 `Depends(require_permission(...))` 的镜像，两处必须一致，
    否则会无声漂移；启动时 verify_route_refs() 负责比对。
    """

    __tablename__ = "sys_permission_api"

    permission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sys_permission.id", ondelete="CASCADE"),
        primary_key=True, comment="权限ID",
    )
    method: Mapped[str] = mapped_column(
        String(10), primary_key=True, comment="HTTP 方法",
    )
    path: Mapped[str] = mapped_column(
        String(200), primary_key=True, comment="接口路径（如 /api/system/user/{id}）",
    )

    __table_args__ = (
        {"comment": "权限点-API 映射表：三列共同主键"},
    )


# ═══════════════════════════════════════════════════════════════
# 15. LLM 模型配置
# ═══════════════════════════════════════════════════════════════

class OrmLlmModel(Base):
    """LLM 模型配置表：管理员在「系统管理 → 模型配置」维护的可选模型清单。

    一条记录 = 一个模型，自带厂商、协议、端点与能力参数 —— 引擎按这些字段决定
    装配哪个客户端、请求体里塞什么、上下文窗口按多大算，不再依赖全局 settings。

    `deployment_type` 区分公网模型与内网自建模型：前者 key 必填、有单价与配额，
    后者常无 key、且受单机显存限制需要并发闸门。两者共用同一张表，差异只落在
    管理页的字段显隐与校验上。

    `api_key_enc` 是 Fernet 密文，**刻意不出现在 `to_dict()` 里**；对外只暴露
    `has_api_key` 与 `api_key_hint`（形如 `sk-ab…a1b2`）。
    """

    __tablename__ = "llm_model"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键",
    )
    model_key: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True,
        comment="模型唯一标识；聊天下拉与消息的 model 字段存的就是它",
    )
    display_name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="显示名（下拉里给用户看的）",
    )
    deployment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="public", server_default="public",
        comment="部署类型：public 公网 | intranet 内网自建",
    )
    protocol: Mapped[str] = mapped_column(
        String(30), nullable=False, default="openai_compatible",
        server_default="openai_compatible",
        comment="协议：openai_compatible | anthropic；决定装配哪个客户端类",
    )
    vendor: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", server_default="",
        comment="厂商，仅用于分组与图标，不参与逻辑",
    )
    model_api_name: Mapped[str] = mapped_column(
        String(200), nullable=False,
        comment="请求体里真正传的模型名（如 vLLM 的 served-model-name）",
    )
    base_url: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default="",
        comment="接口根地址；客户端自行拼 /v1/chat/completions",
    )
    api_key_enc: Mapped[Optional[str]] = mapped_column(
        Text, comment="API Key 的 Fernet 密文；主密钥在本地 .env（IWORK_MODEL_API_KEY_ENCRYPTION_KEY）",
    )
    api_key_hint: Mapped[str] = mapped_column(
        String(50), nullable=False, default="", server_default="",
        comment="掩码预览（如 sk-ab…a1b2），供管理页显示；不含明文信息",
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120",
        comment="单次请求超时（秒）；内网自建有冷启动与排队，需要调大",
    )
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3",
        comment="网络异常 / 429 / 502 / 503 的自动重试次数",
    )
    extra_body: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
        comment="透传进请求体的额外字段（如 vLLM 的 chat_template_kwargs）",
    )
    context_window: Mapped[int] = mapped_column(
        Integer, nullable=False, default=65536, server_default="65536",
        comment="上下文窗口（模型能力声明）；未配绝对压缩阈值时按它折算触发线",
    )
    compress_threshold_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        comment="上下文压缩触发线（绝对 token 数）；留空 = 按 context_window 折（build 80% / ask 55%）",
    )
    max_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20000, server_default="20000",
        comment="单次输出上限；同时是截断续写的推断阈值",
    )
    supports_tools: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true(),
        comment="是否支持 function calling；false 时请求体不带 tools",
    )
    supports_thinking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_false(),
        comment="是否支持思考；false 时请求体不带 thinking 字段（部分端点会 400）",
    )
    thinking_budget_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4096, server_default="4096",
        comment="思考预算 token 数",
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
        comment="并发上限；0=不限。内网单机 GPU 必须设，否则并发流式会打爆显存",
    )
    price_input_per_1m: Mapped[Optional[float]] = mapped_column(
        Float, comment="每 1M 输入 token 单价；内网模型留空表示不计费",
    )
    price_output_per_1m: Mapped[Optional[float]] = mapped_column(
        Float, comment="每 1M 输出 token 单价；内网模型留空表示不计费",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa_true(),
        comment="是否启用；停用后不出现在下拉里",
    )
    remark: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default="", comment="备注",
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
        {"comment": "LLM 模型配置表：管理员维护的可选模型清单，引擎按行解析客户端与能力参数"},
    )

    def to_dict(self) -> dict:
        """管理面视图。**刻意不含 `api_key_enc`** —— 密钥密文一律不出接口。"""
        return {
            "model_key": self.model_key,
            "display_name": self.display_name,
            "deployment_type": self.deployment_type,
            "protocol": self.protocol,
            "vendor": self.vendor,
            "model_api_name": self.model_api_name,
            "base_url": self.base_url,
            "has_api_key": bool(self.api_key_enc),
            "api_key_hint": self.api_key_hint,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "extra_body": dict(self.extra_body or {}),
            "context_window": self.context_window,
            "compress_threshold_tokens": self.compress_threshold_tokens,
            "max_output_tokens": self.max_output_tokens,
            "supports_tools": self.supports_tools,
            "supports_thinking": self.supports_thinking,
            "thinking_budget_tokens": self.thinking_budget_tokens,
            "max_concurrency": self.max_concurrency,
            "price_input_per_1m": self.price_input_per_1m,
            "price_output_per_1m": self.price_output_per_1m,
            "enabled": self.enabled,
            "remark": self.remark,
        }

    def to_engine_dict(self) -> dict:
        """引擎内部视图：`to_dict()` + `api_key_enc`（Fernet 密文）。

        **只给 `ModelResolver` 用。** 单独开这个方法是刻意的：密钥密文的读取路径只有
        一条、且名字里带着 engine，接口层想误用 `to_dict()` 也拿不到密文，
        想误把本方法的返回值直接 return 出去也会因为名字显眼而被 review 拦住。
        """
        data = self.to_dict()
        data["api_key_enc"] = self.api_key_enc
        return data
