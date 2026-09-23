"""按 user_id 缓存的权限点与数据范围（doc 19-5.1）。

进程内内存 —— `readme.md` 里 uvicorn 没带 `--workers`，就一个进程；
将来上多 worker / 多实例要换成 Redis。

权限点和数据范围**共用一个缓存项**：都是「按 user_id 查角色」，
分两份存没有意义，还多一次失效要记得清。
"""

from __future__ import annotations

import time
from uuid import UUID

TTL_SECONDS = 15 * 60
"""与「封号后旧 token 最长 15 分钟仍可用」同一量级，不必为它单独设计失效（doc 18-8.5）。"""

# user_id → (权限点集合, 数据范围, 所属部门, 过期时刻)
# 部门也要进来：`DEPT` 档的数据范围是在**主体自己的部门子树**上算的，
# 每次现查等于每请求多一条查询（doc 19-5.3）。
_entries: dict[UUID, tuple[frozenset[str], str, int | None, float]] = {}


def get(user_id: UUID) -> tuple[frozenset[str], str, int | None] | None:
    """取缓存；未命中或已过期返回 None（顺手清掉过期项）。"""
    entry = _entries.get(user_id)
    if entry is None:
        return None
    perms, scope, dept_id, expires_at = entry
    if expires_at <= time.monotonic():
        _entries.pop(user_id, None)
        return None
    return perms, scope, dept_id


def put(
    user_id: UUID, perms: set[str] | frozenset[str], scope: str, dept_id: int | None,
) -> None:
    _entries[user_id] = (frozenset(perms), scope, dept_id, time.monotonic() + TTL_SECONDS)


def invalidate(user_id: UUID) -> None:
    _entries.pop(user_id, None)


def clear_all() -> None:
    """授权变更后整体清空（doc 19-5.1 的"写穿"）。

    不做按角色反查受影响用户：单进程下缓存里一活跃用户一条，
    授权变更是低频管理动作；按角色反查要给 sys_user_role.role_id 建索引
    并多写一条查询，收益要到上多实例换 Redis 时才体现。

    部门树的改动（挪父节点 / 删部门）也走这里 —— 整棵子树上所有人的可见范围
    都变了，同样反查不出是谁（doc 19-5.3）。
    """
    _entries.clear()
