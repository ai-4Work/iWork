from __future__ import annotations
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import Response, PlainTextResponse

from server.config import settings, validate_auth_config
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
    from server.db.seed import (
        seed_hub_data, seed_expert_hub_data, seed_rbac, seed_dept,
        seed_llm_models,
    )
    from server.storage.postgres import (
        PgSessionRepo, PgMessageRepo,
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
    await seed_hub_data(session_factory)
    await seed_expert_hub_data(session_factory)
    # RBAC：字典表对账 + 角色/首个管理员（doc 19-5.5）。要在 auth_service 之前，
    # 否则管理员账号建不出来；也要在 verify_route_refs 之前，先把 sys_permission 补齐。
    await seed_rbac(session_factory)
    # 部门：默认部门 + 存量用户归属回填（doc 19-2.2）。必须在 seed_rbac 之后 ——
    # 首批管理员账号是上一步建的，跑早了它第一次会漏掉回填。
    await seed_dept(session_factory)
    # LLM 模型：把 .env 的单模型配置迁成 llm_model 的首行。要在装配解析器之前 ——
    # 解析器查不到任何模型时只能回落默认，而那时默认也是空的。
    await seed_llm_models(session_factory)

    # ── 2.5 认证 ──
    from server.auth.service import AuthService
    app.state.auth_service = AuthService(session_factory)

    # 路由引用的权限点必须在 catalog 清单里，否则启动就失败 ——
    # 拦的是"清单删了一行、路由还留着引用"这种静默 403（doc 19-4.4）。
    from server.authz.service import verify_route_refs
    verify_route_refs(app)

    # ── 3. PG repos ──
    session_repo = PgSessionRepo(session_factory)
    message_repo = PgMessageRepo(session_factory)

    # ── 4. 模型解析器 ──
    # 不再"按 provider 三选一造一个客户端"：每个模型自己带协议与参数，解析器按
    # `llm_model` 表逐条装配。库里一条启用项都没有时（没跑种子 / 全被停用）它自己
    # 回落到 .env 的单模型配置，行为与改造前一致。
    from server.llm.resolver import ModelResolver
    from server.storage.postgres import LlmModelRepo

    model_repo = LlmModelRepo(session_factory)
    model_resolver = ModelResolver(model_repo)
    app.state.model_resolver = model_resolver
    default_resolved = await model_resolver.resolve()
    # 兜底客户端只喂给 `EngineManager` 的位置参数 —— 引擎拿到 resolver 后自己不查它。
    llm = default_resolved.client
    logger.info(
        "LLM 装配  默认模型=%s  协议=%s  启用模型数=%d",
        default_resolved.model_key or "(未配置)", default_resolved.protocol,
        len(await model_repo.list_enabled_options()),
    )
    # 旧名守卫：改过名的变量会被静默忽略（`extra="allow"`），表现是"明明配了却还是
    # 未配置"，而库里已存的密文会全部解不开（→ 空密钥 → 401）。远端那份 .env 靠手工
    # 同步、最容易漏改，所以在这里点名。
    # 读 os.environ 而不是 settings：模块头的 load_dotenv() 已把 .env 灌了进去，
    # 于是 .env 与进程环境变量两种来源都覆盖得到（pydantic 只把 dotenv 的额外项放进
    # model_extra，键名还是原变量名小写，不如这条直白）。
    if os.environ.get("IWORK_SECRET_KEY"):
        logger.warning(
            "IWORK_SECRET_KEY 已改名为 IWORK_MODEL_API_KEY_ENCRYPTION_KEY，"
            "旧变量不会再被读取；库里已存密文的模型会解不出密钥"
        )
    if not settings.model_api_key_encryption_key:
        logger.warning(
            "IWORK_MODEL_API_KEY_ENCRYPTION_KEY 未配置：经管理页保存模型 API Key 会被拒绝，"
            "密钥只能继续写在 .env 里。生成命令见 .env.example"
        )

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

        # 抽取与去重走"便宜模型"档，与主模型解耦（同压缩的选法）。
        # 一律经解析器：留空或填了个库里没有的模型名都会回落到默认模型并打一次 warning。
        # 改造前这里是 `if provider == "anthropic"` 才另建客户端，于是 provider=deepseek
        # 时 `l1_extraction_model` / `compression_model` 全都不生效（静默用主模型）。
        extraction_model = settings.l1_extraction_model or settings.compression_model
        l1_llm = (await model_resolver.resolve(extraction_model)).client

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
        l2_llm = (await model_resolver.resolve(consolidation_model)).client

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
        l3_llm = (await model_resolver.resolve(generation_model)).client

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
        resolver=model_resolver,
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
    # 关掉解析器持有的 httpx 连接池；不等退休队列的宽限期，直接全关。
    await model_resolver.aclose()
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
from server.api.system_routes import router_system
from server.api.dept_routes import router_dept
from server.api.user_routes import router_user
from server.api.model_routes import router_model, router_models
app.include_router(router_auth)
app.include_router(router_system)
app.include_router(router_dept)
app.include_router(router_user)
app.include_router(router_model)
app.include_router(router_models)
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
