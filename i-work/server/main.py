from __future__ import annotations
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import Response, PlainTextResponse

from server.config import settings, validate_auth_config
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
    if not settings.database_url:
        raise RuntimeError("IWORK_DATABASE_URL 未配置：请在 i-work/.env 中设置（参考 .env.example）")
    # JWT 密钥不足 32 字节直接拒绝启动（doc 8.10）
    validate_auth_config()
    engine, session_factory = create_engine(settings.database_url)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    # ── Audit subscriber: 注入 DB session factory ──
    audit_subscriber.db_factory = session_factory

    # ── 2. 种子数据 ──
    # default-user 只在库里存在、不再被任何接口当作身份使用 —— 它是存量数据的
    # owner，给它设密码后仍可登录查看（见 18-登录认证模块 ch.14）。
    user_repo = UserRepo(session_factory)
    await user_repo.get_or_create_default()
    await seed_hub_data(session_factory)
    await seed_expert_hub_data(session_factory)

    # ── 2.5 认证 ──
    from server.auth.service import AuthService
    app.state.auth_service = AuthService(session_factory)

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

    # ── 5.5 L1 原子记忆（IWORK_L1_ENABLED=true 才装配）──
    memory_recall = None
    l1_task = None
    if settings.l1_enabled:
        from server.memory.dedup import ConflictResolver
        from server.memory.extractor import MemoryExtractor
        from server.memory.l0 import L0Reader
        from server.memory.recall import L1RecallService
        from server.memory.scheduler import L1Scheduler
        from server.storage.postgres import PgL1MemoryRepo

        l1_repo = PgL1MemoryRepo(session_factory)
        memory_recall = L1RecallService(
            l1_repo,
            top_k=settings.l1_recall_top_k,
            min_score=settings.l1_recall_min_score,
            timeout_seconds=settings.l1_recall_timeout_seconds,
            max_chars=settings.l1_recall_max_chars,
            per_memory_chars=settings.l1_recall_per_memory_chars,
        )

        # 抽取与去重走"便宜模型"档，与主模型解耦（同压缩的选法）
        extraction_model = settings.l1_extraction_model or settings.compression_model
        if settings.llm_provider == "anthropic":
            from server.llm.client import AnthropicLLMClient
            l1_llm = AnthropicLLMClient(
                api_key=settings.anthropic_api_key, model=extraction_model,
            )
        else:
            l1_llm = llm

        scheduler = L1Scheduler(
            session_repo=session_repo,
            memory_repo=l1_repo,
            reader=L0Reader(
                ConversationHistoryRepo(session_factory),
                batch_size=settings.l1_batch_size,
                background_size=settings.l1_background_size,
            ),
            extractor=MemoryExtractor(
                l1_llm, max_per_run=settings.l1_max_memories_per_run,
            ),
            resolver=ConflictResolver(
                l1_llm,
                top_k=settings.l1_dedup_candidate_top_k,
                min_score=settings.l1_recall_min_score,
            ),
            interval_seconds=settings.l1_sweep_interval_seconds,
            turn_threshold=settings.l1_turn_threshold,
            idle_minutes=settings.l1_idle_minutes,
            batch_size=settings.l1_batch_size,
        )
        app.state.l1_scheduler = scheduler
        l1_task = asyncio.create_task(scheduler.run_forever())
        logger.info("L1 记忆已启用  抽取模型=%s", extraction_model)

    # ── 5.6 L2 场景记忆（IWORK_L2_ENABLED=true 且 L1 已开才装配）──
    scene_recall = None
    l2_task = None
    if settings.l2_enabled and not settings.l1_enabled:
        logger.warning("L2 场景记忆依赖 L1 的产出，L1 未启用 → L2 本次不装配")
    elif settings.l2_enabled:
        from server.memory.l2.consolidator import MemoryConsolidator
        from server.memory.l2.reader import L2Reader
        from server.memory.l2.recall import L2RecallService
        from server.memory.l2.scheduler import L2Scheduler
        from server.storage.postgres import PgL2SceneRepo

        l2_repo = PgL2SceneRepo(session_factory)
        scene_recall = L2RecallService(
            l2_repo,
            nav_max_chars=settings.l2_nav_max_chars,
            timeout_seconds=settings.l1_recall_timeout_seconds,
        )

        # 整合同样走"便宜模型"档：l2 专用 → L1 抽取 → 压缩
        consolidation_model = (
            settings.l2_consolidation_model
            or settings.l1_extraction_model
            or settings.compression_model
        )
        if settings.llm_provider == "anthropic":
            from server.llm.client import AnthropicLLMClient
            l2_llm = AnthropicLLMClient(
                api_key=settings.anthropic_api_key, model=consolidation_model,
            )
        else:
            l2_llm = llm

        l2_scheduler = L2Scheduler(
            memory_repo=l1_repo,
            scene_repo=l2_repo,
            reader=L2Reader(l1_repo, l2_repo),
            consolidator=MemoryConsolidator(
                l2_llm,
                max_scenes=settings.l2_max_scenes,
                scene_max_chars=settings.l2_scene_max_chars,
            ),
            interval_seconds=settings.l1_sweep_interval_seconds,
            batch_memories=settings.l2_batch_memories,
            candidate_scenes=settings.l2_candidate_scenes,
            candidate_chars=settings.l2_candidate_chars,
            max_scenes=settings.l2_max_scenes,
            cascade_delay_seconds=settings.l2_cascade_delay_seconds,
            min_interval_seconds=settings.l2_min_interval_seconds,
            max_interval_seconds=settings.l2_max_interval_seconds,
        )
        app.state.l2_scheduler = l2_scheduler
        l2_task = asyncio.create_task(l2_scheduler.run_forever())
        logger.info("L2 场景记忆已启用  整合模型=%s", consolidation_model)

    # ── 5.7 L3 画像记忆（IWORK_L3_ENABLED=true 且 L2 已开才装配）──
    persona_recall = None
    if settings.l3_enabled and not settings.l2_enabled:
        logger.warning("L3 画像记忆依赖 L2 的场景产出，L2 未启用 → L3 本次不装配")
    elif settings.l3_enabled:
        from server.memory.l3.generator import PersonaGenerator
        from server.memory.l3.reader import L3Reader
        from server.memory.l3.recall import L3RecallService
        from server.memory.l3.scheduler import L3Scheduler
        from server.memory.l3.types import MODES, MODE_CHAT
        from server.storage.postgres import PgL3PersonaRepo

        l3_repo = PgL3PersonaRepo(session_factory)
        persona_recall = L3RecallService(
            l3_repo, timeout_seconds=settings.l1_recall_timeout_seconds,
        )

        # 生成同样走"便宜模型"档：l3 专用 → L1 抽取 → 压缩
        generation_model = (
            settings.l3_generation_model
            or settings.l1_extraction_model
            or settings.compression_model
        )
        if settings.llm_provider == "anthropic":
            from server.llm.client import AnthropicLLMClient
            l3_llm = AnthropicLLMClient(
                api_key=settings.anthropic_api_key, model=generation_model,
            )
        else:
            l3_llm = llm

        l3_mode = settings.l3_mode if settings.l3_mode in MODES else MODE_CHAT
        l3_scheduler = L3Scheduler(
            persona_repo=l3_repo,
            scene_repo=l2_repo,
            memory_repo=l1_repo,
            reader=L3Reader(l1_repo, l2_repo, l3_repo),
            generator=PersonaGenerator(l3_llm),
            threshold=settings.l3_memory_threshold,
            mode=l3_mode,
        )
        # L3 没有独立定时任务：挂在 L2 的 sweep 上，L2 整合完一个作用域就评估一次（doc L3-1.2）
        l2_scheduler.persona_scheduler = l3_scheduler
        app.state.l3_scheduler = l3_scheduler
        logger.info("L3 画像记忆已启用  模式=%s  生成模型=%s", l3_mode, generation_model)

    app.state.engine_manager = EngineManager(
        session_repo, message_repo, llm,
        session_factory=session_factory,
        skill_registry=skill_registry,
        user_mcp_repo=user_mcp_repo,
        event_bus=event_bus,
        memory_recall=memory_recall,
        scene_recall=scene_recall,
        persona_recall=persona_recall,
    )
    stream_subscriber.set_engine_manager(app.state.engine_manager)
    logger.info("iWork Server started")
    yield
    # ── 关闭 ──
    for task in (l1_task, l2_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
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


from server.api.routes import (
    router, router_sessions, router_rules, router_l1, router_l2, router_l3,
    router_admin_audit,
)
from server.api.mcp_routes import router_mcp
from server.api.skill_routes import router_skill
from server.api.agent_routes import router_agents
from server.api.auth_routes import router_auth
app.include_router(router_auth)
app.include_router(router_agents)
app.include_router(router_sessions)
app.include_router(router)
app.include_router(router_mcp)
app.include_router(router_skill)
app.include_router(router_rules)
app.include_router(router_l1)
app.include_router(router_l2)
app.include_router(router_l3)
app.include_router(router_admin_audit)


@app.get("/health")
async def health():
    return {"status": "ok"}
