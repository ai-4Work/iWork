"""FastAPI 中间件：从 HTTP 请求头提取 trace context，注入 OTel context。
确保 trace_id 从入站请求贯穿到引擎内部所有 Span。
"""
from __future__ import annotations

import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from opentelemetry import context, trace
from opentelemetry.propagate import extract

_logger = logging.getLogger("observability.middleware")


class OtelTraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ctx = extract(request.headers)
        token = context.attach(ctx)
        try:
            response = await call_next(request)
            return response
        finally:
            context.detach(token)
