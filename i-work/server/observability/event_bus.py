"""轻量级进程内事件总线。仅用于 Stream 推送和 Audit 记录。
Tracing / Metrics / Logging 不走 EventBus，各自用 OTel / structlog 原生 API。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Awaitable

from server.observability.events import AgentEvent

EventHandler = Callable[[AgentEvent], Awaitable[None]]

_logger = logging.getLogger("observability.event_bus")


class EventBus:
    """轻量级进程内事件总线。仅用于 Stream 推送（前端实时事件流）和 Audit 记录（审计落库）。"""

    def __init__(self):
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """注册事件处理器（Stream / Audit）。"""
        self._handlers.append(handler)

    async def emit(self, event: AgentEvent) -> None:
        """发射事件给所有订阅者。各 handler 独立执行，单个异常不影响其他。"""
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                _logger.exception(
                    "EventHandler %s failed for event %s",
                    getattr(handler, "__name__", handler), event.type,
                )
