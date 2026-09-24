"""写侧的数据范围断言（doc 19-5.3.1）。

部门面（`dept_routes.py`）与用户面（`user_routes.py`）都要在写之前确认「我这个主体
动得了这个对象吗」，两边各写一份迟早会分叉，所以收在这里一处。

两个断言都报「不存在」而不是「无权」：403 等于告诉对方「这东西存在，只是你看不了」，
可以拿来枚举。判据本身在 `authz.service.visible_dept_ids` 与本文件，读侧那三档在
`UserRepo.list_visible`。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from server.authz.service import visible_dept_ids


async def assert_user_in_scope(
    db,
    caller_id: UUID,
    caller_scope: str,
    caller_dept_id: int | None,
    target_id: UUID,
    target_dept_id: int | None,
) -> None:
    """目标用户是否落在调用者的数据范围内，不在就 404。

    不带前缀的公开名字 —— 与下面那个一起供两个路由模块 import，所以不能再叫
    `_assert_*`（下划线前缀跨模块 import 是明文禁止的约定，虽然 Python 不拦）。
    """
    if caller_scope == "ALL":
        return
    if caller_scope == "DEPT":
        if target_dept_id in await visible_dept_ids(db, caller_dept_id):
            return
    elif target_id == caller_id:
        # SELF，以及清单外的取值 —— 都按最窄算（fail-closed）
        return
    raise HTTPException(
        404, detail={"error": "USER_NOT_FOUND", "message": "用户不存在"}
    )


async def assert_dept_in_scope(
    db, caller_scope: str, caller_dept_id: int | None, dept_id: int,
) -> None:
    """目标部门是否落在调用者的数据范围内，不在就按「部门不存在」报。

    没有这一道，部门管理员能把自己部门的人**挪出去** —— 被操作的人在自己范围内，
    目的地却在范围外，读侧看不见、写侧却动得了。
    """
    if caller_scope == "ALL":
        return
    if caller_scope == "DEPT" and dept_id in await visible_dept_ids(db, caller_dept_id):
        return
    raise HTTPException(
        400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
    )
