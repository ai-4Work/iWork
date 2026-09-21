from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.service import AuthError, AuthService


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
    返回值是 UUID，与 get_default_user_id 同签名，所以 28 处调用点只需换依赖名。
    """
    service = get_auth_service(request)
    token = _bearer_token(request)
    try:
        return service.decode_access_token(token)
    except AuthError as exc:
        raise HTTPException(
            exc.status, detail={"error": exc.code, "message": exc.message}
        )


def get_engine_manager(request: Request):
    """FastAPI 依赖注入：从 app state 获取 EngineManager。"""
    return request.app.state.engine_manager
