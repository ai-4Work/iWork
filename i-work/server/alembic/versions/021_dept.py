"""组织架构：新建 sys_dept，给 users 加 dept_id

阶段：部门层（docs/chapters/19-权限管理RBAC.md §2.2）。

两件事：

1. sys_dept：部门树。`dept_key` 是 NULL + UNIQUE 而非 DEFAULT ''：只有代码要按业务键
   认行的行才填（默认部门 = 'default'），用户建的部门填 NULL，NULL 之间不冲突 ——
   同 sys_permission.perms 的处理。
2. users.dept_id：可空外键，`ondelete="RESTRICT"`。不是 CASCADE/SET NULL ——
   删除部门由接口层守卫（有子部门或有成员就拒），数据库这层用 RESTRICT 把意图
   也写死，万一有路径绕过接口也不会静默把人的归属清掉。

建表后种子把 NULL 的 dept_id 全部回填成默认部门（server/db/seed.py 的 seed_dept），
所以这一列在正常运行时不会长期为空。

Revision ID: 021
Revises: 020
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. sys_dept ──
    op.create_table(
        "sys_dept",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="部门ID"),
        sa.Column("parent_id", sa.BigInteger, nullable=False, server_default="0",
                  comment="父级ID（0=顶级）"),
        sa.Column("dept_name", sa.String(length=50), nullable=False,
                  comment="部门名称"),
        sa.Column("dept_key", sa.String(length=50), nullable=True,
                  comment="业务键；只有代码要按它认行的字典行才填（默认部门=default），"
                          "用户建的部门填 NULL"),
        sa.Column("order_num", sa.Integer, nullable=False, server_default="0",
                  comment="排序号"),
        sa.Column("status", sa.SmallInteger, nullable=False, server_default="1",
                  comment="1=正常 0=禁用"),
        comment="部门表：组织架构树；种子保证「默认部门」始终存在",
    )
    op.create_index("uq_sys_dept_key", "sys_dept", ["dept_key"], unique=True)
    op.create_index("idx_sys_dept_parent", "sys_dept", ["parent_id"])

    # ── 2. users.dept_id ──
    op.add_column(
        "users",
        sa.Column("dept_id", sa.BigInteger, nullable=True,
                  comment="所属部门；种子把 NULL 回填成默认部门（doc 19-2.2）"),
    )
    op.create_foreign_key(
        "fk_users_dept_id", "users", "sys_dept",
        ["dept_id"], ["id"], ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_dept_id", "users", type_="foreignkey")
    op.drop_column("users", "dept_id")

    op.drop_index("idx_sys_dept_parent", table_name="sys_dept")
    op.drop_index("uq_sys_dept_key", table_name="sys_dept")
    op.drop_table("sys_dept")
