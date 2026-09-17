"""Split config JSONB into independent columns on expert_hub and expert_team_hub

Revision ID: 006
Revises: 005
Create Date: 2026-08-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── expert_hub: 新增 max_turn, max_tokens, timeout_seconds, permissions ──
    op.add_column("expert_hub", sa.Column("max_turn", sa.Integer(), nullable=True))
    op.add_column("expert_hub", sa.Column("max_tokens", sa.Integer(), nullable=True))
    op.add_column("expert_hub", sa.Column("timeout_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "expert_hub",
        sa.Column("permissions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    # ── expert_team_hub: 新增 lead_agent_id, members, skills, mcp ──
    op.add_column("expert_team_hub", sa.Column("lead_agent_id", sa.String(200), nullable=True))
    op.add_column(
        "expert_team_hub",
        sa.Column("members", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "expert_team_hub",
        sa.Column("skills", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "expert_team_hub",
        sa.Column("mcp", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )

    # 从 config JSONB 迁移已有数据到新列
    op.execute(sa.text("""
        UPDATE expert_team_hub
        SET lead_agent_id = config->>'lead_agent_id',
            members = COALESCE(config->'members', '[]'::jsonb),
            skills = COALESCE(config->'skills', '[]'::jsonb),
            mcp = COALESCE(config->'mcp', '[]'::jsonb)
    """))

    # 删除 config 列
    op.drop_column("expert_team_hub", "config")
    op.drop_column("expert_hub", "config")


def downgrade() -> None:
    # 恢复 config 列
    op.add_column(
        "expert_hub",
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "expert_team_hub",
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    # 回写数据
    op.execute(sa.text("""
        UPDATE expert_team_hub
        SET config = jsonb_build_object(
            'lead_agent_id', lead_agent_id,
            'members', members,
            'skills', skills,
            'mcp', mcp
        )
    """))
    op.drop_column("expert_team_hub", "mcp")
    op.drop_column("expert_team_hub", "skills")
    op.drop_column("expert_team_hub", "members")
    op.drop_column("expert_team_hub", "lead_agent_id")
    op.drop_column("expert_hub", "permissions")
    op.drop_column("expert_hub", "timeout_seconds")
    op.drop_column("expert_hub", "max_tokens")
    op.drop_column("expert_hub", "max_turn")
