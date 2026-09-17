"""审计日志 —— AuditSubscriber 订阅 EventBus，将敏感操作写入 PostgreSQL audit_logs 表。
同时提供 audit_log() 独立函数供非 EventBus 场景直接写入。
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import text

from server.observability.events import AgentEvent, AgentEventType

_logger = logging.getLogger("observability.audit")

# 需要审计的事件类型
AUDIT_EVENT_TYPES: set[AgentEventType] = {
    AgentEventType.TOOL_EXECUTED,
    AgentEventType.TOOL_PERMISSION_DENIED,
    AgentEventType.NETWORK_APPROVAL,
    AgentEventType.MESSAGE_ERROR,
    AgentEventType.PLAN_INTERACTION,
}

# 与 audit_logs 列宽对齐，避免超出导致整条审计记录插入失败
_ACTION_MAX_LEN = 50      # audit_logs.action   VARCHAR(50)
_RESOURCE_MAX_LEN = 100   # audit_logs.resource VARCHAR(100)


def _clip(value, limit: int) -> str | None:
    """裁剪到列宽。完整内容仍保留在 detail，这里只做标识用途。"""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value[:limit]


class AuditSubscriber:
    """订阅需要审计的 AgentEvent，写入 audit_logs 表。"""

    def __init__(self, db_session_factory):
        self.db_factory = db_session_factory

    async def handle(self, event: AgentEvent) -> None:
        if event.type not in AUDIT_EVENT_TYPES:
            return
        if self.db_factory is None:
            return

        action = self._map_action(event)
        resource = self._map_resource(event)
        try:
            async with self.db_factory() as db:
                await db.execute(
                    text("""
                        INSERT INTO audit_logs (user_id, session_id, message_id, action, resource, detail)
                        VALUES (:uid, :sid, :mid, :act, :res, :det)
                    """),
                    {
                        "uid": event.user_id, "sid": event.session_id,
                        "mid": event.message_id,
                        "act": _clip(action, _ACTION_MAX_LEN),
                        "res": _clip(resource, _RESOURCE_MAX_LEN),
                        "det": json.dumps(event.data, default=str),
                    },
                )
                await db.commit()
        except Exception:
            _logger.exception("AuditSubscriber insert failed  action=%s", action)

    def _map_action(self, event: AgentEvent) -> str:
        if event.type == AgentEventType.TOOL_EXECUTED:
            tool_name = event.data.get("tool_name", "")
            if tool_name in ("read_file", "glob", "grep"):
                return "tool.file_read"
            if tool_name in ("write_file", "edit_file"):
                return "tool.file_write"
            if tool_name == "bash":
                return "tool.shell_exec"
            if tool_name in ("load_memory", "write_memory", "delete_memory"):
                return f"tool.{tool_name}"
            return f"tool.{tool_name}"
        if event.type == AgentEventType.TOOL_PERMISSION_DENIED:
            return "tool.permission_denied"
        if event.type == AgentEventType.NETWORK_APPROVAL:
            return "tool.network_approval"
        if event.type == AgentEventType.MESSAGE_ERROR:
            return "system.error_fatal"
        if event.type == AgentEventType.PLAN_INTERACTION:
            return "user.plan_action"
        return "unknown"

    def _map_resource(self, event: AgentEvent) -> str | None:
        d = event.data
        if event.type == AgentEventType.TOOL_EXECUTED:
            tool = d.get("tool_name", "")
            inp = d.get("input", {})
            if tool in ("read_file", "glob", "grep"):
                return inp.get("file_path") or inp.get("pattern")
            if tool in ("write_file", "edit_file"):
                return inp.get("file_path")
            if tool == "bash":
                return inp.get("command")
            return tool
        if event.type == AgentEventType.MESSAGE_ERROR:
            return d.get("error_type")
        if event.type == AgentEventType.NETWORK_APPROVAL:
            return d.get("host")
        return None


async def audit_log(
    db,
    action: str,
    user_id: UUID,
    detail: dict,
    *,
    session_id: UUID | None = None,
    message_id: UUID | None = None,
    resource: str | None = None,
) -> None:
    """独立审计写入函数，供非 EventBus 场景直接调用。"""
    await db.execute(
        text("""
            INSERT INTO audit_logs (user_id, session_id, message_id, action, resource, detail)
            VALUES (:uid, :sid, :mid, :act, :res, :det)
        """),
        {
            "uid": user_id, "sid": session_id, "mid": message_id,
            "act": _clip(action, _ACTION_MAX_LEN),
            "res": _clip(resource, _RESOURCE_MAX_LEN),
            "det": json.dumps(detail, default=str),
        },
    )
