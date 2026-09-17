from __future__ import annotations
from uuid import UUID
from pydantic import BaseModel
from typing import Literal


# ═══════════════════════════════════════════════════════════════
# 1. 思考与文本（所有模式实时推送）
# ═══════════════════════════════════════════════════════════════

class AgentThinking(BaseModel):
    type: Literal["agent.thinking"] = "agent.thinking"
    seq: int
    delta: str
    turn: int
    message_id: UUID


class AgentText(BaseModel):
    type: Literal["agent.text"] = "agent.text"
    seq: int
    delta: str
    turn: int
    message_id: UUID


# ═══════════════════════════════════════════════════════════════
# 2. 工具调用
# ═══════════════════════════════════════════════════════════════

class ClientToolRequest(BaseModel):
    """服务端委派前端执行工具（含 Build 模式确认）。"""
    type: Literal["client.tool_request"] = "client.tool_request"
    seq: int
    request_id: str
    tool_name: str
    input: dict
    message_id: UUID
    # Build 模式专用（可选）
    tool_call_id: str | None = None
    step: int | None = None
    requires_approval: bool = False
    reasoning: str | None = None
    policy: dict | None = None   # 阶段1：策略包（filesystem/network/sandbox），客户端据此执行/预检


class ClientToolTimeout(BaseModel):
    type: Literal["client.tool_timeout"] = "client.tool_timeout"
    seq: int
    request_id: str
    message: str


# ═══════════════════════════════════════════════════════════════
# 3. Plan 模式专用数据块
# ═══════════════════════════════════════════════════════════════

class PlanGenerated(BaseModel):
    type: Literal["plan.generated"] = "plan.generated"
    seq: int
    message_id: UUID
    plan_text: str


class PlanQuestion(BaseModel):
    type: Literal["plan.question"] = "plan.question"
    seq: int
    message_id: UUID
    question: str
    options: list[str] | None = None
    input_type: Literal["select", "text"] = "select"
    context: str | None = None


class PlanQuestionTimeout(BaseModel):
    type: Literal["plan.question_timeout"] = "plan.question_timeout"
    seq: int
    message_id: UUID


# ═══════════════════════════════════════════════════════════════
# 5. 队列状态事件（实时推送排队位置变化）
# ═══════════════════════════════════════════════════════════════

class QueueEnqueued(BaseModel):
    """消息入队后立即推送，告知客户端当前排位"""
    type: Literal["queue.enqueued"] = "queue.enqueued"
    seq: int
    message_id: UUID
    queue_position: int
    queue_size: int
    ahead_message_id: UUID | None = None
    agent_id: str | None = None


class QueuePositionChanged(BaseModel):
    """前方消息完成/取消导致重排，通知排位变化"""
    type: Literal["queue.position_changed"] = "queue.position_changed"
    seq: int
    message_id: UUID
    new_position: int
    queue_size: int


# ═══════════════════════════════════════════════════════════════
# 6. Token 统计（每次 LLM 调用完实时推送累计）
# ═══════════════════════════════════════════════════════════════

class TokenUsage(BaseModel):
    type: Literal["token.usage"] = "token.usage"
    seq: int
    message_id: UUID
    tokens_in: int
    tokens_out: int


# ═══════════════════════════════════════════════════════════════
# 7. 生命周期 & 系统数据块
# ═══════════════════════════════════════════════════════════════

class MessageStart(BaseModel):
    type: Literal["message.start"] = "message.start"
    seq: int
    message_id: UUID
    mode: str
    scene_mode: str
    workspace: str
    agent_id: str | None = None


class MessageComplete(BaseModel):
    type: Literal["message.complete"] = "message.complete"
    seq: int
    message_id: UUID
    summary: dict  # {turns, tokens_in, tokens_out, duration_ms, tool_calls_count}


class MessageError(BaseModel):
    type: Literal["message.error"] = "message.error"
    seq: int
    message_id: UUID
    message: str
    code: str
    fatal: bool
    turn: int | None = None


class SystemStatus(BaseModel):
    type: Literal["system.status"] = "system.status"
    seq: int
    code: str
    message: str
    detail: str | None = None
    attempt: int
    max_retries: int
    message_id: UUID


# ═══════════════════════════════════════════════════════════════
# 8. 用户侧可观测性增强（7.6 节）
# ═══════════════════════════════════════════════════════════════

class AgentStep(BaseModel):
    """步骤开始（Plan/Build 模式）。"""
    type: Literal["agent.step"] = "agent.step"
    seq: int
    message_id: UUID
    step: int
    total_steps: int
    description: str
    status: Literal["pending", "running", "done", "failed"] = "running"


class AgentStepCompleted(BaseModel):
    """步骤结束。"""
    type: Literal["agent.step_completed"] = "agent.step_completed"
    seq: int
    message_id: UUID
    step: int
    status: Literal["done", "failed", "skipped"] = "done"
    summary: str = ""


class MessageUsage(BaseModel):
    """每条消息完成时的 token 用量与成本追踪。"""
    type: Literal["message.usage"] = "message.usage"
    seq: int
    message_id: UUID
    tokens: dict  # {input, output, cache_read, cache_write}
    cost: dict     # {input, output, total, currency}
    savings: dict | None = None  # {cache_discount, effective_cost}


class SessionUsage(BaseModel):
    """会话累计用量。"""
    type: Literal["session.usage"] = "session.usage"
    seq: int
    session_id: str
    tokens_total: dict  # {input, output}
    cost_total: float
    message_count: int


class ErrorDiagnosis(BaseModel):
    """结构化错误诊断卡片（7.6.3 节）。"""
    type: Literal["error.diagnosis"] = "error.diagnosis"
    seq: int
    message_id: UUID
    error_code: str
    severity: Literal["info", "warning", "fatal"] = "fatal"
    title: str
    explanation: str
    suggestion: str
    detail: dict | None = None


# ═══════════════════════════════════════════════════════════════
# 9. 多 Agent 事件（9.10 节）
# ═══════════════════════════════════════════════════════════════

class SessionPublish(BaseModel):
    """Lead 将子任务委派给成员 Agent。"""
    type: Literal["session.publish"] = "session.publish"
    seq: int
    agent_id: str
    to: str
    task_type: str
    prompt: str


class AgentStatus(BaseModel):
    """子 Agent 状态变化通知。agent_id 为 lead，to 指向子 agent。"""
    type: Literal["agent.status"] = "agent.status"
    seq: int
    agent_id: str
    to: str
    status: Literal["thinking", "running", "done", "error"]
    output_preview: str | None = None




StreamChunk = (
    AgentThinking | AgentText
    | ClientToolRequest | ClientToolTimeout
    | PlanGenerated | PlanQuestion | PlanQuestionTimeout
    | QueueEnqueued | QueuePositionChanged
    | TokenUsage
    | MessageStart | MessageComplete | MessageError
    | SystemStatus
    | AgentStep | AgentStepCompleted
    | MessageUsage | SessionUsage
    | ErrorDiagnosis
    | SessionPublish | AgentStatus
)
