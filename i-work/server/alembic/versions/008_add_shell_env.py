"""Add shell_env column to sessions

Revision ID: 008
Revises: 007
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "shell_env",
            sa.String(30),
            nullable=False,
            server_default=sa.text("''"),
            comment="客户端执行环境：git-bash | zsh | bash（空 = 未上报，不注入 Shell 提示词）",
        ),
    )


def downgrade() -> None:
    op.drop_column("sessions", "shell_env")
