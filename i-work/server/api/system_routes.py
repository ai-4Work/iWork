"""角色权限配置接口（docs/chapters/19-权限管理RBAC.md §4.2）。

只做"读角色/权限点 + 改授予 + 改数据范围"四件事，角色的增删改不在本期
（目录/菜单/按钮由 `authz.catalog` 启动对账生成，不从这里写）。

所有写接口在提交审计后清空授权缓存 —— 不清的话被改的角色最多 15 分钟内
还按旧权限放行（doc 19-5.1 的写穿要求）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.api.deps import get_current_user, get_db, require_permission
from server.authz.cache import clear_all as clear_authz_cache
from server.authz.catalog import ALL_PERMS_ROLE_KEYS
from server.authz.service import load_user_scope_ctx, scope_rank
from server.observability.audit import audit_log
from server.storage.postgres import (
    PermissionRepo,
    RolePermissionRepo,
    RoleRepo,
)

router_system = APIRouter(prefix="/system", tags=["system"])

# sys_role.data_scope 的合法取值（doc 19-2.2）。
# DEPT 是「本部门及下级」，靠 sys_dept.parent_id 算子树（doc 19-5.3）。
VALID_DATA_SCOPES = ("ALL", "DEPT", "SELF")


class GrantRequest(BaseModel):
    permission_ids: list[int]


class DataScopeRequest(BaseModel):
    data_scope: str


def _role_repo(request: Request) -> RoleRepo:
    return RoleRepo(request.app.state.db_session_factory)


def _permission_repo(request: Request) -> PermissionRepo:
    return PermissionRepo(request.app.state.db_session_factory)


def _role_perm_repo(request: Request) -> RolePermissionRepo:
    return RolePermissionRepo(request.app.state.db_session_factory)


# ═══════════════════════════════════════════════════════════════
# 读
# ═══════════════════════════════════════════════════════════════

@router_system.get("/role/list")
async def list_roles(
    request: Request,
    _: None = Depends(require_permission("system:role:list")),
):
    roles = await _role_repo(request).list_all()
    return {
        "roles": [
            {
                "id": r.id,
                "role_name": r.role_name,
                "role_key": r.role_key,
                "data_scope": r.data_scope,
                "status": r.status,
            }
            for r in roles
        ]
    }


@router_system.get("/permission/list")
async def list_permissions(
    request: Request,
    _: None = Depends(require_permission("system:permission:list")),
):
    """返回权限字典全量。

    刻意不下发 `path` / `component`：那是给将来带路由的后台页留的占位，
    当前 agent-client 用不上（doc 19-4.2）。
    """
    perms = await _permission_repo(request).list_all()
    return {
        "permissions": [
            {
                "id": p.id,
                "parent_id": p.parent_id,
                "permission_name": p.permission_name,
                "permission_type": p.permission_type,
                "perms": p.perms,
                "order_num": p.order_num,
            }
            for p in perms
        ]
    }


@router_system.get("/role/{role_id}/permissions")
async def get_role_permissions(
    role_id: int,
    request: Request,
    _: None = Depends(require_permission("system:role:query")),
):
    """某角色当前被授予的权限点 id 列表。

    admin 角色没有授权行（它靠代码短路成全集），所以这里返回空数组 ——
    前端对 admin 列只读恒勾选，不看这个值。
    """
    role = await _role_repo(request).get(role_id)
    if role is None:
        raise HTTPException(
            404, detail={"error": "ROLE_NOT_FOUND", "message": "角色不存在"}
        )
    ids = await _role_perm_repo(request).list_permission_ids(role_id)
    return {"role_id": role_id, "permission_ids": ids}


# ═══════════════════════════════════════════════════════════════
# 写
# ═══════════════════════════════════════════════════════════════

@router_system.put("/role/{role_id}/permissions")
async def grant_role_permissions(
    role_id: int,
    body: GrantRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:role:grant")),
):
    """整集替换：界面语义是"勾完保存"，不是增量（doc 19-3.1）。"""
    role = await _role_repo(request).get(role_id)
    if role is None:
        raise HTTPException(
            404, detail={"error": "ROLE_NOT_FOUND", "message": "角色不存在"}
        )
    if role.role_key in ALL_PERMS_ROLE_KEYS:
        # 这些角色是代码短路成全集的，给它写授权行改了也不生效 —— 拦住比
        # 静默无效好，否则管理员会以为"取消勾选"成功了。
        raise HTTPException(
            400,
            detail={
                "error": "ROLE_PERMS_IMMUTABLE",
                "message": f"{role.role_key} 短路为全集，授权不可编辑",
            },
        )

    known = await _permission_repo(request).list_ids()
    unknown = sorted(set(body.permission_ids) - known)
    if unknown:
        raise HTTPException(
            400,
            detail={
                "error": "UNKNOWN_PERMISSION",
                "message": f"权限点不存在：{unknown}",
            },
        )

    count = await _role_perm_repo(request).replace(role_id, body.permission_ids)
    clear_authz_cache()
    # audit_log 只 execute 不 commit；这里的 db session 只承载这条审计，
    # 不显式提交会在 session 关闭时被回滚掉。
    await audit_log(
        db,
        action="admin.role_permission_changed",
        user_id=user_id,
        resource=f"role/{role_id}",
        detail={"role_id": role_id, "count": count},
    )
    await db.commit()
    return {"updated": True, "count": count}


@router_system.put("/role/{role_id}")
async def set_role_data_scope(
    role_id: int,
    body: DataScopeRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:role:edit")),
):
    """改数据范围。superadmin 也走这里 —— 它的 `ALL` 就存在角色行上，不是短路的。

    数据范围是**唯一**区分超管与管理员的东西，所以这道守卫是它们能安全地
    拥有同一套权限点的前提：没有它，`dept_admin` 把自己的范围改成 `ALL` 就是超管。
    """
    if body.data_scope not in VALID_DATA_SCOPES:
        raise HTTPException(
            400,
            detail={
                "error": "INVALID_DATA_SCOPE",
                "message": f"数据范围只能是 {' / '.join(VALID_DATA_SCOPES)}",
            },
        )
    role = await _role_repo(request).get(role_id)
    if role is None:
        raise HTTPException(
            404, detail={"error": "ROLE_NOT_FOUND", "message": "角色不存在"}
        )

    caller_scope, _ = await load_user_scope_ctx(db, user_id)
    if caller_scope != "ALL":
        # 两头都拦：够不着比自己宽的角色（否则改得动 admin 那行），
        # 也改不出比自己宽的范围（否则自己那行就是提权入口）
        if scope_rank(role.data_scope) > scope_rank(caller_scope):
            raise HTTPException(
                400,
                detail={
                    "error": "ROLE_SCOPE_EXCEEDED",
                    "message": "不能修改数据范围比自己宽的角色",
                },
            )
        if scope_rank(body.data_scope) > scope_rank(caller_scope):
            raise HTTPException(
                400,
                detail={
                    "error": "ROLE_SCOPE_EXCEEDED",
                    "message": "不能把角色的数据范围改成比自己宽",
                },
            )

    await _role_repo(request).set_data_scope(role_id, body.data_scope)
    clear_authz_cache()
    await audit_log(
        db,
        action="admin.role_data_scope_changed",
        user_id=user_id,
        resource=f"role/{role_id}",
        detail={"role_id": role_id, "data_scope": body.data_scope},
    )
    await db.commit()
    return {"updated": True}
