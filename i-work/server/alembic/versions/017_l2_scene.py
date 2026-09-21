"""L2 scene memory: scenes + consolidation checkpoints

阶段：L2 场景记忆（docs/chapters/5-记忆模块(未实现).md 第二部分）。

设计文档把场景当磁盘上的 `.md` 文件，LLM 是"受限的文件操作 agent"（read/write/edit 三个
工具、写 `[DELETED]` 标记删除、工程侧规范化文件名并从文件头重建索引）。本仓库把场景落库，
于是那些只服务于文件系统的机制全部消失：

- 备份场景目录 / 失败整体还原  → 单库事务（失败即回滚，严格更强）
- 删除只能写 `[DELETED]` 标记  → 动作里的 sources 列表
- 全量重建索引                 → 表本身就是索引
- 把导航追加到画像文件末尾     → 导航直接进 system 提示词
- 记忆库镜像（场景同步进记忆库）→ 场景本来就在库里
- `-----META-START-----` 文件头 → summary / heat / 时间戳变成列

保留的是与存储介质无关的内容规则（默认 UPDATE 不是 CREATE、MERGE 必须列旧场景、三级预警、
热度算法）与场景正文的 markdown 章节骨架 —— 正文存在 content 列里，只是不再落成文件。

l2_scenes —— 唯一一张场景内容表。名字是 LLM 引用场景的键，因此有 (user_id, agent_id, name)
的局部唯一索引（`WHERE retrievable`）：merge 掉旧场景后同名重建不能撞索引。被 merge 取代的
旧场景把 retrievable 置 false（软删），行与血缘保留，硬删只留给用户在客户端手删。

l2_checkpoints —— 整合游标（per 作用域，不是 per 会话）。last_memory_at 记到
l1_memories.updated_at 的哪个位置；last_run_at 同时是最小间隔闸门与保底轮询的判据。
游标落库而不是放内存，是为了 sweep 重启后不丢断点。

作用域说明：设计文档的隔离维度是 teamId + agentId，teamId 缺省时退化为 userId。本仓库没有
teams 表，因此直接按 user_id + agent_id 作用域（与 016 同一降级分支）。

Revision ID: 017
Revises: 016
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "l2_scenes",
        sa.Column("id", sa.String(length=64), primary_key=True,
                  comment="s_<epoch_ms>_<hex8>，跨库唯一"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  comment="作用域：所属用户"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="",
                  comment="作用域：agent 标识（顶层为空串）"),
        sa.Column("name", sa.String(length=200), nullable=False,
                  comment="场景名（原文件名），作用域内唯一，导航里对外的键"),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default="",
                  comment="30-40 字摘要，场景导航用"),
        sa.Column("content", sa.Text(), nullable=False,
                  comment="场景叙事正文（markdown，不含 META 头）"),
        sa.Column("heat", sa.Integer(), nullable=False, server_default="1",
                  comment="热度：新建 1 / 更新 旧+1 / 合并 Σ相关+1（doc L2-2.5）"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1",
                  comment="版本号；update/merge 时为目标最大版本 + 1"),
        sa.Column("source_memory_ids", postgresql.JSONB(), nullable=False,
                  server_default="[]", comment="血缘：产出该场景的 L1 记忆 ID"),
        sa.Column("retrievable", sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment="false = 已被 merge 取代（软删），不进导航"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(),
                  comment="创建时间（update/merge 时沿用目标场景最早的创建时间）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="L2 场景记忆表：L1 原子记忆整合出的跨会话叙事",
    )
    # 导航面：按作用域 + retrievable 过滤，按热度排序
    op.create_index("idx_l2_scenes_scope", "l2_scenes",
                    ["user_id", "agent_id", "retrievable"])
    op.create_index("idx_l2_scenes_heat", "l2_scenes", ["user_id", "agent_id", "heat"])
    # 名字唯一 —— 只约束可检索行，merge 后同名重建不撞索引
    op.create_index("uq_l2_scenes_scope_name", "l2_scenes",
                    ["user_id", "agent_id", "name"], unique=True,
                    postgresql_where=sa.text("retrievable"))

    op.create_table(
        "l2_checkpoints",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  comment="作用域：所属用户"),
        sa.Column("agent_id", sa.String(length=64), primary_key=True,
                  server_default="", comment="作用域：agent 标识（顶层为空串）"),
        sa.Column("last_memory_at", sa.DateTime(timezone=True), nullable=True,
                  comment="游标：已处理到的 l1_memories.updated_at"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True,
                  comment="上次成功整合时间；最小间隔闸门与保底轮询的判据"),
        sa.Column("processing_count", sa.Integer(), nullable=False, server_default="0",
                  comment="单调处理计数（doc L2-2.8；L2 内部只自增，供观测）"),
        sa.Column("persona_update_request", sa.Text(), nullable=False, server_default="",
                  comment="LLM 请求刷新 L3 画像的原因（doc L2-2.6 第 4 步）；由 L3 生成侧消费后清空"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="L2 整合游标：每个作用域一条，落库以便重启后续整合",
    )


def downgrade() -> None:
    op.drop_table("l2_checkpoints")
    op.drop_index("uq_l2_scenes_scope_name", table_name="l2_scenes")
    op.drop_index("idx_l2_scenes_heat", table_name="l2_scenes")
    op.drop_index("idx_l2_scenes_scope", table_name="l2_scenes")
    op.drop_table("l2_scenes")
