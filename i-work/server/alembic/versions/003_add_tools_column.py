"""Add tools column to user_mcp_servers

Revision ID: 003
Revises: 002
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_mcp_servers",
                  sa.Column("tools", postgresql.JSONB, nullable=False,
                            server_default=sa.text("'[]'"),
                            comment="该 MCP 服务提供的工具列表"))


def downgrade() -> None:
    op.drop_column("user_mcp_servers", "tools")
