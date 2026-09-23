"""RBAC 的读侧与启动期对账（doc 19-5.1 / 19-4.4）。

读侧只回答两个问题：这个人**有哪些权限点**、**能看到哪些数据行**。
写侧（角色授权）在 `server/api/system_routes.py`。
"""

from __future__ import annotations

import logging
import re
from typing import Iterator
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.authz import cache
from server.authz.catalog import (
    ALL_PERMS_ROLE_KEYS, PERMISSIONS, all_perms, module_of, perm_apis,
    planned_perms, walk,
)
from server.db.models import (
    OrmDept, OrmPermission, OrmPermissionApi, OrmRole, OrmRolePermission, OrmUser,
    OrmUserRole,
)

logger = logging.getLogger("iwork.authz")

_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")

SCOPE_ORDER = ("SELF", "DEPT", "ALL")
"""数据范围由窄到宽。合并取最宽、写侧守卫比宽窄，都靠这一个序（doc 19-5.3）。"""


def scope_rank(scope: str) -> int:
    """取宽度序号。**清单外的取值一律按最窄算**（fail-closed）——
    写错一个 scope 不能变成放开全量，同 `UserRepo.list_visible` 的兜底。"""
    try:
        return SCOPE_ORDER.index(scope)
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════════
# 读侧：权限点与数据范围
# ═══════════════════════════════════════════════════════════════

async def load_user_authz(
    db: AsyncSession, user_id: UUID,
) -> tuple[frozenset[str], str, int | None]:
    """取 (权限点集合, 数据范围, 所属部门)，走缓存。

    权限点与数据范围一起取 —— 后端每请求都要查一次，两次查询没有意义（doc 19-5.1）。
    部门也一起：`DEPT` 档要在主体自己的部门子树上过滤（doc 19-5.3）。
    """
    cached = cache.get(user_id)
    if cached is not None:
        return cached
    perms, scope, dept_id = await _query_user_authz(db, user_id)
    cache.put(user_id, perms, scope, dept_id)
    return frozenset(perms), scope, dept_id


async def load_user_permissions(db: AsyncSession, user_id: UUID) -> frozenset[str]:
    perms, _, _ = await load_user_authz(db, user_id)
    return perms


async def load_user_scope(db: AsyncSession, user_id: UUID) -> str:
    _, scope, _ = await load_user_authz(db, user_id)
    return scope


async def load_user_scope_ctx(db: AsyncSession, user_id: UUID) -> tuple[str, int | None]:
    """数据范围 + 主体所属部门。需要按部门裁剪数据的调用方用这个。

    只要 scope 的仍用 `load_user_scope` —— 别让「取一个没用的字段」变成习惯。
    """
    _, scope, dept_id = await load_user_authz(db, user_id)
    return scope, dept_id


async def visible_dept_ids(db: AsyncSession, dept_id: int | None) -> list[int]:
    """`dept_id` 自己 + 它的全部后代。`dept_id` 为空则返回空列表。

    `DEPT` 档的语义就是这一棵子树（doc 19-5.3）。部门表只有几行，全量取出来在
    Python 里走 BFS，不写递归 CTE —— 与 `dept_routes` 里沿 parent_id 往上走同一个路子。
    """
    if dept_id is None:
        return []
    rows = (await db.execute(select(OrmDept.id, OrmDept.parent_id))).all()
    children: dict[int, list[int]] = {}
    for id_, parent_id in rows:
        children.setdefault(parent_id, []).append(id_)

    out: list[int] = []
    seen: set[int] = set()
    queue = [dept_id]
    while queue:
        current = queue.pop()
        if current in seen:
            # 写入侧已经拦了成环（dept_routes 的 DEPT_CYCLE），这里只是不为此死循环
            continue
        seen.add(current)
        out.append(current)
        queue.extend(children.get(current, []))
    return out


