from __future__ import annotations
from uuid import UUID, uuid4
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InvocationState(str, Enum):
    """工具调用账本状态机（阶段 C-1）。

    issued（意图已落账，结果未回/不确定）
      → completed（确定性结果，一次性终态，不可回退）
      → skipped（明确未执行，如用户跳过审批，可从 issued 覆盖，不可覆盖 completed）
      → superseded（被新的 issued 取代，即重放场景）
    客户端工具超时/取消后停在 issued（结果不确定，留待 C-2 对账窗裁决）。
    注意：工具"执行了但失败"记为 completed + result.success=false，不落 skipped。
    """
    ISSUED = "issued"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"


def normalize_client_result(result: object) -> object:
    """把客户端 ToolResult 的 status 归一成账本契约的 success。

    客户端用 status('success'|'error') 表达成败，而账本/审计/UI 读 result.success
    （见 InvocationState 文档：失败 = completed + result.success=false）。
    不归一的话失败会被下游读成成功（副作用清单显绿、审计恒报失败）。
    递归下钻是为了兼容 reconcile 应答信封 {reconcile, state, result}。
    """
    if not isinstance(result, dict):
        return result
    out = dict(result)
    if "success" not in out and "status" in out:
        out["success"] = out["status"] == "success"
    if isinstance(out.get("result"), dict):
        out["result"] = normalize_client_result(out["result"])
    return out


class ToolInvocation(BaseModel):
    """一条工具调用的执行账本记录。invocation_id == 客户端下发 request_id。"""
    invocation_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    message_id: UUID | None = None          # 归属消息（M2 边界锚点所在消息）
    turn: int | None = None
    attempt: int | None = None              # 运行序号（D）：首跑/regenerate 递增开新块，continue 复用当前
    tool_name: str
    location: str = "server"                # client | server（tool_dispatcher.classify().value）
    state: InvocationState = InvocationState.ISSUED
    input: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    side_effect: bool = True                # 是否含副作用
    idempotency: str = "non-idempotent"     # read-only | idempotent | non-idempotent（C-3 预留，保守默认）
    requires_approval: bool = False
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
