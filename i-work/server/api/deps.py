from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.service import AuthError, AuthService
from server.authz.service import load_user_permissions, load_user_scope
from server.storage.postgres import PgSessionRepo


async def get_db(request: Request) -> AsyncSession:
    """Yield 一个 async DB session，供 request handler 使用。"""
    factory = request.app.state.db_session_factory
    async with factory() as db:
        yield db


def get_auth_service(request: Request) -> AuthService:
    """FastAPI 依赖注入：从 app state 获取 AuthService。"""
    return request.app.state.auth_service


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            401,
            detail={"error": "TOKEN_INVALID", "message": "缺少 access token"},
        )
    return token.strip()


def get_current_user(request: Request) -> UUID:
    """认证前置：验签 + 检查过期，注入用户身份。

    **不查库** —— 代价是封号后旧 token 最长 15 分钟内仍可用（doc 8.5）。
    """
    service = get_auth_service(request)
    token = _bearer_token(request)
    try:
        return service.decode_access_token(token)
    except AuthError as exc:
        raise HTTPException(
            exc.status, detail={"error": exc.code, "message": exc.message}
        )


def require_permission(*perms: str):
    """鉴权前置：要求当前用户至少拥有其中一个权限点（doc 19-5.1）。

    写成 `Depends` 依赖而不是装饰器 —— 项目里所有 handler 都是依赖注入式签名，
    装饰器拿不到 Request 和 DB session，硬插进来就是两套鉴权写法并存。

    用法：`_: None = Depends(require_permission("system:user:add"))`
    传多个权限点表示**任一满足**。
    """
    async def _check(
        user_id: UUID = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        owned = await load_user_permissions(db, user_id)
        if not any(p in owned for p in perms):
            raise HTTPException(
                403,
                detail={"error": "PERMISSION_DENIED", "message": "权限不足"},
            )

    # 启动对账要能取回声明的 perms（doc 19-4.4 的 verify_route_refs）。
    # 闭包本身取不出实参，只能挂一个属性。
    _check._perms = perms
    return _check


def get_engine_manager(request: Request):
    """FastAPI 依赖注入：从 app state 获取 EngineManager。"""
    return request.app.state.engine_manager


async def require_session_access(
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """会话归属校验（doc 19-5.3）。

    读路径参数而非签名 —— `router` 的前缀是 `/sessions/{session_id}`，这些
    端点把 `session_id` 声明成路径参数，但跨 18 个 handler 传进来的写法不一致；
    直接取路径参数，一处改完覆盖全部。

    路径里没有 session_id 的端点（`GET /sessions` 列表）不走这里 —— 那个
    本来就按调用者过滤。

    数据范围 `ALL` 的角色可跨用户访问（管理员排障用）；其余一律按 404 报，
    不用 403 —— 403 会泄露"这个 id 存在"，等于给了枚举会话的口子。
    """
    raw = request.path_params.get("session_id")
    if not raw:
        return
    session = await PgSessionRepo(request.app.state.db_session_factory).get(raw)
    if session is None:
        raise HTTPException(
            404, detail={"error": "not_found", "message": "会话不存在"}
        )
    if str(session.user_id) == str(user_id):
        return
    if await load_user_scope(db, user_id) == "ALL":
        return
    raise HTTPException(
        404, detail={"error": "not_found", "message": "会话不存在"}
    )