async def _query_user_authz(
    db: AsyncSession, user_id: UUID,
) -> tuple[set[str], str, int | None]:
    rows = (await db.execute(
        select(OrmRole.id, OrmRole.role_key, OrmRole.data_scope)
        .join(OrmUserRole, OrmUserRole.role_id == OrmRole.id)
        .where(OrmUserRole.user_id == user_id, OrmRole.status == 1)
    )).all()

    dept_id = (await db.execute(
        select(OrmUser.dept_id).where(OrmUser.id == user_id)
    )).scalar_one_or_none()

    # 多角色取最宽的那档：ALL > DEPT > SELF（doc 19-5.1 / 19-5.3）
    scope = max((r.data_scope for r in rows), key=scope_rank, default="SELF")

    # 全量角色短路成全集：不这么做，管理员哪天把自己的权限删了就再也进不去管理界面。
    # 名单见 catalog.ALL_PERMS_ROLE_KEYS —— 短路只影响权限点，数据范围仍取角色行的值。
    if any(r.role_key in ALL_PERMS_ROLE_KEYS for r in rows):
        return set(all_perms()), scope, dept_id

    role_ids = [r.id for r in rows]
    if not role_ids:
        return set(), scope, dept_id

    # 多角色并集去重；只算 status=1 的角色与权限点，任一被禁用即不参与
    granted = (await db.execute(
        select(OrmPermission.perms)
        .join(OrmRolePermission, OrmRolePermission.permission_id == OrmPermission.id)
        .where(
            OrmRolePermission.role_id.in_(role_ids),
            OrmPermission.status == 1,
            OrmPermission.perms.is_not(None),
        )
    )).scalars()
    return {p for p in granted if p}, scope, dept_id


# ═══════════════════════════════════════════════════════════════
# 启动期 1：字典表对账
# ═══════════════════════════════════════════════════════════════

async def sync_catalog(db: AsyncSession) -> None:
    """按 perms 增改、把代码里已经没有的下线（doc 19-4.2）。

    **每次启动都跑，不是"表空才插一次"**：否则每新增一个权限点库里都落后一步，
    校验时查不到，就是 403。业务表（角色、授权）不走这里，见 `db/seed.py`。
    """
    if not PERMISSIONS:
        raise RuntimeError("权限清单为空，拒绝对账（防止误清空 sys_permission）")

    keep_ids: list[int] = []

    async def _upsert(nodes: tuple[dict, ...], parent_id: int) -> None:
        for order, node in enumerate(nodes, start=1):
            perms = node["key"]
            if perms is None:
                # 目录行没有权限标识，只能按「父级 + 名字 + 类型」认行（doc 19-2.2）
                stmt = select(OrmPermission).where(
                    OrmPermission.parent_id == parent_id,
                    OrmPermission.permission_name == node["name"],
                    OrmPermission.permission_type == node["type"],
                )
            else:
                stmt = select(OrmPermission).where(OrmPermission.perms == perms)

            row = (await db.execute(stmt)).scalars().first()
            if row is None:
                row = OrmPermission(perms=perms)
                db.add(row)
            row.parent_id = parent_id
            row.permission_name = node["name"]
            row.permission_type = node["type"]
            row.path = node.get("path", "")
            row.component = node.get("component", "")
            row.order_num = order
            await db.flush()  # 拿自增 id，下面子节点要当 parent_id 用

            keep_ids.append(row.id)
            await _replace_apis(db, row.id, node.get("apis", ()))
            await _upsert(node.get("children", ()), row.id)

    await _upsert(PERMISSIONS, 0)

    # 下线代码里已经没有的行；授权行随 FK CASCADE 一起消失
    removed = await db.execute(
        delete(OrmPermission).where(OrmPermission.id.notin_(keep_ids))
    )
    if removed.rowcount:
        logger.warning("authz.sync_catalog  offline=%d 行已下线", removed.rowcount)
    await db.commit()


async def _replace_apis(db: AsyncSession, permission_id: int, apis: tuple) -> None:
    await db.execute(
        delete(OrmPermissionApi).where(OrmPermissionApi.permission_id == permission_id)
    )
    for method, path in apis:
        db.add(OrmPermissionApi(permission_id=permission_id, method=method, path=path))


# ═══════════════════════════════════════════════════════════════
# 启动期 2：路由引用校验
# ═══════════════════════════════════════════════════════════════

