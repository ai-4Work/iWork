from __future__ import annotations
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import Response, PlainTextResponse

from server.config import settings
from server.llm.client import FakeLLMClient
from server.engine.query_loop import EngineManager

# ── 可观测性：structlog 替代原有 logging.basicConfig ──
from server.observability.logging import setup_logging, get_logger

setup_logging(log_level=logging.INFO)
logger = get_logger("iwork")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 0. 可观测性基础设施 ──
    from server.observability.tracing import init_tracing
    from server.observability.metrics import init_meter, periodic_metrics_dump
    from server.observability.event_bus import EventBus
    from server.observability.stream import StreamSubscriber
    from server.observability.audit import AuditSubscriber

    init_tracing(
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_endpoint,
        enabled=settings.otel_enabled,
    )
    init_meter(
        service_name=settings.otel_service_name,
        enabled=settings.otel_enabled,
    )

    # EventBus（仅 Stream + Audit）
    event_bus = EventBus()
    stream_subscriber = StreamSubscriber()
    event_bus.subscribe(stream_subscriber.handle)

    audit_subscriber = AuditSubscriber(None)  # DB session factory 在 DB 初始化后设置
    event_bus.subscribe(audit_subscriber.handle)
    app.state.event_bus = event_bus
    app.state.stream_subscriber = stream_subscriber

    # 启动定期 metrics dump（文件兜底）
    asyncio.create_task(periodic_metrics_dump())

    # ── 1. 数据库引擎 + session factory ──
    from server.db.engine import create_engine
    from server.db.seed import seed_default_user, seed_hub_data, seed_expert_hub_data
    from server.storage.postgres import (
        PgSessionRepo, PgMessageRepo, UserRepo,
        ConversationHistoryRepo, UserSkillRepo, UserMcpRepo,
        SkillHubRepo, McpHubRepo,
    )
    engine, session_factory = create_engine(settings.database_url)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    # ── Audit subscriber: 注入 DB session factory ──
    audit_subscriber.db_factory = session_factory

    # ── 2. 种子数据 ──
    user_repo = UserRepo(session_factory)
    default_user = await user_repo.get_or_create_default()
    app.state.default_user_id = default_user.id
    await seed_hub_data(session_factory)
    await seed_expert_hub_data(session_factory)

    # ── 3. PG repos ──
    session_repo = PgSessionRepo(session_factory)
    message_repo = PgMessageRepo(session_factory)

    # ── 4. LLM client ──
    if settings.llm_provider == "deepseek" and settings.deepseek_api_key:
        from server.llm.client import DeepSeekLLMClient
        llm = DeepSeekLLMClient()
        logger.info("LLM provider: deepseek  model=%s", settings.default_model)
    elif settings.anthropic_api_key:
        from server.llm.client import AnthropicLLMClient
        llm = AnthropicLLMClient()
        logger.info("LLM provider: anthropic  model=%s", settings.default_model)
    else:
        llm = FakeLLMClient()
        logger.warning("No API key configured, using FakeLLMClient")

    # ── 5. Skill 注册中心 ──
    from server.skills.skill_registry import SkillRegistry
    user_skill_repo = UserSkillRepo(session_factory)
    user_mcp_repo = UserMcpRepo(session_factory)
    skill_hub_repo = SkillHubRepo(session_factory)
    skill_registry = SkillRegistry(
        skill_hub_repo=skill_hub_repo,
        user_skill_repo=user_skill_repo,
    )
    app.state.skill_registry = skill_registry

    app.state.engine_manager = EngineManager(
        session_repo, message_repo, llm,
        session_factory=session_factory,
        skill_registry=skill_registry,
        user_mcp_repo=user_mcp_repo,
        event_bus=event_bus,
    )
    stream_subscriber.set_engine_manager(app.state.engine_manager)
    logger.info("iWork Server started")
    yield
    # ── 关闭 ──
    await engine.dispose()


app = FastAPI(title="iWork Server", version="0.1.0", lifespan=lifespan)

# ── OTel trace context 中间件 ──
from server.observability.middleware import OtelTraceMiddleware
app.add_middleware(OtelTraceMiddleware)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    response: Response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s -> %s  %.0fms",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


# ── /metrics 端点（Prometheus scrape） ──
from server.observability.metrics import get_prometheus_metrics_text

@app.get("/metrics")
async def metrics():
    return PlainTextResponse(get_prometheus_metrics_text().decode())


from server.api.routes import router, router_sessions, router_rules, router_memories, router_admin_audit
from server.api.mcp_routes import router_mcp
from server.api.skill_routes import router_skill
from server.api.agent_routes import router_agents
app.include_router(router_agents)
app.include_router(router_sessions)
app.include_router(router)
app.include_router(router_mcp)
app.include_router(router_skill)
app.include_router(router_rules)
app.include_router(router_memories)
app.include_router(router_admin_audit)


@app.get("/health")
async def health():
    return {"status": "ok"}
