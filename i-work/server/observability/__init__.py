"""可观测性模块 —— EventBus、OTel Tracing/Metrics、structlog、审计、流推送。"""
from __future__ import annotations

# ── 错误码（从 error_codes.py 导入，保留此处的 re-export 以兼容旧引用） ──
from server.observability.error_codes import ERROR_CODES, get_diagnosis

# ── Hook 相关异常（供引擎捕获后推送 error.diagnosis） ──


class HookBlockedError(Exception):
    """Hook 返回 STOP 时抛出，携带具体原因。"""
    def __init__(self, tool_name: str, hook_name: str, reason: str = ""):
        self.tool_name = tool_name
        self.hook_name = hook_name
        self.reason = reason
        super().__init__(f"工具 '{tool_name}' 被 hook '{hook_name}' 阻止: {reason}")


class ToolBlockedError(HookBlockedError):
    """tool.before hook 阻止工具执行。"""


class LLMBlockedError(Exception):
    """llm.before hook 阻止 LLM 调用。"""
    def __init__(self, hook_name: str, reason: str = ""):
        self.hook_name = hook_name
        self.reason = reason
        super().__init__(f"LLM 调用被 hook '{hook_name}' 阻止: {reason}")


class MessageBeforeBlockedError(Exception):
    """message.before hook 阻止消息处理。"""
    def __init__(self, hook_name: str, reason: str = ""):
        self.hook_name = hook_name
        self.reason = reason
        super().__init__(f"消息被 hook '{hook_name}' 阻止: {reason}")


__all__ = [
    "ERROR_CODES",
    "get_diagnosis",
    "HookBlockedError",
    "ToolBlockedError",
    "LLMBlockedError",
    "MessageBeforeBlockedError",
]
