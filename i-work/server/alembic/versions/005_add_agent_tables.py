"""Add expert_hub, expert_team_hub tables + agent columns

Revision ID: 005
Revises: 004_add_observability_tables
Create Date: 2026-07-31

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── expert_hub ──
    op.create_table(
        "expert_hub",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("plugin_path", sa.String(1000), nullable=False),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_expert_hub_name", "expert_hub", ["name"])

    # ── expert_team_hub ──
    op.create_table(
        "expert_team_hub",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("plugin_path", sa.String(1000), nullable=False),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0.0"),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_team_hub_name", "expert_team_hub", ["name"])

    # ── sessions: add parent_id + agents ──
    op.add_column("sessions", sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sessions_parent_id", "sessions",
        "sessions", ["parent_id"], ["id"],
        ondelete="SET NULL",
    )
    op.add_column("sessions", sa.Column(
        "agents", postgresql.JSONB, nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ))

    # ── messages: add agent_id + agent_type ──
    op.add_column("messages", sa.Column("agent_id", sa.String(200), nullable=True))
    op.add_column("messages", sa.Column("agent_type", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "agent_type")
    op.drop_column("messages", "agent_id")
    op.drop_column("sessions", "agents")
    op.drop_constraint("fk_sessions_parent_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "parent_id")
    op.drop_table("expert_team_hub")
    op.drop_table("expert_hub")
