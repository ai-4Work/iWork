"""
C-3 幂等档：工具"结果不确定/失败时能否自动重放"的权威判定（§12.3.7 重试矩阵）。

判据只来自工具名 + 工具定义自带标注；默认 non-idempotent（保守白名单：只标放宽项）。
- read-only       读操作，无条件安全重放（失败即重试无害）；
- idempotent      整内容覆盖写等，重复执行结果一致，可安全重放；
- non-idempotent  副作用/写类，判不出时一律不自动重放（默认）。

客户端工具（client.tool_request 执行）与 SERVER_MEMORY_TOOLS（服务端直执行）分类对齐。
"""
from __future__ import annotations

# 工具名白名单（客户端执行 / SERVER_MEMORY_TOOLS 服务端直执行的子集）
READ_ONLY_TOOLS = frozenset({
    # 文件读取（客户端工具）
    "read_file", "read", "glob", "grep",
    # 记忆/检索（服务端工具）
    "recall", "load_memory",
})
IDEMPOTENT_TOOLS = frozenset({
    # 整文件覆盖写可安全重复（客户端工具）
    "write_file", "write",
})

DEFAULT_IDEMPOTENCY = "non-idempotent"


def tool_idempotency(tool_name: str) -> str:
    """返回工具幂等档：read-only / idempotent / non-idempotent。"""
    if tool_name in READ_ONLY_TOOLS:
        return "read-only"
    if tool_name in IDEMPOTENT_TOOLS:
        return "idempotent"
    return DEFAULT_IDEMPOTENCY


def tool_side_effect(tool_name: str) -> bool:
    """该工具执行是否构成副作用（D 副作用账本"已执行清单"筛子）。

    判据只一问：动作是否改变动作前的外部状态？read-only 永不落账；
    其余（即使幂等可重放的整文件覆盖写）都动了文件系统 → 记为副作用。
    与 tool_idempotency 同源派生，避免两处白名单漂移。
    """
    return tool_idempotency(tool_name) != "read-only"


def apply_to_tool_def(tool_def: dict) -> dict:
    """给工具定义补上幂等档（已有则尊重，未标则按名分类）。供注册表/下发前分类用。"""
    if "idempotency" not in tool_def:
        tool_def = {**tool_def, "idempotency": tool_idempotency(tool_def.get("name", ""))}
    return tool_def