def normalize_api_path(path: str) -> str:
    """两边折成同一种形式再比，否则永远对不上、全是假告警（doc 19-4.4）。

    * 剥 `/api`：契约表写客户端视角的 `/api/...`，服务端路由没有这个前缀。
    * 占位符统一：契约表写 `{id}`，FastAPI 里常是 `{role_id}`，两边都折成 `{}`。
    """
    if path == "/api":
        path = "/"
    elif path.startswith("/api/"):
        path = path[len("/api"):]
    return _PLACEHOLDER_RE.sub("{}", path)


def collect_route_refs(app) -> dict[tuple[str, str, str], None]:
    """扫 app.routes，取出每条接口声明的 (perms, method, 归一化路径)。"""
    refs: dict[tuple[str, str, str], None] = {}
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        perms_list = list(_iter_perm_calls(dependant))
        if not perms_list:
            continue
        methods = sorted(set(getattr(route, "methods", None) or ()) - {"HEAD", "OPTIONS"})
        if not methods:
            continue
        path = normalize_api_path(getattr(route, "path", ""))
        for perms in perms_list:
            for method in methods:
                refs[(perms, method, path)] = None
    return refs


def _iter_perm_calls(dependant) -> Iterator[str]:
    """递归找依赖树里由 require_permission 造出来的闭包，读它挂的 `_perms`。"""
    for sub in getattr(dependant, "dependencies", None) or ():
        perms = getattr(getattr(sub, "call", None), "_perms", None)
        if perms:
            yield from perms
        yield from _iter_perm_calls(sub)


def verify_route_refs(app) -> None:
    """路由引用的 perms 必须在清单里；否则抛错、不启动（doc 19-4.4）。

    方向只有一个：清单是权威，路由不许引用清单外的点（拼错字、用了已下线的点）。
    反向的"清单有、前端 `ENTRY_VIEWS` 漏配"由前端比对脚本拦，不在服务端职责内。
    """
    refs = collect_route_refs(app)
    referenced = {perms for perms, _, _ in refs}
    known = set(all_perms())

    unknown = sorted(referenced - known)
    if unknown:
        raise RuntimeError(
            "路由引用了权限清单里没有的权限点：" + "、".join(unknown)
            + f"（清单见 server/authz/catalog.py 的 PERMISSIONS，共 {len(known)} 条）"
        )

    # 清单有、但没有任何路由引用：client:* 是纯前端入口，天然没有 API；
    # planned 的是接口还没写的（doc 19-4.2 的"待建"）。两类都不告警。
    orphan = sorted(
        p for p in known
        if p not in referenced and module_of(p) != "client" and p not in planned_perms()
    )
    if orphan:
        logger.warning("authz.verify_route_refs  清单有、无人引用：%s", "、".join(orphan))

    # 接口绑定漂移：告警而非抛错 —— 占位符名字与路径本来就允许两边写法不同。
    # 两边都先收敛口径，只比"清单声明了接口绑定"的那些点：
    #   * planned 的接口还没写，声明了但没有路由是预期内的；
    #   * 纯前端入口（client:*）不声明绑定，路由额外接受它是本设计的 OR 闸门用法
    #     （如 GET /mcp/hub 同时认 system:mcp:list 与 client:mcp:config），不是漂移。
    bound_perms = set(perm_apis())
    declared = {
        (perms, method, normalize_api_path(path))
        for perms, apis in perm_apis().items()
        for method, path in apis
        if perms not in planned_perms()
    }
    refs_bound = {r for r in refs if r[0] in bound_perms}
    drift = sorted(declared.symmetric_difference(refs_bound))
    if drift:
        logger.warning(
            "authz.verify_route_refs  接口绑定与清单不一致（%d 处）：%s",
            len(drift), "、".join(f"{p} {m} {path}" for p, m, path in drift),
        )


def catalog_summary() -> dict:
    """启动日志用的一行摘要。"""
    nodes = [n for n, _ in walk()]
    return {
        "total": len(nodes),
        "perms": len(all_perms()),
        "dirs": sum(1 for n in nodes if n["type"] == "M"),
        "menus": sum(1 for n in nodes if n["type"] == "C"),
        "buttons": sum(1 for n in nodes if n["type"] == "F"),
    }
