"""RBAC 权限体系：sys_role / sys_permission / sys_user_role / sys_role_permission / sys_permission_api

阶段：权限管理（docs/chapters/19-权限管理RBAC.md）。

五张表，`users` 已存在（019）不重建：

1. sys_role：角色 + 一条数据范围。`data_scope` 的 server_default 刻意留 'SELF'，
   因为「不显式赋值就收范围」正是文档 2.2 点名的陷阱——种子里的 admin 必须写 'ALL'。
   数据范围只认角色行、不做 admin 短路（与 permissions 的短路规则相反）。
2. sys_permission：目录/菜单/按钮权限树，只存「有哪些权限点」。
   `perms` 为 NULL + UNIQUE 而非 DEFAULT ''：目录行本就没有权限标识，
   都填空串会在 UNIQUE 下互相冲突；NULL 之间不冲突。
3. sys_user_role / sys_role_permission：两张多对多关联表，均带 CASCADE。
4. sys_permission_api：权限点与后端接口的多对多，三列共同主键
   （一个权限点可对应多个 API，一个 API 也可被多个权限点复用）。

建表后不插数据：字典表由启动时的 sync_catalog() 按 catalog.py 对账生成，
业务表（角色、授权）由 seed.py 以 insert-if-missing 方式补齐（doc 19-5.5）。

Revision ID: 020
Revises: 019
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. sys_role ──
    op.create_table(
        "sys_role",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="角色ID"),
        sa.Column("role_name", sa.String(length=50), nullable=False,
                  comment="角色名称（如：管理员）"),
        sa.Column("role_key", sa.String(length=50), nullable=False,
                  comment="角色标识（如：admin）"),
        sa.Column("data_scope", sa.String(length=20), nullable=False,
                  server_default="SELF",
                  comment="数据范围 ALL | SELF（DEPT 需先有部门表，doc 19-5.3）"),
        sa.Column("status", sa.SmallInteger, nullable=False, server_default="1",
                  comment="1=正常 0=禁用；禁用后其授权不参与计算（doc 19-5.1）"),
        comment="角色表：权限集合 + 数据范围；data_scope 默认 SELF 是陷阱（doc 19-2.2）",
    )
    op.create_index("uq_sys_role_key", "sys_role", ["role_key"], unique=True)

    # ── 2. sys_permission ──
    op.create_table(
        "sys_permission",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="权限ID"),
        sa.Column("parent_id", sa.BigInteger, nullable=False, server_default="0",
                  comment="父级ID（0=顶级）"),
        sa.Column("permission_name", sa.String(length=50), nullable=False,
                  comment="菜单/按钮名称"),
        sa.Column("permission_type", sa.String(length=1), nullable=False,
                  comment="M=目录 C=菜单 F=按钮"),
        sa.Column("path", sa.String(length=200), nullable=False, server_default="",
                  comment="前端路由地址（区别于 sys_permission_api.path 的后端接口路径）"),
        sa.Column("component", sa.String(length=255), nullable=False,
                  server_default="", comment="前端组件路径"),
        sa.Column("perms", sa.String(length=100), nullable=True,
                  comment="权限标识（前后端共用）；目录行填 NULL"),
        sa.Column("icon", sa.String(length=100), nullable=False, server_default="",
                  comment="图标"),
        sa.Column("order_num", sa.Integer, nullable=False, server_default="0",
                  comment="排序号"),
        sa.Column("visible", sa.SmallInteger, nullable=False, server_default="1",
                  comment="1=显示 0=隐藏"),
        sa.Column("status", sa.SmallInteger, nullable=False, server_default="1",
                  comment="1=正常 0=禁用"),
        comment="权限字典表：目录/菜单/按钮；由 catalog.py 启动对账生成，不手写 SQL",
    )
    op.create_index("uq_sys_permission_perms", "sys_permission", ["perms"], unique=True)
    op.create_index("idx_sys_permission_parent", "sys_permission", ["parent_id"])

    # ── 3. sys_user_role ──
    op.create_table(
        "sys_user_role",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True,
                  comment="用户ID"),
        sa.Column("role_id", sa.BigInteger, primary_key=True, comment="角色ID"),
        comment="用户-角色关联表",
    )
    op.create_index("idx_sys_user_role_role", "sys_user_role", ["role_id"])
    op.create_foreign_key(
        "fk_sys_user_role_user_id", "sys_user_role", "users",
        ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_sys_user_role_role_id", "sys_user_role", "sys_role",
        ["role_id"], ["id"], ondelete="CASCADE",
    )

    # ── 4. sys_role_permission ──
    op.create_table(
        "sys_role_permission",
        sa.Column("role_id", sa.BigInteger, primary_key=True, comment="角色ID"),
        sa.Column("permission_id", sa.BigInteger, primary_key=True, comment="权限ID"),
        comment="角色-权限关联表；由角色权限配置页维护",
    )
    op.create_foreign_key(
        "fk_sys_role_permission_role_id", "sys_role_permission", "sys_role",
        ["role_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_sys_role_permission_permission_id", "sys_role_permission", "sys_permission",
        ["permission_id"], ["id"], ondelete="CASCADE",
    )

    # ── 5. sys_permission_api ──
    op.create_table(
        "sys_permission_api",
        sa.Column("permission_id", sa.BigInteger, primary_key=True, comment="权限ID"),
        sa.Column("method", sa.String(length=10), primary_key=True,
                  comment="HTTP 方法"),
        sa.Column("path", sa.String(length=200), primary_key=True,
                  comment="接口路径（如 /api/system/user/{id}）"),
        comment="权限点-API 映射表：三列共同主键",
    )
    op.create_foreign_key(
        "fk_sys_permission_api_permission_id", "sys_permission_api", "sys_permission",
        ["permission_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_table("sys_permission_api")
    op.drop_table("sys_role_permission")
    op.drop_table("sys_user_role")
    op.drop_table("sys_permission")
    op.drop_table("sys_role")
