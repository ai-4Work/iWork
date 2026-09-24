"""部门（组织架构）接口（docs/chapters/19-权限管理RBAC.md §2.2）。

只做"读部门树 + 增删改部门"这几件事。用户归**用户面**管（`user_routes.py`）：
那面原来寄在本文件里，等它六个点齐了就分了出去，现在两边各管各的。

写入一律 `audit_log` + `await db.commit()`：`audit_log` 只 execute 不 commit。

数据范围（doc 19-5.3）在这个文件里的落点：建 / 挪 / 删部门前，目标与目的地都要在
自己范围内（`assert_dept_in_scope`）—— 在别人的子树里挂节点或拆节点，等于替别人改
可见集。判据收在 `scope_guards.py`，用户面也在用同一份。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.api.deps import (
    assert_permission, get_current_user, get_db, require_permission,
)
from server.api.scope_guards import assert_dept_in_scope
from server.authz.cache import clear_all
from server.authz.catalog import DEFAULT_DEPT_KEY
from server.authz.service import load_user_scope_ctx
from server.observability.audit import audit_log
from server.storage.postgres import DeptRepo

router_dept = APIRouter(prefix="/system", tags=["system"])


class DeptCreateRequest(BaseModel):
    parent_id: int = 0
    dept_name: str
    order_num: int = 0


class DeptUpdateRequest(BaseModel):
    parent_id: int
    dept_name: str
    order_num: int = 0


def _dept_repo(request: Request) -> DeptRepo:
    return DeptRepo(request.app.state.db_session_factory)


# ═══════════════════════════════════════════════════════════════
# 读
# ═══════════════════════════════════════════════════════════════

@router_dept.get("/dept/list")
async def list_depts(
    request: Request,
    _: None = Depends(require_permission("system:dept:list")),
):
    """全量部门，前端自己拼树（不在这里做嵌套，省得为展示形状改接口）。"""
    depts = await _dept_repo(request).list_all()
    return {
        "depts": [
            {
                "id": d.id,
                "parent_id": d.parent_id,
                "dept_name": d.dept_name,
                "dept_key": d.dept_key,
                "order_num": d.order_num,
                "status": d.status,
            }
            for d in depts
        ]
    }


# ═══════════════════════════════════════════════════════════════
# 写
# ═══════════════════════════════════════════════════════════════

@router_dept.post("/dept")
async def create_dept(
    body: DeptCreateRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(
        require_permission("system:dept:addRoot", "system:dept:addChild")
    ),
):
    """建部门。顶级（`parent_id = 0`）与子部门是两个权限点，同一支 API。

    依赖只能按"任一即可"放行——它看不到请求体；这里按 `parent_id` 挑准那一个。
    放在最前：没权限的人不该从 400/404 里看出部门存不存在。
    """
    await assert_permission(
        db,
        user_id,
        "system:dept:addRoot" if body.parent_id == 0 else "system:dept:addChild",
    )
    name = body.dept_name.strip()
    if not name:
        raise HTTPException(
            400, detail={"error": "INVALID_DEPT_NAME", "message": "部门名称不能为空"}
        )
    repo = _dept_repo(request)
    if body.parent_id != 0 and await repo.get(body.parent_id) is None:
        raise HTTPException(
            400, detail={"error": "PARENT_NOT_FOUND", "message": "上级部门不存在"}
        )
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, user_id)
    # `parent_id = 0` 是"新建顶级部门"，对非 ALL 主体算范围外（fail-closed）：
    # 在别人的子树里挂节点，等于替别人改可见集（doc 19-5.3.1）
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, body.parent_id)

    dept = await repo.create(
        parent_id=body.parent_id, dept_name=name, order_num=body.order_num,
    )
    await audit_log(
        db,
        action="admin.dept_created",
        user_id=user_id,
        resource=f"dept/{dept.id}",
        detail={"dept_id": dept.id, "parent_id": body.parent_id},
    )
    await db.commit()
    return {"id": dept.id}


@router_dept.put("/dept/{dept_id}")
async def update_dept(
    dept_id: int,
    body: DeptUpdateRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:dept:edit")),
):
    name = body.dept_name.strip()
    if not name:
        raise HTTPException(
            400, detail={"error": "INVALID_DEPT_NAME", "message": "部门名称不能为空"}
        )
    repo = _dept_repo(request)
    if await repo.get(dept_id) is None:
        raise HTTPException(
            404, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )
    # 环会让前端拼树时无限递归。挂在某个后代下面 == 把自己挂到自己下面。
    if body.parent_id == dept_id or await _is_descendant(repo, dept_id, body.parent_id):
        raise HTTPException(
            400,
            detail={
                "error": "DEPT_CYCLE",
                "message": "不能把部门挂到自己或自己的下级部门上",
            },
        )
    if body.parent_id != 0 and await repo.get(body.parent_id) is None:
        raise HTTPException(
            400, detail={"error": "PARENT_NOT_FOUND", "message": "上级部门不存在"}
        )
    # 挪树就是这个人的可见集本身，两头都必须在范围内：
    # 目标部门在范围外 → 他看不见；新父节点在范围外 → 挪完就看得见别人了（doc 19-5.3.1）
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, user_id)
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, dept_id)
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, body.parent_id)

    await repo.update(dept_id, name, body.parent_id, body.order_num)
    # 挪树改了整棵子树的形状，受影响的人反查不出来 —— 走 clear_all（doc 19-5.1）
    clear_all()
    await audit_log(
        db,
        action="admin.dept_updated",
        user_id=user_id,
        resource=f"dept/{dept_id}",
        detail={"dept_id": dept_id, "parent_id": body.parent_id},
    )
    await db.commit()
    return {"updated": True}


@router_dept.delete("/dept/{dept_id}")
async def delete_dept(
    dept_id: int,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:dept:remove")),
):
    """三条守卫都拒删，不静默改数据。

    删部门时不常做的事，静默把成员搬去默认部门比直接报错更难排查；
    默认部门本身也不能删 —— 它是所有人的兜底归属。
    """
    repo = _dept_repo(request)
    dept = await repo.get(dept_id)
    if dept is None:
        raise HTTPException(
            404, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )
    if dept.dept_key == DEFAULT_DEPT_KEY:
        raise HTTPException(
            400,
            detail={"error": "DEPT_DEFAULT_IMMUTABLE", "message": "默认部门不可删除"},
        )
    if await repo.count_children(dept_id):
        raise HTTPException(
            400,
            detail={"error": "DEPT_HAS_CHILDREN", "message": "请先删除下级部门"},
        )
    if await repo.count_members(dept_id):
        raise HTTPException(
            400,
            detail={"error": "DEPT_HAS_MEMBERS", "message": "请先把成员移到别的部门"},
        )
    # 同上：范围外的部门对非 ALL 主体按「不存在」报（doc 19-5.3.1）
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, user_id)
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, dept_id)

    await repo.delete(dept_id)
    # 同挪树：整棵子树没了，受影响的人反查不出来
    clear_all()
    await audit_log(
        db,
        action="admin.dept_removed",
        user_id=user_id,
        resource=f"dept/{dept_id}",
        detail={"dept_id": dept_id, "dept_name": dept.dept_name},
    )
    await db.commit()
    return {"deleted": True}


async def _is_descendant(repo: DeptRepo, ancestor_id: int, candidate_id: int) -> bool:
    """`candidate_id` 是不是 `ancestor_id` 的后代。沿 parent_id 往上走。"""
    seen: set[int] = set()
    current = candidate_id
    while current != 0 and current not in seen:
        seen.add(current)
        parent = await repo.get(current)
        if parent is None:
            return False
        if parent.parent_id == ancestor_id:
            return True
        current = parent.parent_id
    return False




