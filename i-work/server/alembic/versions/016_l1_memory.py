"""L1 atomic memory: records + extraction checkpoints

阶段：L1 原子记忆（docs/chapters/5-记忆模块(未实现).md 第一部分）。

两张表：

l1_memories —— 唯一一张记忆内容表。设计文档里的"产出文档（append-only 事实源）
+ 向量库（检索镜像）"双写在 Postgres 里是同一个失败域，双写容错失去意义，因此
合并成一张表：行永不硬删，被 update/merge 取代的旧行把 retrievable 置 false，
事实源与血缘语义（source_message_ids 始终保留）不变。唯一的硬删路径是用户在
客户端手工删除。

l1_checkpoints —— 抽取游标（per session）。last_cursor 记到 conversation_history
的哪个 sequence，last_scene_name 是喂给下一次抽取的"上一个情境"。游标落库而不是
放内存，是为了 sweep 重启后不丢断点。

作用域说明：设计文档的隔离维度是 teamId + agentId，teamId 缺省时退化为 userId。
本仓库没有 teams 表，因此直接按 user_id + agent_id 作用域（即文档的降级分支）。
将来引入团队时再加 team_id 列并改检索范围。

Revision ID: 016
Revises: 015
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "l1_memories",
        sa.Column("id", sa.String(length=64), primary_key=True,
                  comment="m_<epoch_ms>_<hex8>，跨库唯一"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  comment="作用域：所属用户"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="",
                  comment="作用域：agent 标识（顶层为空串）"),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True,
                  comment="抽取来源会话（跨会话累积，仅作溯源）"),
        sa.Column("content", sa.Text(), nullable=False,
                  comment="自包含的记忆陈述"),
        sa.Column("type", sa.String(length=20), nullable=False,
                  comment="persona | episodic | instruction"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0",
                  comment="重要度打分；各类型有各自的丢弃阈值"),
        sa.Column("scene_name", sa.String(length=200), nullable=False, server_default="",
                  comment="情境名：我（AI）在和xxx做xxx"),
        sa.Column("source_message_ids", postgresql.JSONB(), nullable=False,
                  server_default="[]", comment="血缘：产出该记忆的 L0 消息 ID"),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False,
                  server_default="{}", comment="类型专属元数据；episodic 带活动起止时间"),
        sa.Column("timestamps", postgresql.JSONB(), nullable=False,
                  server_default="[]", comment="时间轨迹，merge 时并集去重排序"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1",
                  comment="版本号；update/merge 时为目标最大版本 + 1"),
        sa.Column("retrievable", sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment="false = 已被 update/merge 取代（软删），不进检索"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="L1 原子记忆表：自动抽取的结构化事实碎片",
    )
    # 检索面：按作用域 + retrievable 过滤
    op.create_index("idx_l1_memories_scope", "l1_memories",
                    ["user_id", "agent_id", "retrievable"])
    op.create_index("idx_l1_memories_session", "l1_memories", ["session_id"])
    op.create_index("idx_l1_memories_updated", "l1_memories", ["updated_at"])

    op.create_table(
        "l1_checkpoints",
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True,
                  comment="所属会话"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False,
                  comment="冗余的作用域字段，sweep 单查即可分组"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="",
                  comment="作用域：agent 标识"),
        sa.Column("last_cursor", sa.BigInteger(), nullable=False, server_default="0",
                  comment="已处理到的 conversation_history.sequence"),
        sa.Column("last_scene_name", sa.String(length=200), nullable=False,
                  server_default="", comment="上一个情境名，供下次抽取判断是否切换"),
        sa.Column("last_extracted_at", sa.DateTime(timezone=True), nullable=True,
                  comment="上次成功抽取时间，空闲兜底的判据"),
        comment="L1 抽取游标：每条会话一条，落库以便重启后续抽",
    )
    op.create_index("idx_l1_checkpoints_scope", "l1_checkpoints", ["user_id", "agent_id"])


def downgrade() -> None:
    op.drop_index("idx_l1_checkpoints_scope", table_name="l1_checkpoints")
    op.drop_table("l1_checkpoints")
    op.drop_index("idx_l1_memories_updated", table_name="l1_memories")
    op.drop_index("idx_l1_memories_session", table_name="l1_memories")
    op.drop_index("idx_l1_memories_scope", table_name="l1_memories")
    op.drop_table("l1_memories")
