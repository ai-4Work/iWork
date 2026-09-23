"""认证接口（docs/chapters/18-登录认证模块.md 第 11 章）。

字段命名一律 snake_case，与现有业务接口一致。
响应体统一 `{"error": "<码>", "message": "<人话>"}`，错误码见 11.4。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.api.deps import get_auth_service, get_current_user, get_db
from server.auth.service import AuthError, AuthService, serialize_user
from server.authz.service import load_user_permissions

router_auth = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


def _fail(exc: AuthError):
    raise HTTPException(
        exc.status, detail={"error": exc.code, "message": exc.message}
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router_auth.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
):
    try:
        return await service.login(
            body.username,
            body.password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        _fail(exc)


@router_auth.post("/refresh")
async def refresh(
    body: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
):
    """用 refresh token 本身认证，不需要 access token（access 这时通常是过期的）。"""
    try:
        return await service.refresh(body.refresh_token)
    except AuthError as exc:
        _fail(exc)


@router_auth.post("/logout")
async def logout(
    body: LogoutRequest,
    service: AuthService = Depends(get_auth_service),
):
    """幂等：token 已失效也返回 200，客户端登出路径不该因为清理失败而报错。"""
    await service.logout(body.refresh_token)
    return {"ok": True}


@router_auth.post("/logout-all")
async def logout_all(
    user_id: UUID = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """吊销该用户全部 refresh token —— 当前客户端也在其中。"""
    await service.logout_all(user_id)
    return {"ok": True}


@router_auth.get("/me")
async def me(
    user_id: UUID = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
):
    """查库，不用 token 里的旧快照。

    顺带下发 `permissions`（doc 19-5.4）：客户端 `authStore` 已经在调本接口取用户，
    权限搭这个响应回去最省事。

    **只下发权限点，不下发菜单树或入口清单** —— 前端自己持有 `perms → 组件` 映射，
    下发的数组只用来查那张表（doc 19-6.4）。权限也不塞进 token：被改后要等 token
    过期才生效，而扩在这里是每次开客户端就重新拉。
    """
    try:
        user = await service.get_user(user_id)
    except AuthError as exc:
        _fail(exc)
    permissions = await load_user_permissions(db, user_id)
    return {
        "user": serialize_user(user),
        "permissions": sorted(permissions),
    }


@router_auth.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user_id: UUID = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
):
    """只改密码，不改 username。

    改密后全部 refresh token 被吊销（含当前会话），所以这里直接返回新的一对
    token —— 否则客户端改完密码立刻被自己的操作踢下线。
    """
    try:
        return await service.change_password(
            user_id, body.old_password, body.new_password
        )
    except AuthError as exc:
        _fail(exc)
