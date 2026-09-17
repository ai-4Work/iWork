"""Initial schema — all 8 tables

Revision ID: 001
Revises: None
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users ──
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"),
                  comment="用户唯一标识"),
        sa.Column("username", sa.String(100), nullable=False, unique=True,
                  comment="登录用户名"),
        sa.Column("display_name", sa.String(200), comment="显示名称"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="用户表",
    )

    # ── 2. sessions ──
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  comment="会话唯一标识（客户端生成）"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属用户"),
        sa.Column("title", sa.String(500), nullable=False,
                  server_default="新建任务", comment="会话标题（首条消息自动设置）"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active", comment="状态：active | archived"),
        sa.Column("mode", sa.String(20), nullable=False,
                  server_default="build", comment="模式：ask | plan | build"),
        sa.Column("scene_mode", sa.String(20), nullable=False,
                  server_default="office", comment="场景：office | code"),
        sa.Column("model", sa.String(100), nullable=False,
                  server_default="", comment="使用的 LLM 模型"),
        sa.Column("workspace", sa.Text, nullable=False,
                  server_default="", comment="工作目录路径"),
        sa.Column("client_tools", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="客户端注册的内置工具列表"),
        sa.Column("client_mcp_tools", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="客户端上报的 MCP 工具清单"),
        sa.Column("current_message_id", postgresql.UUID(as_uuid=True),
                  comment="当前正在处理的消息 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="会话表：每个用户的一次对话会话",
    )
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_index("idx_sessions_status", "sessions", ["status"])
    op.create_index("idx_sessions_user_status", "sessions", ["user_id", "status"])
    op.create_index("idx_sessions_updated_at", "sessions", ["updated_at"])
    op.create_foreign_key(
        "fk_sessions_user_id", "sessions", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )

    # ── 3. messages ──
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  comment="消息唯一标识"),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属会话"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="发送者"),
        sa.Column("content", sa.Text, nullable=False, comment="用户输入文本"),
        sa.Column("scene_mode", sa.String(20), nullable=False,
                  comment="场景模式（记录发送时的快照）"),
        sa.Column("workspace", sa.Text, nullable=False,
                  server_default="", comment="工作目录"),
        sa.Column("model", sa.String(100), nullable=False, comment="指定模型"),
        sa.Column("mode", sa.String(20), nullable=False, comment="对话模式"),
        sa.Column("files", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="@ 引用的文件路径列表"),
        sa.Column("skill_invocations", postgresql.JSONB, nullable=False,
                  server_default="[]",
                  comment="/ 调用的 skill 列表 [{skill_id, skill_name, skill_md}]"),
        sa.Column("mcp_servers", postgresql.JSONB, nullable=False,
                  server_default="[]",
                  comment="本次消息附加的 MCP 服务 [{server_id, server_name, enabled_tools}]"),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="pending",
                  comment="处理状态：pending | processing | completed | error | cancelled"),
        sa.Column("queue_position", sa.Integer,
                  comment="队列位置（1-based），处理中为 NULL"),
        sa.Column("turn_count", sa.Integer, nullable=False,
                  server_default="0", comment="LLM 已执行轮数"),
        sa.Column("tokens_in", sa.Integer, nullable=False,
                  server_default="0", comment="累计输入 token 数"),
        sa.Column("tokens_out", sa.Integer, nullable=False,
                  server_default="0", comment="累计输出 token 数"),
        sa.Column("duration_ms", sa.Integer, nullable=False,
                  server_default="0", comment="总执行耗时（毫秒）"),
        sa.Column("tool_calls_count", sa.Integer, nullable=False,
                  server_default="0", comment="工具调用总次数"),
        sa.Column("error_message", sa.Text, comment="错误信息"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="入队时间"),
        sa.Column("started_at", sa.DateTime(timezone=True),
                  comment="引擎开始处理时间"),
        sa.Column("completed_at", sa.DateTime(timezone=True),
                  comment="处理完成时间"),
        comment="消息表：用户发送的请求记录，含处理状态和统计元数据",
    )
    op.create_index("idx_messages_session_id", "messages", ["session_id"])
    op.create_index("idx_messages_session_status", "messages", ["session_id", "status"])
    op.create_index(
        "idx_messages_session_queue", "messages", ["session_id", "queue_position"],
        postgresql_where=sa.text("queue_position IS NOT NULL"),
    )
    op.create_index("idx_messages_status", "messages", ["status"])
    op.create_foreign_key(
        "fk_messages_session_id", "messages", "sessions",
        ["session_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_messages_user_id", "messages", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )

    # ── 4. conversation_history ──
    op.create_table(
        "conversation_history",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属会话"),
        sa.Column("role", sa.String(20), nullable=False,
                  comment="角色：user | assistant | tool"),
        sa.Column("content", sa.Text, comment="消息文本内容"),
        sa.Column("reasoning_content", sa.Text,
                  comment="推理内容（DeepSeek 等模型的 thinking）"),
        sa.Column("tool_calls", postgresql.JSONB,
                  comment="工具调用列表 [{id, type, function: {name, arguments}}]"),
        sa.Column("tool_call_id", sa.String(100),
                  comment="工具调用 ID（role=tool 时关联对应的 tool_call）"),
        sa.Column("sequence", sa.Integer, nullable=False,
                  comment="消息序号（会话内单调递增，保证顺序）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        comment="对话历史表：LLM 可见的完整消息流，OpenAI 兼容格式",
    )
    op.create_index("idx_conv_history_session", "conversation_history",
                    ["session_id", "sequence"])
    op.create_foreign_key(
        "fk_conv_history_session_id", "conversation_history", "sessions",
        ["session_id"], ["id"], ondelete="CASCADE",
    )

    # ── 5. user_skills ──
    op.create_table(
        "user_skills",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属用户"),
        sa.Column("skill_id", sa.String(50), nullable=False,
                  comment="Skill 标识（hub: sk1.., custom: cs1..）"),
        sa.Column("skill_name", sa.String(200), nullable=False,
                  comment="Skill 名称"),
        sa.Column("description", sa.Text, nullable=False,
                  server_default="", comment="功能描述"),
        sa.Column("folder_path", sa.String(500), nullable=False,
                  server_default="",
                  comment="文件夹名（位于 skills/definitions/ 下）"),
        sa.Column("version", sa.String(20), nullable=False,
                  server_default="1.0.0", comment="版本号"),
        sa.Column("category", sa.String(50), nullable=False,
                  server_default="通用", comment="分类"),
        sa.Column("icon", sa.String(10), nullable=False,
                  server_default="⚡", comment="图标"),
        sa.Column("author", sa.String(100), nullable=False,
                  server_default="", comment="作者"),
        sa.Column("tags", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="标签列表"),
        sa.Column("is_installed", sa.Boolean, nullable=False,
                  server_default=sa.text("TRUE"), comment="是否已安装"),
        sa.Column("is_custom", sa.Boolean, nullable=False,
                  server_default=sa.text("FALSE"), comment="是否自定义 Skill"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="安装/创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("user_id", "skill_id", name="uq_user_skills"),
        comment="用户 Skill 表：记录每个用户安装的 Skill 和自定义 Skill",
    )
    op.create_index("idx_user_skills_user", "user_skills", ["user_id"])
    op.create_index("idx_user_skills_installed", "user_skills",
                    ["user_id", "is_installed"])
    op.create_foreign_key(
        "fk_user_skills_user_id", "user_skills", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )

    # ── 6. user_mcp_servers ──
    op.create_table(
        "user_mcp_servers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属用户"),
        sa.Column("server_id", sa.String(50), nullable=False,
                  comment="MCP 服务标识（hub: mh6.., custom: cm1..）"),
        sa.Column("server_name", sa.String(200), nullable=False,
                  comment="MCP 服务名称"),
        sa.Column("description", sa.Text, nullable=False,
                  server_default="", comment="功能描述"),
        sa.Column("icon", sa.String(10), nullable=False,
                  server_default="🔌", comment="图标"),
        sa.Column("category", sa.String(50), nullable=False,
                  server_default="自定义", comment="分类"),
        sa.Column("transport", sa.String(30), nullable=False,
                  server_default="stdio",
                  comment="传输方式：stdio | streamable-http | sse"),
        sa.Column("command", sa.String(500),
                  comment="启动命令（stdio 传输时使用）"),
        sa.Column("args", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="命令参数列表"),
        sa.Column("url", sa.String(1000),
                  comment="服务 URL（HTTP/SSE 传输时使用）"),
        sa.Column("env", postgresql.JSONB, nullable=False,
                  server_default="{}", comment="环境变量"),
        sa.Column("is_installed", sa.Boolean, nullable=False,
                  server_default=sa.text("TRUE"), comment="是否已安装"),
        sa.Column("is_custom", sa.Boolean, nullable=False,
                  server_default=sa.text("FALSE"),
                  comment="是否自定义 MCP 服务"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="安装/创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("user_id", "server_id", name="uq_user_mcp"),
        comment="用户 MCP 服务表：记录每个用户安装的 MCP 服务和自定义 MCP 服务",
    )
    op.create_index("idx_user_mcp_user", "user_mcp_servers", ["user_id"])
    op.create_index("idx_user_mcp_installed", "user_mcp_servers",
                    ["user_id", "is_installed"])
    op.create_foreign_key(
        "fk_user_mcp_user_id", "user_mcp_servers", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )

    # ── 7. skill_hub ──
    op.create_table(
        "skill_hub",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("skill_id", sa.String(50), nullable=False, unique=True,
                  comment="Skill 唯一标识（sk1, sk2, ...）"),
        sa.Column("skill_name", sa.String(200), nullable=False, unique=True,
                  comment="Skill 名称（唯一）"),
        sa.Column("description", sa.Text, nullable=False,
                  server_default="", comment="功能描述"),
        sa.Column("folder_path", sa.String(500), nullable=False,
                  comment="文件夹名（位于 skills/definitions/ 下）"),
        sa.Column("version", sa.String(20), nullable=False,
                  server_default="1.0.0", comment="版本号"),
        sa.Column("category", sa.String(50), nullable=False,
                  server_default="通用", comment="分类"),
        sa.Column("icon", sa.String(10), nullable=False,
                  server_default="⚡", comment="图标"),
        sa.Column("author", sa.String(100), nullable=False,
                  server_default="", comment="作者"),
        sa.Column("tags", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="标签列表"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="Skill 目录表：全局可安装的 Skill 列表，管理员通过 API 维护",
    )

    # ── 8. mcp_hub ──
    op.create_table(
        "mcp_hub",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("server_id", sa.String(50), nullable=False, unique=True,
                  comment="MCP 服务唯一标识（mh6, mh7, ...）"),
        sa.Column("server_name", sa.String(200), nullable=False, unique=True,
                  comment="MCP 服务名称（唯一）"),
        sa.Column("description", sa.Text, nullable=False,
                  server_default="", comment="功能描述"),
        sa.Column("icon", sa.String(10), nullable=False,
                  server_default="🔌", comment="图标"),
        sa.Column("category", sa.String(50), nullable=False,
                  server_default="通用", comment="分类"),
        sa.Column("transport", sa.String(30), nullable=False,
                  server_default="stdio",
                  comment="传输方式：stdio | streamable-http | sse"),
        sa.Column("command", sa.String(500),
                  comment="启动命令（stdio 传输时使用）"),
        sa.Column("args", postgresql.JSONB, nullable=False,
                  server_default="[]", comment="命令参数列表"),
        sa.Column("url", sa.String(1000),
                  comment="服务 URL（HTTP/SSE 传输时使用）"),
        sa.Column("env", postgresql.JSONB, nullable=False,
                  server_default="{}", comment="环境变量"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="MCP 目录表：全局可安装的 MCP 服务列表，管理员通过 API 维护",
    )


def downgrade() -> None:
    op.drop_table("mcp_hub")
    op.drop_table("skill_hub")
    op.drop_table("user_mcp_servers")
    op.drop_table("user_skills")
    op.drop_table("conversation_history")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("users")
