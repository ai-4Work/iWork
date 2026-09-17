"""Add agent_path / root_session_id to sessions

阶段：多 Agent 协作（ch9 §9.11.4）。给会话补上 agent 树寻址列：
  agent_path      顶层会话 /root，子会话 /root/{member_id}
  root_session_id agent 树顶层会话 ID（刻意不加 FK：会话只归档不硬删）

回填策略：历史行一律回填成「自己就是自己的根」——每行的 root_session_id = id
天然唯一，因此随后的 (root_session_id, agent_path) 唯一索引一次建得成功，
不需要先解决历史数据里的路径冲突。

Revision ID: 013
Revises: 012
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default 让已有行一次性填成 /root（PG 走 fast default，不重写表）
    op.add_column(
        "sessions",
        sa.Column(
            "agent_path",
            sa.String(length=64),
            nullable=False,
            server_default="/root",
            comment="agent 树路径：顶层会话 /root，子会话 /root/{member_id}",
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "root_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="agent 树顶层会话 ID。刻意不加 FK：会话只归档不硬删",
        ),
    )
    op.execute("UPDATE sessions SET root_session_id = id WHERE root_session_id IS NULL")
    op.create_index(
        "uq_sessions_root_agent_path",
        "sessions",
        ["root_session_id", "agent_path"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_sessions_root_agent_path", table_name="sessions")
    op.drop_column("sessions", "root_session_id")
    op.drop_column("sessions", "agent_path")
