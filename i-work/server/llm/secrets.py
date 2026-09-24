"""模型 API Key 的加解密与掩码。

本仓此前没有任何加解密设施（唯一沾凭据卫生的地方是 `refresh_tokens` 只存 sha256
哈希）。这里引入 Fernet 对称加密，因为 `llm_model` 的 key **必须能还原成明文**去发
请求 —— 哈希做不到；而明文进库不可接受：远端部署靠手工拷文件同步、多人共用一个
`agent_test` 库，明文等于把 key 交给所有能读库的人。

主密钥 `IWORK_MODEL_API_KEY_ENCRYPTION_KEY` 刻意留在本地 `.env`、不落库：
整库泄露时没有主密钥也解不开。
未配置主密钥时本模块一律**拒绝**加密，key 只能继续走 `.env`。
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretKeyError(RuntimeError):
    """主密钥缺失或非法，无法加解密。"""


def _fernet(secret_key: str) -> Fernet:
    if not secret_key:
        raise SecretKeyError(
            "IWORK_MODEL_API_KEY_ENCRYPTION_KEY 未配置，无法加解密模型密钥。生成命令："
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(secret_key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretKeyError(
            f"IWORK_MODEL_API_KEY_ENCRYPTION_KEY 不是合法的 Fernet 密钥：{exc}"
        ) from exc


def encrypt_key(plain: str, secret_key: str) -> str:
    """明文 → Fernet 密文（str）。空明文返回空串，调用方据此存 NULL/空。"""
    if not plain:
        return ""
    return _fernet(secret_key).encrypt(plain.encode()).decode()


def decrypt_key(cipher: str, secret_key: str) -> str:
    """密文 → 明文。密文为空返回空串。主密钥不对或密文被篡改时抛 SecretKeyError。"""
    if not cipher:
        return ""
    try:
        return _fernet(secret_key).decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise SecretKeyError(
            "模型密钥解密失败：IWORK_MODEL_API_KEY_ENCRYPTION_KEY 与加密时不一致，"
            "或密文已损坏。"
            "换过主密钥的话，需要重新填写各模型的 API Key。"
        ) from exc


def decrypt_key_or_none(cipher: str, secret_key: str) -> str | None:
    """解密失败返回 None 而不是抛异常 —— 请求路径上不该因为一条坏密钥整轮崩掉。

    调用方拿到 None 时的语义是「这个模型当前没有可用密钥」，由上层决定报错还是回落。
    """
    try:
        return decrypt_key(cipher, secret_key)
    except SecretKeyError:
        return None


def mask_key(plain: str) -> str:
    """生成掩码预览（如 `sk-a…1b2c`）。**不含足以还原明文的信息**，仅供管理页显示。

    过短的 key 一律回 "…"：露出 4 头 4 尾对短串来说就是露出全部。
    """
    if not plain:
        return ""
    if len(plain) <= 12:
        return "…"
    return f"{plain[:4]}…{plain[-4:]}"
