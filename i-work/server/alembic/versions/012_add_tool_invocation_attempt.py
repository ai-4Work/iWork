"""Add attempt column to tool_invocations

阶段 D：副作用按"运行序号"分块。attempt 语义：
  首跑 / regenerate 递增开新块（max(已有)+1）；continue 复用当前块（max，不新开）。
旧行（迁移前）为 NULL，前端按独立旧块处理。

Revision ID: 012
Revises: 011
Create Date: 2026-09-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=True,
            comment="运行序号：首跑/regenerate 递增开新块，continue 复用当前",
        ),
    )


def downgrade() -> None:
    op.drop_column("tool_invocations", "attempt")
