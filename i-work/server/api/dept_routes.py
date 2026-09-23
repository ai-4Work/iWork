"""部门（组织架构）、用户归属与用户角色接口（docs/chapters/19-权限管理RBAC.md §2.2）。

只做"读部门 + 增删改部门 + 建号 + 挪人 + 给用户挂角色"这几件事，用户管理的其余
五个点（详情/改/删/导出/重置密码）仍是 doc 19-4.2 的"待建"，在 catalog 里标着 planned。

`GET /user/list` 也放这里，没有单开 user_routes：它现在只为部门配置页服务。
等其余四个点真做了，这几个接口该跟着搬过去 —— 顺带把 doc 19-5.3 说的
「查用户四类入口」补齐（现在只落了列表这一类）。

写入一律 `audit_log` + `await db.commit()`：`audit_log` 只 execute 不 commit。

数据范围（doc 19-5.3）在这个文件里有四处落点，改任何一处都要想想另外三处：
1. 读侧：`GET /user/list` 按主体的 `ALL / DEPT / SELF` 裁剪。
2. 写侧：挪人和挂角色前，先确认目标用户在自己范围内（`_assert_in_scope`）。
3. 写侧：挂角色时不能授出比自己宽的角色（`scope_rank` 比宽窄）。
4. 写侧：建号时目标部门要在自己范围内 —— 这一条就是"超管任意、部门管理员只在本
   部门及下级"的全部实现。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.api.deps import (
    get_auth_service, get_current_user, get_db, require_permission,
)
from server.auth.service import AuthError
from server.authz.cache import clear_all, invalidate
from server.authz.catalog import ALL_PERMS_ROLE_KEYS, DEFAULT_DEPT_KEY
from server.authz.service import (
    load_user_scope_ctx, scope_rank, visible_dept_ids,
)
from server.observability.audit import audit_log
from server.storage.postgres import DeptRepo, RoleRepo, UserRepo, UserRoleRepo

router_dept = APIRouter(prefix="/system", tags=["system"])


class DeptCreateRequest(BaseModel):
    parent_id: int = 0
    dept_name: str
    order_num: int = 0


class DeptUpdateRequest(BaseModel):
    parent_id: int
    dept_name: str
    order_num: int = 0


class UserCreateRequest(BaseModel):
    username: str
    password: str
    dept_id: int


class UserDeptRequest(BaseModel):
    dept_id: int


class UserRolesRequest(BaseModel):
    """整集替换，不是增量 —— 与 `GrantRequest`（system_routes.py）同语义。"""
    role_ids: list[int]


def _dept_repo(request: Request) -> DeptRepo:
    return DeptRepo(request.app.state.db_session_factory)


def _user_repo(request: Request) -> UserRepo:
    return UserRepo(request.app.state.db_session_factory)


def _user_role_repo(request: Request) -> UserRoleRepo:
    return UserRoleRepo(request.app.state.db_session_factory)


def _role_repo(request: Request) -> RoleRepo:
    return RoleRepo(request.app.state.db_session_factory)


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


@router_dept.get("/user/list")
async def list_users(
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:list")),
):
    """可见用户列表（带所属部门和角色 id）。

    数据范围在服务端算、不接受客户端传值（doc 19-5.3）：
    `SELF` 只看自己，`DEPT` 看本部门及下级的全部人，`ALL` 不加条件。

    `role_ids` 只给 id 不给角色名 —— 名字由客户端拿 `GET /system/role/list` 对，
    和 `dept_id` ↔ `/dept/list` 一个路子。
    """
    scope, dept_id = await load_user_scope_ctx(db, user_id)
    dept_ids = await visible_dept_ids(db, dept_id) if scope == "DEPT" else None
    users = await _user_repo(request).list_visible(user_id, scope, dept_ids)
    roles_by_user = await _user_role_repo(request).list_role_ids_by_users([u.id for u in users])
    return {
        "users": [
            {
                "id": str(u.id),
                "username": u.username,
                "display_name": u.display_name,
                "dept_id": u.dept_id,
                "status": u.status,
                "role_ids": roles_by_user.get(u.id, []),
            }
            for u in users
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
    _: None = Depends(require_permission("system:dept:add")),
):
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
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, body.parent_id)

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
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, dept_id)
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, body.parent_id)

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
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, dept_id)

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


@router_dept.post("/user")
async def create_user(
    body: UserCreateRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:add")),
):
    """管理员建号，密码由建号的人转交给本人（doc 19-4.2）。

    开放注册已下线 —— 谁都能刷账号、注册完还自动落进默认部门，而组织架构本该
    是管理员在管的。角色固定给默认角色（普通用户），要调去成员表的角色下拉。

    范围判据就是「目标部门在自己子树内」：超管 `ALL` 短路放行、部门管理员只在本
    部门及下级（`_assert_dept_in_scope`）。范围外报 400 `DEPT_NOT_FOUND`。
    """
    if await _dept_repo(request).get(body.dept_id) is None:
        raise HTTPException(
            400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, caller_id)
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, body.dept_id)

    try:
        user = await get_auth_service(request).create_user(
            body.username, body.password, body.dept_id
        )
    except AuthError as exc:
        raise HTTPException(
            exc.status, detail={"error": exc.code, "message": exc.message}
        )
    await audit_log(
        db,
        action="admin.user_created",
        user_id=caller_id,
        resource=f"user/{user.id}",
        detail={"username": user.username, "dept_id": body.dept_id},
    )
    await db.commit()
    return {"id": str(user.id), "username": user.username}


@router_dept.put("/user/{user_id}/dept")
async def set_user_dept(
    user_id: UUID,
    body: UserDeptRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:dept:assign")),
):
    """改一个用户的所属部门。一个用户一个部门，不是多对多。

    两道范围守卫：被挪的人要在自己范围内，**目的地也要**。少了后一道，
    部门管理员能把自己部门的人挪去范围外的部门 —— 人就"凭空消失"了。
    """
    dept_repo = _dept_repo(request)
    target = await _user_repo(request).get_by_id(user_id)
    if target is None:
        raise HTTPException(
            404, detail={"error": "USER_NOT_FOUND", "message": "用户不存在"}
        )
    if await dept_repo.get(body.dept_id) is None:
        raise HTTPException(
            400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )

    caller_scope, caller_dept_id = await load_user_scope_ctx(db, caller_id)
    await _assert_user_in_scope(
        db, caller_id, caller_scope, caller_dept_id, user_id, target.dept_id,
    )
    await _assert_dept_in_scope(db, caller_scope, caller_dept_id, body.dept_id)

    await dept_repo.set_user_dept(user_id, body.dept_id)
    # 换了子树，DEPT 视野随之变 —— 必须写穿（doc 19-5.1）
    invalidate(user_id)
    await audit_log(
        db,
        action="admin.user_dept_changed",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id), "dept_id": body.dept_id},
    )
    await db.commit()
    return {"updated": True}


@router_dept.put("/user/{user_id}/roles")
async def set_user_roles(
    user_id: UUID,
    body: UserRolesRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:role:assign")),
):
    """整集替换一个用户的角色（doc 19-2.2 的 `sys_user_role`，多对多）。

    校验都是"拒掉"而不是静默改数据（同删部门那几条守卫）：目标用户要在自己
    数据范围内（doc 19-5.3）、不能授出比自己宽的角色、空集会让这个人权限全空、
    把自己手里的全量角色**摘光**就是把自己锁在门外。
    """
    user = await _user_repo(request).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            404, detail={"error": "USER_NOT_FOUND", "message": "用户不存在"}
        )

    caller_scope, caller_dept_id = await load_user_scope_ctx(db, caller_id)
    await _assert_user_in_scope(
        db, caller_id, caller_scope, caller_dept_id, user_id, user.dept_id,
    )

    wanted = sorted(set(body.role_ids))
    # 空集与空角色 id 分开报：前者是业务规则，后者是传错了
    if not wanted:
        raise HTTPException(
            400, detail={"error": "ROLE_REQUIRED", "message": "用户至少需要一个角色"}
        )
    known = await _role_repo(request).list_ids()
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise HTTPException(
            400, detail={"error": "UNKNOWN_ROLE", "message": f"角色不存在：{unknown}"}
        )

    user_role_repo = _user_role_repo(request)
    held = await user_role_repo.list_role_ids(user_id)

    # 不能授出比自己宽的角色 —— 否则 dept_admin 给自己挂个 admin 就是超管。
    # 只看**新增**的那些：对方本来就持有的宽角色被原样回传，不算提权，别拦。
    if caller_scope != "ALL":
        scopes = await _role_repo(request).scopes_by_ids(wanted)
        caller_rank = scope_rank(caller_scope)
        added = set(wanted) - set(held)
        if any(scope_rank(scopes.get(rid, "SELF")) > caller_rank for rid in added):
            raise HTTPException(
                400,
                detail={
                    "error": "ROLE_SCOPE_EXCEEDED",
                    "message": "不能授予超出自己数据范围的角色",
                },
            )

    if user_id == caller_id:
        # 把自己手里的全量角色**全摘掉**就是把自己锁在门外，只能改库救回来。
        # 降一档（超管 → 管理员）是正当操作：判据是"新的集合里还有没有全量角色"，
        # 不是"有没有保住原来那一个"。
        role_repo = _role_repo(request)
        all_ids: set[int] = set()
        for key in sorted(ALL_PERMS_ROLE_KEYS):
            row = await role_repo.get_by_key(key)
            if row is not None:
                all_ids.add(row.id)
        if (set(held) & all_ids) and not (set(wanted) & all_ids):
            raise HTTPException(
                400,
                detail={
                    "error": "CANNOT_REMOVE_SELF_ALL_PERMS",
                    "message": "不能摘掉自己全部的管理员角色",
                },
            )

    count = await user_role_repo.replace(user_id, wanted)
    # 权限点与数据范围按 user_id 缓存（doc 19-5.1），改完要写穿。
    # 这里知道改的是谁，所以用 invalidate 而不是 clear_all。
    invalidate(user_id)
    await audit_log(
        db,
        action="admin.user_role_changed",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id), "role_ids": wanted},
    )
    await db.commit()
    return {"updated": True, "count": count}


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


async def _assert_user_in_scope(
    db,
    caller_id: UUID,
    caller_scope: str,
    caller_dept_id: int | None,
    target_id: UUID,
    target_dept_id: int | None,
) -> None:
    """目标用户是否落在调用者的数据范围内，不在就 404（doc 19-5.3）。

    用 404 不用 403，和 `deps.require_session_access` 同口径：403 等于告诉对方
    「这人存在，只是你看不了」，可以拿来枚举。
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


async def _assert_dept_in_scope(
    db, caller_scope: str, caller_dept_id: int | None, dept_id: int,
) -> None:
    """目标部门是否落在调用者的数据范围内，不在就按「部门不存在」报。

    没有这一道，部门管理员能把自己部门的人**挪出去** —— 被操作的人在自己范围内，
    目的地却在范围外，读侧看不见、写侧却动得了。
    报 `DEPT_NOT_FOUND` 而不是另起一个码：范围外的部门和真不存在的部门
    对外应当无法区分（同上，防枚举）。
    """
    if caller_scope == "ALL":
        return
    if caller_scope == "DEPT" and dept_id in await visible_dept_ids(db, caller_dept_id):
        return
    raise HTTPException(
        400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
    )
