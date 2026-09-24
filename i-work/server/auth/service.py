"""登录认证业务：建号、登录、刷新、登出、改密、当前用户。

三处容易写错、都在这里收敛：

- **锁定**：先判锁定再验密码（锁定期内不白跑 bcrypt）；失败计数递增与置
  `locked_until` 必须是同一条 UPDATE；登录成功把两个字段一起清零。
- **轮换**：一次一换。旧 token 吊销后 30 秒内仍接受（客户端刷新响应丢失后会重试），
  超过窗口才判重放并吊销该用户全部 refresh token。
- **审计**：`login_logs` 只做审计，写入失败不能阻断登录。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Interval as SAInterval
from sqlalchemy import case, func, literal, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.auth.security import (
    AccessTokenError,
    PasswordPolicyError,
    create_access_token,
    decode_access_token as _decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

from server.authz.catalog import DEFAULT_ROLE_KEY
from server.config import settings
from server.db.models import (
    OrmLoginLog, OrmRefreshToken, OrmRole, OrmUser, OrmUserRole,
)

logger = logging.getLogger("iwork.auth")

_USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,32}$")

# 错误码 → HTTP 状态（doc 18-11.4）
_STATUS = {
    "INVALID_CREDENTIALS": 401,
    "USERNAME_TAKEN": 409,
    "ACCOUNT_LOCKED": 403,
    "ACCOUNT_DISABLED": 403,
    "TOKEN_EXPIRED": 401,
    "TOKEN_INVALID": 401,
    "REFRESH_TOKEN_INVALID": 401,
    "INVALID_REQUEST": 400,
}

_MESSAGES = {
    "INVALID_CREDENTIALS": "账号或密码错误",
    "USERNAME_TAKEN": "用户名已被占用",
    "ACCOUNT_LOCKED": "账号已锁定，请稍后再试",
    "ACCOUNT_DISABLED": "账号已被禁用",
    "TOKEN_EXPIRED": "登录已过期，请重新登录",
    "TOKEN_INVALID": "登录凭证无效，请重新登录",
    "REFRESH_TOKEN_INVALID": "登录已过期，请重新登录",
    "INVALID_REQUEST": "请求参数不合法",
}


class AuthError(Exception):
    """认证失败。code 与 HTTP 状态由 11.4 的错误码表定义。"""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.status = _STATUS.get(code, 400)
        self.message = message or _MESSAGES.get(code, code)
        super().__init__(self.message)


def normalize_username(username: str) -> str:
    """入库与登录查询都要过这里。

    PG 唯一索引大小写敏感：不规范化的 `Ai4work` 和 `ai4work` 会变成两个账号，
    且用户输入大写时永远登不进来。
    """
    return (username or "").strip().lower()


def _validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "INVALID_REQUEST",
            "用户名需为 3–32 位小写字母、数字、下划线或短横线",
        )


def serialize_user(user: OrmUser) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "status": user.status,
    }


async def _db_now(db: AsyncSession) -> datetime:
    """取数据库当前时间。

    锁定的时间比较一律以 DB 为准 —— 用应用进程时间会出隐蔽的
    「刚锁上就能登」或「永远解不开」。
    """
    return (await db.execute(select(func.now()))).scalar_one()


async def _assign_default_role(db: AsyncSession, user_id) -> None:
    """给新建账号挂上默认角色（doc 19-5.5）。

    不挂的话新账号的 `permissions` 是空的 —— 侧边栏一个入口都不显示。
    角色由种子建立；查不到只记一条告警、不让建号失败：
    建号不该因为 RBAC 还没初始化好而炸掉。
    """
    role_id = (await db.execute(
        select(OrmRole.id).where(OrmRole.role_key == DEFAULT_ROLE_KEY)
    )).scalar_one_or_none()
    if role_id is None:
        logger.warning("auth.create_user  默认角色 %s 不存在，跳过挂角色", DEFAULT_ROLE_KEY)
        return
    db.add(OrmUserRole(user_id=user_id, role_id=role_id))
    await db.commit()


class AuthService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    # ── 建号 ────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        password: str,
        dept_id: int,
        role_ids: list[int] | None = None,
    ) -> OrmUser:
        """管理员建号，开放注册已下线（doc 19-4.2）。

        部门由调用方定 —— 超管可任意、部门管理员限本人子树，那道范围守卫在
        `user_routes.py` 里，这里只负责把 `dept_id` 原样写进去。

        `role_ids` 给了就用它、**不**再挂默认角色（建号时直接选角色，省掉"先建后改"
        那一步）；不给就还是默认角色。角色的合法性（id 存在、不比自己宽）由调用方
        在进来之前判完 —— 这里只写。
        """
        username = normalize_username(username)
        _validate_username(username)
        try:
            password_hash = hash_password(password)
        except PasswordPolicyError as exc:
            raise AuthError("INVALID_REQUEST", str(exc))

        async with self._sf() as db:
            user = OrmUser(
                username=username,
                display_name=username,  # 显示名默认取用户名，用户之后可改
                password_hash=password_hash,
                status="active",
                dept_id=dept_id,
            )
            db.add(user)
            try:
                await db.commit()
            except IntegrityError:
                # 查重不能只靠「先 SELECT 再 INSERT」——并发下两个请求会同时通过，
                # 唯一索引是唯一可靠的闸门。
                await db.rollback()
                raise AuthError("USERNAME_TAKEN")
            await db.refresh(user)
            if role_ids:
                db.add_all([
                    OrmUserRole(user_id=user.id, role_id=rid)
                    for rid in sorted(set(role_ids))
                ])
                await db.commit()
            else:
                await _assign_default_role(db, user.id)
            logger.info("auth.create_user  username=%s dept_id=%s", username, dept_id)
            return user

    # ── 登录 ────────────────────────────────────────────

    async def login(
        self,
        username: str,
        password: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        username = normalize_username(username)

        async with self._sf() as db:
            now = await _db_now(db)
            result = await db.execute(
                select(OrmUser).where(OrmUser.username == username)
            )
            user = result.scalar_one_or_none()

            if user is None:
                # 照跑一次密码校验再报错，否则响应快慢能反推账号是否存在
                verify_password(password, None)
                await self._log_login(
                    username=username, user_id=None, result="fail",
                    reason="INVALID_CREDENTIALS", ip=ip, user_agent=user_agent,
                )
                raise AuthError("INVALID_CREDENTIALS")

            if user.status == "disabled":
                await self._log_login(
                    username=username, user_id=user.id, result="fail",
                    reason="ACCOUNT_DISABLED", ip=ip, user_agent=user_agent,
                )
                raise AuthError("ACCOUNT_DISABLED")

            # 先判锁定再验密码：锁定期内直接拒，不白烧一次 bcrypt。
            # 响应比"密码错"更快这点可被时序区分，但锁定是用户自己触发的，接受。
            if user.locked_until is not None and user.locked_until > now:
                await self._log_login(
                    username=username, user_id=user.id, result="fail",
                    reason="ACCOUNT_LOCKED", ip=ip, user_agent=user_agent,
                )
                raise AuthError("ACCOUNT_LOCKED")

            if not verify_password(password, user.password_hash):
                await self._register_failure(db, user)
                await self._log_login(
                    username=username, user_id=user.id, result="fail",
                    reason="INVALID_CREDENTIALS", ip=ip, user_agent=user_agent,
                )
                raise AuthError("INVALID_CREDENTIALS")

            self._clear_failures(user, now)
            tokens = await self._issue_tokens(db, user.id, now)
            await db.commit()
            await self._log_login(
                username=username, user_id=user.id, result="success",
                reason="", ip=ip, user_agent=user_agent,
            )
            return {**tokens, "user": serialize_user(user)}

    async def _register_failure(self, db: AsyncSession, user: OrmUser) -> None:
        """递增失败计数；到阈值时**在同一条 UPDATE 里**置 locked_until。

        分两步写会并发漏锁：两个请求各自读到 count=4，都判"还不到 5"，
        结果计数到 6 但 locked_until 仍是 NULL。
        """
        lock_after = settings.auth_max_failed_logins
        lock_delta = literal(
            timedelta(minutes=settings.auth_lockout_minutes), SAInterval
        )
        await db.execute(
            update(OrmUser)
            .where(OrmUser.id == user.id)
            .values(
                failed_login_count=OrmUser.failed_login_count + 1,
                locked_until=case(
                    (OrmUser.failed_login_count + 1 >= lock_after,
                     func.now() + lock_delta),
                    else_=OrmUser.locked_until,
                ),
                updated_at=func.now(),
            )
        )
        await db.commit()

    def _clear_failures(self, user: OrmUser, now: datetime) -> None:
        """登录成功清零。

        不清的话断续的失败会攒够 5 次，把正常用户锁掉。跟后面的签发落在同一个
        事务里，所以直接改 ORM 对象即可，不必绕 Core UPDATE。
        """
        if user.failed_login_count or user.locked_until is not None:
            user.failed_login_count = 0
            user.locked_until = None
            user.updated_at = now

    # ── 刷新 ────────────────────────────────────────────

    async def refresh(self, raw_refresh: str) -> dict:
        token_hash = hash_refresh_token(raw_refresh or "")

        async with self._sf() as db:
            now = await _db_now(db)
            result = await db.execute(
                select(OrmRefreshToken, OrmUser)
                .join(OrmUser, OrmUser.id == OrmRefreshToken.user_id)
                .where(OrmRefreshToken.token_hash == token_hash)
            )
            row = result.first()

            # 找不到 / 已过期 / 用户被封：统一 REFRESH_TOKEN_INVALID。
            # 三种原因不细分 —— 客户端动作一样（回登录页），细分只是给攻击者多一个信号。
            if row is None:
                raise AuthError("REFRESH_TOKEN_INVALID")
            token, user = row

            if token.expires_at <= now:
                raise AuthError("REFRESH_TOKEN_INVALID")

            if user.status == "disabled":
                raise AuthError("ACCOUNT_DISABLED")

            if token.revoked_at is not None:
                # 宽限期只覆盖"被轮换取代"的 token —— 那才是客户端刷新响应丢失后
                # 重试的场景。登出 / 改密 / 整族吊销是显式失效，一次都不该放行，
                # 否则偷到 token 的人在吊销后 30 秒内重放还能拿到新的一对。
                # 判据用 rotated_to：被轮换过的才非空。
                if token.rotated_to is None:
                    raise AuthError("REFRESH_TOKEN_INVALID")

                grace = timedelta(seconds=settings.auth_refresh_grace_seconds)
                if now - token.revoked_at > grace:
                    # 超过宽限期还在用已轮换的 token = 重放，整族吊销
                    await self._revoke_all(db, token.user_id, now)
                    await db.commit()
                    logger.warning(
                        "auth.refresh.replay  user_id=%s", token.user_id
                    )
                    raise AuthError("REFRESH_TOKEN_INVALID")
                # 宽限期内：客户端刷新响应丢失后带旧 token 重试，重新发一对即可。
                # 刻意不碰 revoked_at —— 覆盖它等于每次重试都把宽限窗口往后推，
                # 偷到 token 的人只要每 30 秒试一次就能一直续下去。
                tokens = await self._issue_tokens(db, token.user_id, now)
                await db.commit()
                return tokens

            tokens = await self._issue_tokens(
                db, token.user_id, now, rotate_from=token
            )
            await db.commit()
            return tokens

    # ── 登出 ────────────────────────────────────────────

    async def logout(self, raw_refresh: str) -> None:
        """吊销单个 refresh token。**幂等** —— token 已失效也正常返回。

        客户端登出路径不能因为"清理一个已经没了的凭证"而报错。
        """
        token_hash = hash_refresh_token(raw_refresh or "")
        async with self._sf() as db:
            now = await _db_now(db)
            result = await db.execute(
                select(OrmRefreshToken).where(
                    OrmRefreshToken.token_hash == token_hash
                )
            )
            token = result.scalar_one_or_none()
            if token is None or token.revoked_at is not None:
                return
            token.revoked_at = now
            await db.commit()

    async def logout_all(self, user_id: uuid.UUID) -> None:
        """吊销该用户全部 refresh token。

        注意当前的调用方也在吊销之列 —— 客户端要主动清本地回登录页，
        不能等下次 401。
        """
        async with self._sf() as db:
            now = await _db_now(db)
            await self._revoke_all(db, user_id, now)
            await db.commit()

    # ── 改密 ────────────────────────────────────────────

    async def change_password(
        self, user_id: uuid.UUID, old_password: str, new_password: str
    ) -> dict:
        """改密：校验旧密码，吊销全部 refresh token，返回新的一对 token。

        返回新 token 是必须的 —— 当前会话也在吊销之列，不返新的客户端改完即被踢。
        """
        async with self._sf() as db:
            now = await _db_now(db)
            user = await db.get(OrmUser, user_id)
            if user is None or user.status != "active":
                raise AuthError("TOKEN_INVALID")

            if not verify_password(old_password, user.password_hash):
                raise AuthError("INVALID_CREDENTIALS")

            try:
                new_hash = hash_password(new_password)
            except PasswordPolicyError as exc:
                raise AuthError("INVALID_REQUEST", str(exc))

            user.password_hash = new_hash
            user.failed_login_count = 0
            user.locked_until = None
            user.updated_at = now
            await self._revoke_all(db, user_id, now)
            tokens = await self._issue_tokens(db, user_id, now)
            await db.commit()
            logger.info("auth.change_password  user_id=%s", user_id)
            return {**tokens, "user": serialize_user(user)}

    # ── 当前用户 ────────────────────────────────────────

    async def get_user(self, user_id: uuid.UUID) -> OrmUser:
        """查库取当前用户 —— 不用 token 里的旧快照（token 只放了 id）。"""
        async with self._sf() as db:
            user = await db.get(OrmUser, user_id)
            if user is None or user.status != "active":
                raise AuthError("TOKEN_INVALID")
            return user

    # ── 内部 ────────────────────────────────────────────

    async def _issue_tokens(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        now: datetime,
        *,
        rotate_from: OrmRefreshToken | None = None,
    ) -> dict:
        """签发一对 token；给了 rotate_from 就把旧行标记为已轮换。"""
        access_token, expires_in = create_access_token(user_id)
        raw_refresh = generate_refresh_token()
        new_row = OrmRefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(raw_refresh),
            # 滑动续期：每次刷新都按 now + 7 天重算
            expires_at=now + timedelta(days=settings.auth_refresh_ttl_days),
        )
        db.add(new_row)
        await db.flush()

        if rotate_from is not None:
            rotate_from.revoked_at = now
            rotate_from.rotated_to = new_row.id

        return {
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "expires_in": expires_in,
        }

    async def revoke_all_tokens(self, db: AsyncSession, user_id: uuid.UUID) -> None:
        """吊销一个用户的全部 refresh token，落调用方的 db / 事务。

        给管理侧用（封号、重置密码）：带自己的 session 进来，与那次写入同一个
        `commit` —— 分成两次提交会出现「状态改了、token 还在」的窗口。

        **不重签**：管理员不是那个人，新会话该由他自己登录取。对比 `change_password`
        —— 那里必须返新 token，因为被吊销的会话里就有发起改密的那一个。
        """
        await self._revoke_all(db, user_id, await _db_now(db))

    async def _revoke_all(
        self, db: AsyncSession, user_id: uuid.UUID, now: datetime
    ) -> None:
        await db.execute(
            update(OrmRefreshToken)
            .where(
                OrmRefreshToken.user_id == user_id,
                OrmRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def _log_login(
        self,
        *,
        username: str,
        user_id: uuid.UUID | None,
        result: str,
        reason: str,
        ip: str | None,
        user_agent: str | None,
    ) -> None:
        """写审计日志。best-effort —— 写失败不能把一次成功的登录变成 500。"""
        try:
            async with self._sf() as db:
                db.add(OrmLoginLog(
                    user_id=user_id,
                    attempt_username=username,
                    ip=ip,
                    user_agent=user_agent,
                    result=result,
                    reason=reason,
                ))
                await db.commit()
        except Exception:
            logger.warning("auth.login_log.write_failed  username=%s", username)

    # ── 给依赖用 ────────────────────────────────────────

    @staticmethod
    def decode_access_token(token: str) -> uuid.UUID:
        """验签取用户 ID；把 security 层的异常翻成 AuthError。"""
        try:
            return _decode_access_token(token)
        except AccessTokenError as exc:
            raise AuthError(exc.code)
