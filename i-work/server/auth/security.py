"""密码哈希与不透明 token 的生成/哈希。

纯函数，不碰数据库 —— 方便单测。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from server.config import settings

# ── 密码 ──────────────────────────────────────────────

# cost 12 ≈ 250ms/次：暴力破解足够贵，登录延迟用户无感
_BCRYPT_ROUNDS = 12

PASSWORD_MIN_BYTES = 8
# bcrypt 的硬上限，超出部分被静默截断 —— 两个不同的长密码会变得等价
PASSWORD_MAX_BYTES = 72

# 用户不存在时也要跑一次密码校验，否则响应快慢能反推账号是否存在。
# 必须是**预先算好的固定假哈希**：临时随机值每次成本都不同，时间特征会露馅。
_DUMMY_HASH = bcrypt.hashpw(
    b"iwork-nonexistent-user", bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
)


class PasswordPolicyError(ValueError):
    """密码不满足策略。"""


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) < PASSWORD_MIN_BYTES:
        raise PasswordPolicyError(f"密码至少 {PASSWORD_MIN_BYTES} 字节")
    if len(raw) > PASSWORD_MAX_BYTES:
        raise PasswordPolicyError(
            f"密码最多 {PASSWORD_MAX_BYTES} 字节"
            "（bcrypt 上限，超出会被静默截断，等于少了几位）"
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """校验密码。

    `password_hash` 为 None（不可密码登录）时走固定假哈希：耗时与真实校验一致，
    调用方据此对"账号不存在"和"密码错"返回同一个错误。
    """
    has_real_hash = password_hash is not None
    target = password_hash.encode("ascii") if has_real_hash else _DUMMY_HASH
    try:
        matched = bcrypt.checkpw(password.encode("utf-8"), target)
    except ValueError:
        # 哈希格式损坏或密码超 72 字节被拒；校验时间已经花掉了
        return False
    return matched and has_real_hash


# ── access token（JWT）────────────────────────────────

ISSUER = "iwork"
ALGORITHM = "HS256"


class AccessTokenError(Exception):
    """access token 无效或过期。code 供上层映射成错误码。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code  # TOKEN_EXPIRED | TOKEN_INVALID


def create_access_token(
    user_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[str, int]:
    """签发 access token，返回 (token, expires_in 秒)。

    payload 只放 sub / iss / iat / exp —— 不放 username / status：
    放了就会和库里的值不一致（改名、封号后 token 里还是旧的）。需要时查库。
    """
    issued = now or datetime.now(timezone.utc)
    expires_in = settings.auth_access_ttl_minutes * 60
    payload = {
        "sub": str(user_id),
        "iss": ISSUER,
        "iat": issued,
        "exp": issued + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> uuid.UUID:
    """验签并取回用户 ID。**不查库** —— 封号后旧 token 最长 15 分钟内仍可用。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],  # 写死，拒绝 alg: none
            issuer=ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise AccessTokenError("TOKEN_EXPIRED")
    except jwt.PyJWTError:
        raise AccessTokenError("TOKEN_INVALID")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise AccessTokenError("TOKEN_INVALID")


# ── refresh token（不透明随机串）──────────────────────

REFRESH_PREFIX = "rt_"
_REFRESH_BYTES = 32


def generate_refresh_token() -> str:
    """明文形式 `rt_` + 32 字节随机串。前缀便于日志识别与泄露扫描。"""
    return REFRESH_PREFIX + secrets.token_urlsafe(_REFRESH_BYTES)


def hash_refresh_token(raw: str) -> str:
    """sha256 而非 bcrypt：随机串熵已足够，慢哈希只会白白拖慢每次刷新。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
