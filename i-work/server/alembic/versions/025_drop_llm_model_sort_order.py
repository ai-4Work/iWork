"""模型配置：去掉「排序」这个概念

阶段：模型配置细化（管理页不再有排序栏）。

`llm_model.sort_order` 决定三件事：管理页列表顺序、聊天下拉的先后、以及"没指定模型
时用哪个"的回落末位（`ModelResolver._preferred_fallback` 的 `enabled[0]`）。管理页
那个输入框本身没人用，管理员也看不出它牵连这么多，于是整条拆掉。

排序退回 `id`（建库先后），三处 `order_by` 都改成只按 id。因为库里三行的 `sort_order`
全是 0，改动前后顺序一致 —— 不是"换了套排序"，而是"删掉一个从没起过作用的旋钮"。
想指定默认模型仍有入口：`IWORK_DEFAULT_MODEL` 优先于"最早建的那条"。

两件事：

1. `idx_llm_model_enabled` 的定义是 `(enabled, sort_order)`（见 023），承载的列要没了，
   先删索引再删列。**不重建**成 `(enabled)`：它本是给 `WHERE enabled ORDER BY
   sort_order` 用的，排序退回主键后，三行的表不值得再留一个低基数索引。
2. 删列是**破坏性**的：三行里的值真没了（全是 0，无损失）。同一套代码必须先部署，
   否则旧进程的 `order_by(OrmLlmModel.sort_order, ...)` 会因为列不存在而直接报错。

Revision ID: 025
Revises: 024
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("idx_llm_model_enabled", table_name="llm_model")
    op.drop_column("llm_model", "sort_order")


def downgrade() -> None:
    op.add_column(
        "llm_model",
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0",
                  comment="排序号，决定下拉里的先后"),
    )
    op.create_index("idx_llm_model_enabled", "llm_model", ["enabled", "sort_order"])
