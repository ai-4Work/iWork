"""StreamSubscriber —— 订阅 chunk 同名事件，零翻译推送 NDJSON 到前端。
内部事件（llm.call_*, tool.*）不推前端，仅 Audit 消费。
"""
from __future__ import annotations

import logging
from server.observability.events import AgentEvent, AgentEventType

_logger = logging.getLogger("observability.stream")

# chunk 同名事件 —— data 结构直接等于 NDJSON chunk，零翻译
CHUNK_TYPES: set[AgentEventType] = {
    AgentEventType.QUEUE_ENQUEUED,
    AgentEventType.MESSAGE_START,
    AgentEventType.MESSAGE_COMPLETE,
    AgentEventType.MESSAGE_ERROR,
    AgentEventType.TOKEN_USAGE,
    AgentEventType.PLAN_GENERATED,
    AgentEventType.PLAN_QUESTION,
    AgentEventType.SYSTEM_STATUS,
}


class StreamSubscriber:
    """接收 AgentEvent，过滤出 chunk 同名事件，通过 EngineManager 查找到对应引擎的 _push_chunk 推送前端。"""

    def __init__(self):
        self._engine_manager = None  # EngineManager，延迟注入

    def set_engine_manager(self, engine_manager) -> None:
        self._engine_manager = engine_manager

    async def handle(self, event: AgentEvent) -> None:
        if event.type not in CHUNK_TYPES:
            return
        if self._engine_manager is None:
            return
        engine = self._engine_manager.get(event.session_id)
        if engine is None:
            return
        try:
            await engine._push_chunk(event.data)
        except Exception:
            _logger.exception("StreamSubscriber send failed  type=%s", event.type)
