"""删掉 default-user 这个临时身份

阶段：登录认证上线后，清掉「没有登录」时期的产物（docs/chapters/18-登录认证模块.md ch.13）。

`default-user` 是登录做出来之前所有接口共用的身份（`Depends(get_default_user_id)`
全部返回同一个 UUID）。登录上线后它的调用方已经删干净，但创建路径留在种子里
（`seed_default_user` / `UserRepo.get_or_create_default`），于是它以「默认用户」
的身份一直留在部门配置页的成员表里。本轮把创建路径一并删掉，这里负责删行。

⚠️ 不可逆地丢数据：`users` 的外键全是 `ON DELETE CASCADE`（sessions、messages、
audit_logs、l1_*、l2_*、l3_*、user_skills、user_mcp_servers、rules…），删这一行会
级联清空它名下的全部记录。要保留请先导出。

Revision ID: 022
Revises: 021
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM users WHERE username = 'default-user'")


def downgrade() -> None:
    """数据回不来，降级是空操作。

    要重建只能再跑一次种子，而那个种子已经删了 —— 想恢复就自己 INSERT 一行
    `username = 'default-user'`，但那时候它名下的数据已经没了，恢复了也是个空账号。
    """
