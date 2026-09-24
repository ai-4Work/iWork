"""模型配置：压缩触发线与上下文窗口解耦

阶段：模型配置细化（管理页可调压缩时机）。

在此之前 `llm_model.context_window` 一个字段担两个语义 —— 既是"这模型能装多少"的
能力声明，又是压缩的触发输入（`ContextCompressor` 判 `估算 >= 窗口 × 0.80`）。结果是
管理员想调压缩时机只能去改小窗口，等于填一个假的模型能力值。

一件事：加一个可空的 `compress_threshold_tokens`（绝对 token 数）。**刻意不回填**：

* NULL = 未设置 → 引擎仍按 `窗口 × 比例`（build 0.80 / ask 0.55）折算。升级后已有行
  的行为与加列前逐字一致，且日后改窗口时它们自动跟随。
* 回填成 `窗口 × 0.8` 会把 ask 模式的 0.55 静默钉成 0.80，还让"跟着窗口走"这个
  默认语义消失 —— 那是在替管理员做一个他没做过的决定。
* 另一个好处：远端旧进程跑的还是没有这一列的代码，它的 INSERT 不带该列 → NULL，
  两边都读得动。纯加列、可空，对共享库 `agent_test` 是安全操作。

Revision ID: 024
Revises: 023
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_model",
        sa.Column(
            "compress_threshold_tokens", sa.Integer, nullable=True,
            comment="上下文压缩触发线（绝对 token 数）；留空 = 按 context_window 折（build 80% / ask 55%）",
        ),
    )


def downgrade() -> None:
    op.drop_column("llm_model", "compress_threshold_tokens")
