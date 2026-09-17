"""Add offloaded_blocks table (10.5.3 external memory)

Revision ID: 007
Revises: 006
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "offloaded_blocks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                  nullable=False, comment="所属会话"),
        sa.Column("block_id", sa.String(100), nullable=False,
                  comment="源 InfoBlock 的 block_id"),
        sa.Column("turn", sa.Integer, nullable=False,
                  comment="块创建轮次"),
        sa.Column("label", sa.String(100), nullable=False, server_default=sa.text("''"),
                  comment="索引标签（title/artifact/截断正文）"),
        sa.Column("precision", sa.String(20), nullable=False, server_default=sa.text("'DERIVED'"),
                  comment="CONFIRMED | DERIVED | OBSOLETE | PENDING"),
        sa.Column("artifact", sa.String(200), nullable=False, server_default=sa.text("''"),
                  comment="归一化 artifact 名"),
        sa.Column("content", sa.Text, nullable=False,
                  comment="卸载块原文/摘要"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Index("idx_offloaded_session", "session_id"),
        sa.Index("idx_offloaded_session_block", "session_id", "block_id"),
        comment="卸载块表：会话上下文压缩时卸载的外部记忆，TF-IDF 召回用",
    )


def downgrade() -> None:
    op.drop_table("offloaded_blocks")
