"""错误码 → 诊断信息映射（7.6.3 节 + 8.8 节联动）。
从 observability/__init__.py 抽离，新增完整错误码清单。
"""
from __future__ import annotations

ERROR_CODES: dict[str, dict] = {
    # ── Hook 相关 ──
    "hook_denied": {
        "severity": "warning",
        "title": "Hook 拦截",
        "explanation": "工具被用户配置的 Hook 脚本阻止执行。",
        "suggestion": "如需执行此操作，请联系管理员调整 Hook 配置，或修改输入参数后重试。",
    },
    "hook_llm_blocked": {
        "severity": "fatal",
        "title": "LLM 调用被阻止",
        "explanation": "LLM 调用被 Hook 脚本阻止。",
        "suggestion": "请检查 Hook 配置，或联系管理员排查阻止原因。",
    },
    "hook_message_blocked": {
        "severity": "warning",
        "title": "消息被 Hook 阻止",
        "explanation": "用户输入被 Hook 脚本拦截，消息未进入处理流程。",
        "suggestion": "请修改输入内容后重试，或联系管理员了解拦截规则。",
    },
    # ── LLM 相关 ──
    "llm_rate_limit_exhausted": {
        "severity": "fatal",
        "title": "AI 服务暂时不可用",
        "explanation": "短时间内请求过多，AI 服务已触发速率限制，重试均失败。",
        "suggestion": "请等待 1-2 分钟后再发送新消息，或切换到其他可用模型。",
    },
    "llm_auth_failed": {
        "severity": "fatal",
        "title": "API 密钥失效",
        "explanation": "AI 服务认证失败，API 密钥可能已过期或配置错误。",
        "suggestion": "联系管理员检查 API 配置。",
    },
    "llm_retrying": {
        "severity": "info",
        "title": "正在重试",
        "explanation": "AI 服务连接异常，正在自动重试中。",
        "suggestion": "无需操作，系统会自动恢复。",
    },
    # ── 上下文相关 ──
    "context_token_exceeded": {
        "severity": "fatal",
        "title": "对话上下文过长",
        "explanation": "当前对话历史和上下文已超过模型最大 token 限制。",
        "suggestion": "开启新任务或删除部分历史消息后重试。",
    },
    # ── 工具相关 ──
    "tool_permission_denied": {
        "severity": "warning",
        "title": "操作被安全策略拦截",
        "explanation": "该操作被工作空间安全策略阻止。",
        "suggestion": "检查工作空间权限设置，或联系管理员授权。",
    },
    "tool_execution_timeout": {
        "severity": "fatal",
        "title": "工具执行超时",
        "explanation": "工具执行超过配置的最大时间限制。",
        "suggestion": "重试或检查网络/系统连接状态。",
    },
    # ── 引擎相关 ──
    "max_turns_reached": {
        "severity": "fatal",
        "title": "任务步骤过多",
        "explanation": "该消息消耗的推理轮次已达到上限。",
        "suggestion": "拆分任务为更小的子任务，分多次发送。",
    },
    "loop_detected": {
        "severity": "fatal",
        "title": "检测到重复循环",
        "explanation": "AI 重复执行相同操作，可能陷入死循环。",
        "suggestion": "AI 已自动终止，请重新描述需求或调整指令。",
    },
    "execution_timeout": {
        "severity": "fatal",
        "title": "执行超时",
        "explanation": "消息处理超过了配置的最大时间限制。",
        "suggestion": "请尝试简化请求，或拆分任务为多条消息。",
    },
    "content_filter": {
        "severity": "fatal",
        "title": "内容安全拦截",
        "explanation": "AI 服务的安全策略拦截了本次请求或响应。",
        "suggestion": "请修改输入内容后重试。",
    },
    # ── 会话相关 ──
    "client_disconnected": {
        "severity": "info",
        "title": "客户端连接中断",
        "explanation": "客户端网络连接已断开，AI 在后台继续处理。",
        "suggestion": "AI 在后台继续运行，请刷新页面或重新连接恢复。",
    },
    "queue_full": {
        "severity": "warning",
        "title": "消息队列已满",
        "explanation": "当前排队消息已达到上限，新消息暂时无法入队。",
        "suggestion": "等待当前任务完成后发送，或取消排队中的消息。",
    },
    "session_archived": {
        "severity": "info",
        "title": "任务已归档",
        "explanation": "该任务已被归档，无法发送新消息。",
        "suggestion": "无法发送新消息，请创建新任务继续工作。",
    },
    # ── 内部错误 ──
    "internal_error": {
        "severity": "fatal",
        "title": "服务内部错误",
        "explanation": "服务端发生未预期的错误。",
        "suggestion": "请稍后重试，如频繁出现请联系管理员。",
    },
}


def get_diagnosis(code: str, **kwargs) -> dict:
    """根据错误码生成诊断信息，kwargs 用于填充模板变量。"""
    info = ERROR_CODES.get(code, ERROR_CODES["internal_error"]).copy()
    explanation = info.get("explanation", "")
    for key, val in kwargs.items():
        explanation = explanation.replace("{" + key + "}", str(val))
    info["explanation"] = explanation
    info["error_code"] = code
    return info
