"""用户管理面接口（docs/chapters/19-权限管理RBAC.md §4.2 第 2–7 行）。

从 `dept_routes.py` 整体搬过来 —— 那个文件头原来就写着「等其余四个点真做了，这几个
接口该跟着搬过去」。现在用户管理页齐了，这一面自己立一个模块：部门面只管组织架构。

一页六件事：列表、建号、改显示名、重置密码、停用/启用、解锁。其中「建号」「改部门」
「换角色」三件在部门配置页也有控件，**两页共用同一批权限点与同一支 API**（见
`catalog.py` 里 `system:dept:addUser` / `system:dept:assign` / `system:role:assign` 旁边的
注释）—— 这里只有一份实现，两页各挂各的按钮，同一件事不开两个点。

写入一律 `audit_log` + `await db.commit()`：`audit_log` 只 execute 不 commit。

数据范围（doc 19-5.3）在这个文件里的落点，改任何一处都要想想另外几处：
1. 读侧：`GET /list` 按主体的 `ALL / DEPT / SELF` 裁剪。
2. 写侧：动任何一个用户之前 `assert_user_in_scope`；挪人时**目的地**还要 `assert_dept_in_scope`。
3. 写侧：换角色不能授出比自己宽的角色（`scope_rank` 比宽窄）。
4. 写侧：建号时目标部门要在自己范围内 —— 这条就是"超管任意、部门管理员只在本部门及下级"
   的全部实现。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.api.deps import (
    assert_permission, get_auth_service, get_current_user, get_db, require_permission,
)
from server.api.scope_guards import assert_dept_in_scope, assert_user_in_scope
from server.auth.security import PasswordPolicyError, hash_password
from server.auth.service import AuthError
from server.authz.cache import invalidate
from server.authz.catalog import ALL_PERMS_ROLE_KEYS
from server.authz.service import load_user_scope_ctx, scope_rank, visible_dept_ids
from server.observability.audit import audit_log
from server.storage.postgres import DeptRepo, RoleRepo, UserRepo, UserRoleRepo

router_user = APIRouter(prefix="/system/user", tags=["system"])

VALID_STATUS = ("active", "disabled")


class UserCreateRequest(BaseModel):
    """`role_ids` 缺省 = 挂默认角色；给了（非空）就用它，权限点是 `system:role:assign`。

    两种"空"分开：`None`（没这个字段）走默认角色，`[]` 当传错报 `ROLE_REQUIRED`。
    """
    username: str
    password: str
    dept_id: int
    role_ids: list[int] | None = None


class UserUpdateRequest(BaseModel):
    """只改显示名。用户名是登录标识、也是 `login_logs` 的审计线索，不给改。"""
    display_name: str


class UserDeptRequest(BaseModel):
    dept_id: int


class UserRolesRequest(BaseModel):
    """整集替换，不是增量 —— 与 `GrantRequest`（system_routes.py）同语义。"""
    role_ids: list[int]


class UserPasswordRequest(BaseModel):
    password: str


class UserStatusRequest(BaseModel):
    """`active` / `disabled` 两档，仅管理员态；到期自解的自动锁定走 `locked_until`
    （doc 18-3.3 的字段分工），所以解锁是另一支接口、另一个点。"""
    status: str


def _dept_repo(request: Request) -> DeptRepo:
    return DeptRepo(request.app.state.db_session_factory)


def _user_repo(request: Request) -> UserRepo:
    return UserRepo(request.app.state.db_session_factory)


def _user_role_repo(request: Request) -> UserRoleRepo:
    return UserRoleRepo(request.app.state.db_session_factory)


def _role_repo(request: Request) -> RoleRepo:
    return RoleRepo(request.app.state.db_session_factory)


async def _target_in_scope(
    request: Request, db, caller_id: UUID, user_id: UUID,
) -> tuple[object, str, int | None]:
    """取出目标用户并确认它落在调用者的数据范围内。

    六条写接口的第一步都是这四行，抄六遍迟早有一条忘了 —— 忘了那条就是"知道 id 就能
    改别人的号"。返回调用者的 scope / dept_id 一并捎出来，挪人那条还要用它们判目的地。
    """
    target = await _user_repo(request).get_by_id(user_id)
    if target is None:
        raise HTTPException(
            404, detail={"error": "USER_NOT_FOUND", "message": "用户不存在"}
        )
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, caller_id)
    await assert_user_in_scope(
        db, caller_id, caller_scope, caller_dept_id, user_id, target.dept_id,
    )
    return target, caller_scope, caller_dept_id


async def _assert_roles_grantable(
    request: Request, caller_scope: str, wanted: list[int], held: set[int],
) -> None:
    """角色集合的两条判据：id 都认识、且不授出比自己数据范围更宽的角色（doc 19-5.3）。

    建号时带角色与事后换角色共用这一段 —— 两处都是"整集替换"的写入口，
    判据必须一致，抄两遍迟早一处松一处紧。

    `held` 是对方**现有**的角色：只看新增的那些，对方本来就持有的宽角色被原样回传
    不算提权，别拦。建号时手里还没有角色，传 `set()`。
    """
    unknown = sorted(set(wanted) - await _role_repo(request).list_ids())
    if unknown:
        raise HTTPException(
            400, detail={"error": "UNKNOWN_ROLE", "message": f"角色不存在：{unknown}"}
        )
    if caller_scope == "ALL":
        return
    # 不能授出比自己宽的角色 —— 否则 dept_admin 给自己挂个 admin 就是超管。
    scopes = await _role_repo(request).scopes_by_ids(wanted)
    caller_rank = scope_rank(caller_scope)
    added = set(wanted) - held
    if any(scope_rank(scopes.get(rid, "SELF")) > caller_rank for rid in added):
        raise HTTPException(
            400,
            detail={
                "error": "ROLE_SCOPE_EXCEEDED",
                "message": "不能授予超出自己数据范围的角色",
            },
        )


# ═══════════════════════════════════════════════════════════════
# 读
# ═══════════════════════════════════════════════════════════════

@router_user.get("/list")
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

    `locked_until` 给用户管理页用：它据此决定「解锁」那个按钮出不出来。
    只读侧要不要看到锁定期，本来就是管理员排障最常问的一句。
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
                "locked_until": u.locked_until.isoformat() if u.locked_until else None,
                "role_ids": roles_by_user.get(u.id, []),
            }
            for u in users
        ]
    }


# ═══════════════════════════════════════════════════════════════
# 写
# ═══════════════════════════════════════════════════════════════

@router_user.post("")
async def create_user(
    body: UserCreateRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    # 两个点任一即可：`system:dept:addUser` 是部门页那个「添加用户」，
    # `system:user:add` 是用户管理页的「新增」——建号本就是同一件事（doc 19-4.2）。
    _: None = Depends(require_permission("system:user:add", "system:dept:addUser")),
):
    """管理员建号，密码由建号的人转交给本人（doc 19-4.2）。

    开放注册已下线 —— 谁都能刷账号、注册完还自动落进默认部门，而组织架构本该
    是管理员在管的。角色可以在这一步就挂上（body 带 `role_ids`），不带就还是默认角色。

    范围判据就是「目标部门在自己子树内」：超管 `ALL` 短路放行、部门管理员只在本
    部门及下级（`assert_dept_in_scope`）。范围外报 400 `DEPT_NOT_FOUND`。

    **校验全在写之前**：角色越权（`ROLE_SCOPE_EXCEEDED`）或 id 不认识（`UNKNOWN_ROLE`）
    时一个用户都不会建出来 —— 建号是"一次成型"，不该留个半成品让人去收拾。
    """
    caller_scope, caller_dept_id = await load_user_scope_ctx(db, caller_id)

    # 角色这道闸进不了依赖：那一支是「add / addUser 任一即可」，把 `system:role:assign`
    # 加进去就等于"只有换角色权限的人也能建号"。所以按 body 分流，在这里补判（doc 19-4.2 第 36 行）。
    wanted: list[int] | None = None
    if body.role_ids is not None:
        await assert_permission(db, caller_id, "system:role:assign")
        if not body.role_ids:
            raise HTTPException(
                400, detail={"error": "ROLE_REQUIRED", "message": "用户至少需要一个角色"}
            )
        wanted = sorted(set(body.role_ids))
        # 建号时手里还没有角色，新增集就是全集
        await _assert_roles_grantable(request, caller_scope, wanted, held=set())

    if await _dept_repo(request).get(body.dept_id) is None:
        raise HTTPException(
            400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, body.dept_id)

    try:
        user = await get_auth_service(request).create_user(
            body.username, body.password, body.dept_id, role_ids=wanted
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
        detail={
            "username": user.username,
            "dept_id": body.dept_id,
            "role_ids": wanted,
        },
    )
    await db.commit()
    return {"id": str(user.id), "username": user.username}


@router_user.put("/{user_id}")
async def update_user(
    user_id: UUID,
    body: UserUpdateRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:edit")),
):
    """改显示名。空串按错报，不静默写空 —— 列表那一列就靠它认人。"""
    name = body.display_name.strip()
    if not name:
        raise HTTPException(
            400,
            detail={"error": "INVALID_DISPLAY_NAME", "message": "显示名不能为空"},
        )
    await _target_in_scope(request, db, caller_id, user_id)

    await _user_repo(request).update_display_name(user_id, name)
    await audit_log(
        db,
        action="admin.user_updated",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id), "display_name": name},
    )
    await db.commit()
    return {"updated": True}


@router_user.put("/{user_id}/dept")
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
    _, caller_scope, caller_dept_id = await _target_in_scope(
        request, db, caller_id, user_id,
    )
    if await dept_repo.get(body.dept_id) is None:
        raise HTTPException(
            400, detail={"error": "DEPT_NOT_FOUND", "message": "部门不存在"}
        )
    await assert_dept_in_scope(db, caller_scope, caller_dept_id, body.dept_id)

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


@router_user.put("/{user_id}/roles")
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
    _, caller_scope, _caller_dept_id = await _target_in_scope(
        request, db, caller_id, user_id,
    )

    wanted = sorted(set(body.role_ids))
    # 空集与空角色 id 分开报：前者是业务规则，后者是传错了
    if not wanted:
        raise HTTPException(
            400, detail={"error": "ROLE_REQUIRED", "message": "用户至少需要一个角色"}
        )

    user_role_repo = _user_role_repo(request)
    held = await user_role_repo.list_role_ids(user_id)
    await _assert_roles_grantable(request, caller_scope, wanted, held=set(held))

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


@router_user.put("/{user_id}/password")
async def reset_user_password(
    user_id: UUID,
    body: UserPasswordRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:resetPwd")),
):
    """管理员重置密码（doc 18-3.5）。密码由管理员当面转交本人。

    三件事落在同一次提交里，缺一件这次重置就是假的：

    * 换 `password_hash`；
    * 清 `failed_login_count` / `locked_until` —— 重设密码就是一次干净的开始，
      与登录成功清锁（doc 18-3.3）同口径，否则新密码第一次登录还撞在锁定期上；
    * 吊销他手上全部 refresh token，**不重签**：管理员不是那个人，新会话该由他
      自己登录取。不吊销的话旧会话照用，改密等于没改。
    """
    await _target_in_scope(request, db, caller_id, user_id)

    try:
        new_hash = hash_password(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            400, detail={"error": "INVALID_REQUEST", "message": str(exc)}
        )

    await _user_repo(request).set_password_hash(user_id, new_hash)
    await get_auth_service(request).revoke_all_tokens(db, user_id)
    await audit_log(
        db,
        action="admin.user_password_reset",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id)},
    )
    await db.commit()
    return {"updated": True}


@router_user.put("/{user_id}/status")
async def set_user_status(
    user_id: UUID,
    body: UserStatusRequest,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:status")),
):
    """停用 / 启用一个账号（doc 18-3.4 的 `ACCOUNT_DISABLED`）。

    停用是"封号"的唯一实现：登录侧只看 `status != 'active'`，而**删除**一个用户会
    连带清掉 12 张表（`users.id` 的 11 个 CASCADE + `login_logs` 的 SET NULL），
    所以这一档不给删除。

    两条守卫：
    * 取值只能是两档，别的值报 400（写进去就是谁都登不上的僵尸态）；
    * **不能停用自己** —— 同换角色那条「不能摘掉自己全部的管理员角色」，都是防把自己
      锁在门外，只能改库救回来；
    * 停用顺手吊销那个人的 refresh token：不吊销，旧 token 还能用满 15 分钟
      （`get_current_user` 不查库，doc 8.5），停用就只是装饰。
    """
    if body.status not in VALID_STATUS:
        raise HTTPException(
            400,
            detail={
                "error": "INVALID_STATUS",
                "message": "状态只能是 active 或 disabled",
            },
        )
    if body.status == "disabled" and user_id == caller_id:
        raise HTTPException(
            400,
            detail={"error": "CANNOT_DISABLE_SELF", "message": "不能停用自己"},
        )
    await _target_in_scope(request, db, caller_id, user_id)

    await _user_repo(request).set_status(user_id, body.status)
    if body.status == "disabled":
        await get_auth_service(request).revoke_all_tokens(db, user_id)
    await audit_log(
        db,
        action="admin.user_status_changed",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id), "status": body.status},
    )
    await db.commit()
    return {"updated": True}


@router_user.put("/{user_id}/unlock")
async def unlock_user(
    user_id: UUID,
    request: Request,
    caller_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:user:unlock")),
):
    """解锁：清失败计数与锁定期（doc 18-3.3）。

    这是"人工解除"的那个出口 —— 没有它，连续错密码锁上 15 分钟之间谁都没办法，
    只能干等。幂等：没锁着也返回成功，管理员不必先去看他锁没锁。
    """
    await _target_in_scope(request, db, caller_id, user_id)

    await _user_repo(request).clear_lock(user_id)
    await audit_log(
        db,
        action="admin.user_unlocked",
        user_id=caller_id,
        resource=f"user/{user_id}",
        detail={"user_id": str(user_id)},
    )
    await db.commit()
    return {"updated": True}
