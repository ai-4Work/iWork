"""OpenTelemetry Tracing —— TracerProvider + OTLP exporter 初始化。
引擎中用 `with tracer.start_as_current_span(...)` 直接构建 Span 树，不走 EventBus。
"""
from __future__ import annotations

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

_logger = logging.getLogger("observability.tracing")

_tracer_provider: TracerProvider | None = None


def init_tracing(*, service_name: str = "iwork-agent", endpoint: str = "", enabled: bool = True) -> None:
    """初始化 TracerProvider。endpoint 为空或 enabled=False 时跳过 OTLP 导出（Span 仅通过 structlog trace_id 关联）。"""
    global _tracer_provider

    if not enabled:
        _logger.info("OTel tracing disabled")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            _logger.info("OTel tracing initialized  endpoint=%s  service=%s", endpoint, service_name)
        except Exception:
            _logger.exception("Failed to create OTLP exporter, tracing will use structlog-only mode")

    trace.set_tracer_provider(provider)
    _tracer_provider = provider


def get_tracer(name: str = "iwork.engine") -> trace.Tracer:
    return trace.get_tracer(name)
