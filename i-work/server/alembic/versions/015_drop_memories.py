"""Drop the legacy four-type memory table

阶段：L1 原子记忆接替旧的"四类长期记忆"（user/feedback/project/reference）。

旧记忆是"模型自觉调 load_memory / write_memory / delete_memory 手工维护"，
本次由 L1（从对话自动抽取结构化原子记忆 + 每轮自动召回注入）取代，因此
memories 表与其三个工具一并下线。L1 的新表由 016 建。

刻意不改 002：那个 revision 同时建了 memories 和 rules，而 rules 仍在用
（query_loop._build_rules_xml → context.py 注入 <rules>）。在 002 上动刀会
把 rules 一起带走。这里单独 drop，索引与唯一约束随表自动消失。

⚠️ 不可逆地丢数据：升级前请先导出 memories。

Revision ID: 015
Revises: 014
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("memories")


def downgrade() -> None:
    """按 002 的形状重建，保证 downgrade → upgrade 可往返。"""
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
            comment="所属用户",
        ),
        sa.Column("name", sa.String(length=200), nullable=False,
                  comment="记忆名称（唯一标识），如 user_role"),
        sa.Column("description", sa.String(length=500), nullable=False,
                  comment="一行描述，用于 MEMORY.md 索引"),
        sa.Column("type", sa.String(length=20), nullable=False,
                  comment="user | feedback | project | reference"),
        sa.Column("content", sa.Text(), nullable=False, comment="记忆正文（Markdown）"),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false(),
                  comment="true 时 AI 不可修改或删除"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("user_id", "name", name="uq_memories_user_name"),
        comment="记忆表：用户长期记忆，AI 自动写入 + 用户手动管理",
    )
    op.create_index("idx_memories_user", "memories", ["user_id", "updated_at"])
    op.create_index("idx_memories_type", "memories", ["user_id", "type"])
