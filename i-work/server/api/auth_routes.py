"""认证接口（docs/chapters/18-登录认证模块.md 第 11 章）。

字段命名一律 snake_case，与现有业务接口一致。
响应体统一 `{"error": "<码>", "message": "<人话>"}`，错误码见 11.4。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.api.deps import get_auth_service, get_current_user
from server.auth.service import AuthError, AuthService, serialize_user

router_auth = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(..., description="3–32 位小写字母/数字/下划线/短横线")
    password: str


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


@router_auth.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
):
    """开放注册，只需 username + password。

    不返回 token —— 注册完还要显式登录一次，省掉"注册即登录"这条隐式路径。
    """
    try:
        user = await service.register(body.username, body.password)
    except AuthError as exc:
        _fail(exc)
    return {"user": serialize_user(user)}


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
):
    """查库，不用 token 里的旧快照。"""
    try:
        user = await service.get_user(user_id)
    except AuthError as exc:
        _fail(exc)
    return {"user": serialize_user(user)}


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
