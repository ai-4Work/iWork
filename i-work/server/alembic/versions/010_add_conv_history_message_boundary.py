"""Add message_id/turn boundary columns to conversation_history

conversation_history 此前是会话级无界流：无法把历史行归属到某条 Message，
恢复/重跑/历史回读时无法按消息截断或组装。

迁移 010 加 message_id / turn 两个可空列：
  - message_id：该历史行由哪条 Message 产出（消息第一行 = 该消息的 user 行，
    是 truncate/regenerate 的边界锚点；plan 追问等中间 user 行同属该消息）。
  - turn：该行所属 turn 序号（可空，兼容老行 message_id/turn 均为 NULL）。

Revision ID: 010
Revises: 009
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_history",
        sa.Column(
            "message_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="归属的消息 ID（该消息第一行 = user 行，为消息边界锚点）",
        ),
    )
    op.add_column(
        "conversation_history",
        sa.Column(
            "turn",
            sa.Integer(),
            nullable=True,
            comment="归属的 turn 序号（可空 = 老行未标注）",
        ),
    )
    op.create_index(
        "idx_conv_history_message",
        "conversation_history",
        ["session_id", "message_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("idx_conv_history_message", table_name="conversation_history")
    op.drop_column("conversation_history", "turn")
    op.drop_column("conversation_history", "message_id")
