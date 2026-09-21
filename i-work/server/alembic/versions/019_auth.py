"""登录认证：users 加凭证与锁定列 + refresh_tokens / login_logs

阶段：登录认证（docs/chapters/18-登录认证模块.md）。

三处改动：

1. users 加 4 列。`password_hash` 可空 —— NULL 表示"不可密码登录"，
   存量 default-user 就是此态，给它设密码之前该账号登不进来。
   `status` / `failed_login_count` 必须带 server_default，否则已有行违反 NOT NULL。
2. refresh_tokens：不透明随机串存 sha256 哈希，一次一换。
   刻意没有 `revoked` 布尔列 —— `revoked_at IS NULL` 即"未吊销"，
   两者并存会互相矛盾；宽限期也只认 revoked_at（doc 8.4）。
3. login_logs：只做审计。锁定状态落在 users 上，本表不参与判断，
   所以 user_id 可空（密码错或账号不存在时无对应用户），写入是 best-effort。

Revision ID: 019
Revises: 018
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users 加列 ──
    op.add_column("users", sa.Column(
        "password_hash", sa.String(length=200), nullable=True,
        comment="bcrypt 哈希；NULL = 不可密码登录（如 default-user 初始态）",
    ))
    op.add_column("users", sa.Column(
        "status", sa.String(length=20), nullable=False, server_default="active",
        comment="active | disabled；仅管理员态，到期自解的自动锁定走 locked_until",
    ))
    op.add_column("users", sa.Column(
        "failed_login_count", sa.Integer(), nullable=False, server_default="0",
        comment="连续登录失败次数；登录成功或锁定期满即清零",
    ))
    op.add_column("users", sa.Column(
        "locked_until", sa.DateTime(timezone=True), nullable=True,
        comment="自动锁定截止时间；NULL = 未锁定",
    ))

    # ── 2. refresh_tokens ──
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  comment="token ID"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="所属用户"),
        sa.Column("token_hash", sa.String(length=64), nullable=False,
                  comment="sha256 哈希后的 token（明文绝不落库）"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False,
                  comment="过期时间"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True,
                  comment="吊销时间；NULL = 未吊销。宽限期据它判断（doc 8.4）"),
        sa.Column("rotated_to", postgresql.UUID(as_uuid=True), nullable=True,
                  comment="轮换后的新 token id，只作轮换链审计"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="签发时间"),
        comment="Refresh Token 表：可吊销的长期凭证，一次一换",
    )
    op.create_index("uq_refresh_tokens_hash", "refresh_tokens", ["token_hash"],
                    unique=True)
    op.create_index("idx_refresh_tokens_user", "refresh_tokens", ["user_id"])
    op.create_foreign_key(
        "fk_refresh_tokens_user_id", "refresh_tokens", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )

    # ── 3. login_logs ──
    op.create_table(
        "login_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True,
                  comment="用户；密码错或账号不存在时为 NULL"),
        sa.Column("attempt_username", sa.String(length=100), nullable=False,
                  server_default="", comment="本次尝试的账号（用于排查爆破）"),
        sa.Column("ip", sa.String(length=45), nullable=True, comment="来源 IP"),
        sa.Column("user_agent", sa.Text(), nullable=True, comment="客户端信息"),
        sa.Column("result", sa.String(length=20), nullable=False,
                  comment="success | fail"),
        sa.Column("reason", sa.String(length=50), nullable=False,
                  server_default="", comment="失败原因（错误码）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="时间"),
        comment="登录日志表：只做审计，锁定判断不依赖它",
    )
    op.create_index("idx_login_logs_created", "login_logs", ["created_at"])
    op.create_index("idx_login_logs_user", "login_logs", ["user_id", "created_at"])
    op.create_foreign_key(
        "fk_login_logs_user_id", "login_logs", "users",
        ["user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_table("login_logs")
    op.drop_table("refresh_tokens")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_count")
    op.drop_column("users", "status")
    op.drop_column("users", "password_hash")
