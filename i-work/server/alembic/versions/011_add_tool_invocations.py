"""Add tool_invocations ledger table

阶段 C-1：工具执行账本。保证一个工具动作至多生效一次、结果不被重复消费。
以 invocation_id 为键（== 客户端工具下发 request_id），记录
issued → completed/skipped/superseded 的状态机，供对账补投（C-2）与幂等档
标注（C-3）读取。

Revision ID: 011
Revises: 010
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column(
            "invocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="调用 ID（= 客户端工具下发 request_id）",
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属会话",
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="归属消息 ID（可空 = 老记录/消息未标注）",
        ),
        sa.Column(
            "turn",
            sa.Integer(),
            nullable=True,
            comment="归属 turn 序号",
        ),
        sa.Column(
            "tool_name",
            sa.String(100),
            nullable=False,
            comment="工具名",
        ),
        sa.Column(
            "location",
            sa.String(20),
            nullable=False,
            server_default="server",
            comment="执行位置：client | server",
        ),
        sa.Column(
            "state",
            sa.String(20),
            nullable=False,
            server_default="issued",
            comment="状态：issued | completed | skipped | superseded",
        ),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="工具输入参数 JSON",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="工具结果 JSON（completed 时写入）",
        ),
        sa.Column(
            "side_effect",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="是否含副作用",
        ),
        sa.Column(
            "idempotency",
            sa.String(20),
            nullable=False,
            server_default="non-idempotent",
            comment="幂等档：read-only | idempotent | non-idempotent（C-3）",
        ),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否需用户审批",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="失败/中止原因",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="记录创建（issued）时间",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="终态落定时间",
        ),
        sa.PrimaryKeyConstraint("invocation_id"),
    )
    op.create_index(
        "idx_tool_invocations_session_message",
        "tool_invocations",
        ["session_id", "message_id"],
    )
    op.create_index(
        "idx_tool_invocations_session_state",
        "tool_invocations",
        ["session_id", "state"],
    )
    op.create_index(
        "uq_tool_invocations_session_id",
        "tool_invocations",
        ["session_id", "invocation_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_tool_invocations_session_id", table_name="tool_invocations")
    op.drop_index("idx_tool_invocations_session_state", table_name="tool_invocations")
    op.drop_index("idx_tool_invocations_session_message", table_name="tool_invocations")
    op.drop_table("tool_invocations")
