"""Add mailbox envelope columns to messages

阶段：Session Mailbox（ch9 §9.11.7）。信封不进新表，就落在 messages 上：
  sender_agent_id / recipient_agent_id  发件人 / 收件人的 agent_path
  msg_type                              user | task | followup | result | status
  cid                                   关联 id，把 result 对回它那条 task

msg_type 的 server_default='user' 让历史行自动归为"用户消息"，于是新的
msg_type 过滤（dequeue_next / count_pending / list_pending / renumber_queue）
对老数据是 no-op——既有队列语义与测试不受影响。

Revision ID: 014
Revises: 013
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "sender_agent_id", sa.String(length=200), nullable=True,
            comment="发件人 agent_path（用户直接输入的消息为空）",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "recipient_agent_id", sa.String(length=200), nullable=True,
            comment="收件人 agent_path（邮箱按它过滤）",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "msg_type", sa.String(length=20), nullable=False,
            server_default="user",
            comment="user | task | followup | result | status；只有前三种会被出队跑回合",
        ),
    )
    op.add_column(
        "messages",
        sa.Column(
            "cid", sa.String(length=64), nullable=True,
            comment="关联 id：把 result 对回它那条 task",
        ),
    )
    op.create_index(
        "idx_messages_mailbox",
        "messages",
        ["session_id", "recipient_agent_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_messages_mailbox", table_name="messages")
    op.drop_column("messages", "cid")
    op.drop_column("messages", "msg_type")
    op.drop_column("messages", "recipient_agent_id")
    op.drop_column("messages", "sender_agent_id")
