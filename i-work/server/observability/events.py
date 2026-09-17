"""AgentEvent 类型定义 —— 仅 Stream + Audit 使用。
Tracing / Metrics / Logging 不走 EventBus，各自用 OTel / structlog 原生 API。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from enum import Enum


class AgentEventType(str, Enum):
    # === 消息生命周期 ===
    QUEUE_ENQUEUED = "queue.enqueued"
    MESSAGE_START = "message.start"
    MESSAGE_COMPLETE = "message.complete"
    MESSAGE_ERROR = "message.error"

    # === LLM 调用 ===
    LLM_CALL_STARTED = "llm.call_started"
    LLM_FIRST_TOKEN = "llm.first_token"
    LLM_CALL_COMPLETED = "llm.call_completed"
    LLM_CALL_FAILED = "llm.call_failed"
    TOKEN_USAGE = "token.usage"

    # === 工具执行 ===
    TOOL_DISPATCHED = "tool.dispatched"
    TOOL_EXECUTED = "tool.executed"
    TOOL_FAILED = "tool.failed"
    TOOL_PERMISSION_DENIED = "tool.permission_denied"
    NETWORK_APPROVAL = "tool.network_approval"

    # === Plan 交互 ===
    PLAN_GENERATED = "plan.generated"
    PLAN_QUESTION = "plan.question"
    PLAN_INTERACTION = "plan.interaction"

    # === 上下文 ===
    CONTEXT_BUILT = "context.built"
    CONTEXT_COMPRESSED = "context.compressed"

    # === 系统 ===
    SYSTEM_STATUS = "system.status"


@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    timestamp: datetime
    session_id: UUID
    user_id: UUID
    message_id: UUID
    data: dict[str, Any] = field(default_factory=dict)


def now() -> datetime:
    return datetime.now(timezone.utc)
