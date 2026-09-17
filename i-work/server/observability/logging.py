"""结构化日志 —— structlog 配置（含 OTel trace_id 注入 + 文件轮转）。
替代原有 `logging.basicConfig` 方案，所有模块统一使用此 logger。
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
import os
import sys

import structlog
from opentelemetry import trace


def add_otel_context(_logger, _method_name, event_dict):
    """structlog processor：注入当前 OTel span 的 trace_id / span_id。"""
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def format_printf_style(_logger, _method_name, event_dict):
    """将 stdlib logging 风格的 %s 占位符自动插值。"""
    positional_args = event_dict.pop("positional_args", ())
    if positional_args:
        event = event_dict.get("event", "")
        if "%" in event and isinstance(event, str):
            try:
                event_dict["event"] = event % positional_args
            except (TypeError, ValueError):
                pass
    return event_dict


def colorize_event(_logger, _method_name, event_dict):
    """给 event 标签加青色高亮，其余值不加颜色。"""
    event = event_dict.get("event", "")
    if isinstance(event, str) and event:
        event_dict["event"] = f"\033[1;36m{event}\033[0m"
    return event_dict


def setup_logging(*, log_dir: str = "logs", log_level: int = logging.INFO) -> None:
    """配置 structlog + 文件轮转。

    - stdout: 人类可读的 console 格式（开发友好）
    - 文件: JSON 格式按天轮转，保留 30 天（供 Loki / grep 消费）
    - 自动注入 trace_id / span_id
    """
    os.makedirs(log_dir, exist_ok=True)

    # ── 控制台 handler：人类可读格式 ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # ── 文件 handler：JSON 格式，按天轮转 ──
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "agent.log"),
        when="midnight", backupCount=30, encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
    ))

    # ── structlog 配置 ──
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            add_otel_context,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            format_printf_style,
            colorize_event,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 将 handler 挂到根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # 降噪第三方库（uvicorn.access 由中间件替代）
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "opentelemetry", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str = "iwork") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


# 默认 logger 实例
logger = get_logger()
