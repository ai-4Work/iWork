"""L3 persona memory: one row per (user, agent) scope

阶段：L3 画像记忆（docs/chapters/5-记忆模块(未实现).md 第三部分）。

设计文档把画像当一份磁盘上的 `persona.md` 文件，LLM 是"文件代理"（write/edit 工具、沙箱限定
数据目录、只准操作一个文件、保留 3 份备份、对象存储模式、正文与场景导航一体落地）。本仓库把
画像落库，于是那些只服务于文件系统的机制全部消失：

- 备份 3 份 / 对象存储模式      → 单事务原子写，不存在"写坏一半"
- LLM 用文件工具写文件          → 一次 LLM 调用返回画像全文，工程侧落库
- 读回 + 剥掉尾部场景导航        → 导航是 L2 自己的表字段，从不进画像
- 追加导航 + 写回               → 导航由 L2 侧渲染注入 system 提示词
- P2.5「画像文件丢失/被误删」恢复 → 行不会损坏；真没有就是没生成过，由 P2 / P3 兜
- 进程内互斥 / 分布式锁          → 单进程 60s 轮询、串行执行，无锁（同 L2）

l3_personas —— 唯一一张画像表，主键 (user_id, agent_id)。**刻意不建 l3_checkpoints**：
画像行本身既是产物也是游标（updated_at = 上次生成时间，据它筛变化场景；行的存在与否 =
有没有画像，据它定首次/增量与冷启动），memory_count_at_generation 是 P4 阈值算增量用的
快照。触发评估只在 L2 的扫描里级联跑，没有独立的定时任务。

作用域与 016 / 017 同一条降级分支：设计文档的 teamId + agentId，本仓库没有 teams 表，
直接按 user_id + agent_id。

Revision ID: 018
Revises: 017
Create Date: 2026-09-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "l3_personas",
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
                  comment="作用域：所属用户"),
        sa.Column("agent_id", sa.String(length=64), primary_key=True,
                  server_default="", comment="作用域：agent 标识（顶层为空串）"),
        sa.Column("content", sa.Text(), nullable=False,
                  comment="画像正文（后处理之后的最终内容）"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1",
                  comment="版本号：每次重写 +1，首次插入为 1"),
        sa.Column("memory_count_at_generation", sa.Integer(), nullable=False,
                  server_default="0",
                  comment="本次生成时的 L1 记忆总数快照；P4 阈值据『当前总数 − 它』算增量"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(),
                  comment="首次生成时间（增量重写时沿用）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(),
                  comment="最后生成时间；L3-2.2 据此筛变化场景"),
        comment="L3 画像记忆表：L2 场景叙事综合出的身份文档",
    )


def downgrade() -> None:
    op.drop_table("l3_personas")
