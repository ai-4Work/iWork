"""Add client_message_id column to messages

Revision ID: 009
Revises: 008
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "client_message_id",
            sa.String(64),
            nullable=True,
            comment="客户端幂等键；(session_id, client_message_id) 唯一，NULL = 未启用去重",
        ),
    )
    op.create_index(
        "uq_messages_session_client_msg",
        "messages",
        ["session_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_messages_session_client_msg", table_name="messages")
    op.drop_column("messages", "client_message_id")
