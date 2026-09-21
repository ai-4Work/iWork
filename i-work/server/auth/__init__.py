"""登录认证：密码哈希、token 签发验签、登录/刷新/登出业务。

对应 docs/chapters/18-登录认证模块.md。
"""

from server.auth.service import AuthError, AuthService

__all__ = ["AuthError", "AuthService"]
