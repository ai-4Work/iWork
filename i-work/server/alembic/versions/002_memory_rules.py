"""Add memories and rules tables

Revision ID: 002
Revises: 001
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"),
                  comment="记忆唯一标识"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, comment="所属用户"),
        sa.Column("name", sa.String(200), nullable=False,
                  comment="记忆名称（唯一标识），如 user_role"),
        sa.Column("description", sa.String(500), nullable=False,
                  comment="一行描述，用于 MEMORY.md 索引"),
        sa.Column("type", sa.String(20), nullable=False,
                  comment="user | feedback | project | reference"),
        sa.Column("content", sa.Text, nullable=False,
                  comment="记忆正文（Markdown）"),
        sa.Column("protected", sa.Boolean, nullable=False,
                  server_default=sa.text("false"),
                  comment="true 时 AI 不可修改或删除"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("user_id", "name", name="uq_memories_user_name"),
        sa.Index("idx_memories_user", "user_id", "updated_at"),
        sa.Index("idx_memories_type", "user_id", "type"),
        comment="记忆表：用户长期记忆，AI 自动写入 + 用户手动管理",
    )

    op.create_table(
        "rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"),
                  comment="规则唯一标识"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, comment="所属用户"),
        sa.Column("name", sa.String(200), nullable=False,
                  comment="规则名称（唯一标识），如 always-typescript"),
        sa.Column("description", sa.String(500), nullable=False,
                  comment="一行描述，用于索引"),
        sa.Column("content", sa.Text, nullable=False,
                  comment="规则正文（Markdown）"),
        sa.Column("priority", sa.Integer, nullable=False,
                  server_default=sa.text("0"),
                  comment="优先级，数字越大越靠前"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("user_id", "name", name="uq_rules_user_name"),
        sa.Index("idx_rules_user", "user_id", "priority"),
        comment="规则表：用户手动定义的强制性约束，AI 只读",
    )



def downgrade() -> None:
    op.drop_table("rules")
    op.drop_table("memories")
