"""OpenTelemetry Metrics —— MeterProvider + 所有 instrument 定义。
引擎中直接 import instrument 并调用 `counter.add(...)` / `histogram.record(...)`，不走 EventBus。
"""
from __future__ import annotations

import json
import asyncio
import logging
from datetime import datetime, timezone

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.exporter.prometheus import PrometheusMetricReader

_logger = logging.getLogger("observability.metrics")

_meter_provider: MeterProvider | None = None
_reader: PrometheusMetricReader | None = None


def init_meter(*, service_name: str = "iwork-agent", enabled: bool = True) -> None:
    """初始化 MeterProvider，PrometheusMetricReader 暴露 /metrics 端点。"""
    global _meter_provider, _reader

    if not enabled:
        _logger.info("OTel metrics disabled")
        return

    resource = Resource.create({SERVICE_NAME: service_name})
    _reader = PrometheusMetricReader()
    _meter_provider = MeterProvider(resource=resource, metric_readers=[_reader])
    metrics.set_meter_provider(_meter_provider)
    _logger.info("OTel metrics initialized  service=%s", service_name)


def get_meter() -> metrics.Meter:
    return metrics.get_meter("iwork.agent")


meter = get_meter()

# ── LLM 调用指标 ──
llm_call_duration = meter.create_histogram(
    "agent_llm_call_duration_seconds", description="LLM 调用耗时", unit="s")
llm_token_usage = meter.create_counter(
    "agent_llm_token_usage_total", description="Token 用量累计", unit="tokens")
llm_cost = meter.create_counter(
    "agent_llm_cost_dollars_total", description="LLM 调用成本", unit="dollars")

# ── 工具执行指标 ──
tool_call_total = meter.create_counter(
    "agent_tool_call_total", description="工具调用次数")
tool_call_duration = meter.create_histogram(
    "agent_tool_call_duration_seconds", description="工具执行耗时", unit="s")
tool_permission_denied_total = meter.create_counter(
    "agent_tool_permission_denied_total", description="权限拒绝次数")

# ── 消息 / 队列指标 ──
message_total = meter.create_counter(
    "agent_message_total", description="消息处理总数")
message_duration = meter.create_histogram(
    "agent_message_duration_seconds", description="消息从入队到终态总耗时", unit="s")
message_turns = meter.create_histogram(
    "agent_message_turns_total", description="每条消息消耗的 turn 数分布")
queue_depth = meter.create_up_down_counter(
    "agent_queue_depth", description="当前排队消息数")

# ── 会话 / 系统指标 ──
session_active = meter.create_up_down_counter(
    "agent_session_active", description="当前 PROCESSING 状态的会话数")
stream_buffer_size = meter.create_up_down_counter(
    "agent_stream_buffer_size", description="StreamBuffer 缓存条目数")


def get_prometheus_metrics_text() -> bytes:
    """返回 Prometheus 格式的指标文本，供 /metrics 端点使用。"""
    from prometheus_client import generate_latest
    try:
        return generate_latest()
    except Exception:
        return b""


async def periodic_metrics_dump(path: str = "logs/metrics_snapshot.json", interval: int = 60) -> None:
    """定期写 metrics snapshot 到文件，供无 Prometheus 时本地查看。"""
    while True:
        try:
            metrics_text = get_prometheus_metrics_text().decode()
            snapshot = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics": metrics_text,
            }
            import os
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                json.dump(snapshot, f, default=str)
        except Exception:
            _logger.exception("periodic_metrics_dump failed")
        await asyncio.sleep(interval)
