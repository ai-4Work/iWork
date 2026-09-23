from pathlib import Path

from pydantic_settings import BaseSettings

# i-work/.env：相对本文件定位，避免 alembic 从 server/ 目录运行时读不到（CWD 相对会解析成 server/.env）
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    # 环境变量加载规则：所有字段从带 IWORK_ 前缀的变量读取（如 IWORK_MAX_TURNS），
    # 并加载 i-work/.env；优先级 进程环境变量 > .env > 字段默认值。
    # extra="allow" 允许存在未声明的额外变量而不报错。
    model_config = {"env_prefix": "IWORK_", "env_file": _ENV_FILE, "extra": "allow"}

    # ── LLM 配置（.env文件中配置） ──
    # 选用哪个 LLM 供应商，决定引擎装配哪个客户端
    # 使用: server/main.py:83-92（启动时选 client）、query_loop.py:186（压缩用 client）、
    #       query_loop.py:342（token 计数器按供应商选算法）
    llm_provider: str = "deepseek"           # anthropic | deepseek
    # Anthropic API Key，provider=anthropic 时必填
    # 使用: server/llm/client.py:74、query_loop.py:189
    anthropic_api_key: str = ""
    # DeepSeek API Key，provider=deepseek 时必填；为空则退回 Fake 客户端
    # 使用: server/llm/client.py:124、main.py:83
    deepseek_api_key: str = ""
    # DeepSeek 的 OpenAI 兼容接口根地址（客户端自行拼 /v1/chat/completions）
    # 使用: server/llm/client.py:126
    deepseek_base_url: str = ""
    # 默认模型名；消息自身未指定 model 时回落到此（仅用于实际选模与日志/指标，不参与权限）
    # 使用: server/llm/client.py:76,125、query_loop.py:944,969,1499,1521,1776
    default_model: str = ""

    # ── 引擎配置 ──
    # 单条消息最多执行多少轮 LLM 循环（工具调用轮、被截断后的续写轮均计入）
    # 使用: server/engine/query_loop.py:1381（主循环 while 条件）
    max_turns: int = 25
    # 单条消息总执行超时（秒），超时则置 error + 推送 code=execution_timeout
    # 使用: server/engine/query_loop.py:1384（每轮开头检查）
    message_timeout_seconds: int = 1800
    # 上下文 token 上限；估算超过该值即触发上下文压缩（DeepSeek v4 默认 64K）
    # 使用: server/engine/query_loop.py:348（构造 ContextCompressor）、
    #       context_compressor.py:469（判断是否超限）
    model_context_limit: int = 65536
    # 压缩上下文时使用的模型（固定走 Anthropic 客户端，与主模型解耦）
    # 使用: server/engine/query_loop.py:190
    compression_model: str = "claude-haiku-4-5"
    # 等待客户端同步应答（Plan 确认 / 客户端工具回投结果）的默认超时（秒）
    # 使用: server/engine/query_loop.py:309（SyncWaiter 默认超时）
    sync_wait_timeout_seconds: int = 300
    # 单会话消息队列最大深度，超出则拒绝入队
    # 使用: server/engine/query_loop.py:1123-1124（抛 QueueFullError）
    max_queue_size: int = 10
    # 会话空闲多久后回收（分钟）
    # 使用: 当前代码未引用（预留），全仓仅此定义处
    session_idle_minutes: int = 30
    # LLM 网络异常 / 限流（429,502,503）类错误的自动重试次数
    # 使用: 当前代码未引用（DeepSeek 客户端用的是本地常量 max_retries=3）
    max_retries: int = 3
    # LLM 输出被 max_tokens 截断后的最大自动续写次数；超出则终止为 error
    # 使用: server/engine/query_loop.py:1846,1850,1858,1862（截断续写分支）
    max_truncation_retries: int = 3
    # 单轮读段 fan-out 的并发闸门：限制同时在飞的读工具回投数（服务端读工具
    # 如 skill / recall / memory_search 会真的读库与跑 TF-IDF）
    # 使用: server/engine/query_loop.py（QueryLoopEngine._read_semaphore）
    max_read_concurrency: int = 8
    # 进程内引擎注册表容量上限；达到上限时驱逐最老的空闲引擎（绝不驱逐在跑的）
    # 使用: server/engine/query_loop.py（EngineManager._evict_idle）
    max_engines: int = 200
    # 同时在跑的子 agent 上限（跨会话计）。超限的 task **直接失败**而不是排队
    # ——对齐 Codex 的"超限拒绝"：排队的子任务挂在父的在途清单里，父会一直等，
    # 而队列深度不可见，用户看到的只是"卡住"。
    # 使用: server/engine/query_loop.py（EngineManager.try_acquire_child_slot）
    max_concurrent_children: int = 4
    # 子 agent 继承父上下文轮数的全局默认档（§9.11.5）：none | all | "<正整数>"。
    # 仅当 agent 的 .md frontmatter 与工具入参都没给 fork_turns 时生效
    # 使用: server/engine/fork.py（normalize_fork_turns）
    fork_turns_default: str = "3"

    # ── L1 原子记忆（docs/chapters/5-记忆模块） ──
    # 总开关：false 时不起调度 sweep、不召回、不注册 memory_search 工具
    l1_enabled: bool = False
    # 抽取用的模型；留空回落到 compression_model（同属"便宜模型"档）
    l1_extraction_model: str = ""
    # 阈值触发：本会话累积的 user 消息条数达到该值就抽一次（doc L1-1.2 触发①）
    l1_turn_threshold: int = 5
    # 空闲兜底：距上次抽取超过该分钟数且仍有未抽消息就抽（触发②）
    l1_idle_minutes: int = 10
    # 每次抽取送进模型的新消息条数上限（doc L1-2.3）
    l1_batch_size: int = 10
    # 上下文用的背景消息条数上限（严禁从中提取记忆）
    l1_background_size: int = 5
    # 每批抽取的记忆条数上限，超出截断（doc L1-2.4 第④步）
    l1_max_memories_per_run: int = 10
    # sweep 轮询间隔（秒）。轮询而非 per-session 计时器：游标落库，重启不丢
    l1_sweep_interval_seconds: int = 60
    # 召回：取几条、相似度阈值、整体超时（doc L1-3.3）
    # 阈值默认 0.15 而不是文档的 0.3：文档那个数是**向量余弦**标定的，当前实现是
    # TF-IDF 余弦，量纲不同。实测相关命中约 0.28、无关约 0.10，0.3 会把全部命中
    # 过滤掉（召回直接失效）。换成向量检索后应回调到 0.3。
    l1_recall_top_k: int = 5
    l1_recall_min_score: float = 0.15
    l1_recall_timeout_seconds: float = 5.0
    # 召回预算：单条记忆字符上限、召回总字符上限（doc L1-3.5）
    l1_recall_per_memory_chars: int = 500
    l1_recall_max_chars: int = 2000
    # memory_search 每轮（消息级）合计最大调用次数（doc L1-3.5）
    l1_search_tool_max_calls: int = 3
    # 去重候选召回：每条新记忆取相似度最高的前 N 条做候选（doc L1-2.5）
    l1_dedup_candidate_top_k: int = 5

    # ── L2 场景记忆（docs/chapters/5-记忆模块 第二部分） ──
    # 总开关：false 时不起 sweep、不注入导航、不注册 scene_read 工具。
    # 依赖 L1（L2 吃 l1_memories 的产出），只开 L2 无意义 —— 装配处会打 warning。
    l2_enabled: bool = False
    # 整合用的模型；留空依次回落到 l1_extraction_model / compression_model
    l2_consolidation_model: str = ""
    # 场景数量上限（doc 默认 15）。**只喂给提示词做三级预警**，工程侧不强制裁剪
    # ——doc L2-2.3 明确"上限靠 LLM 自觉遵守"。
    l2_max_scenes: int = 15
    # 每轮读入的新 L1 记忆条数上限（doc L2-2.2 "每批 20 条"）
    l2_batch_memories: int = 20
    # 连带正文注入的场景个数（按 TF-IDF 相似度选）
    l2_candidate_scenes: int = 5
    # 候选场景正文注入的总字符预算
    l2_candidate_chars: int = 12000
    # 单个场景正文的字符上限（doc L2-4.1 模板）
    l2_scene_max_chars: int = 1500
    # 场景导航注入 system 的字符预算（超预算按热度降序丢尾）
    l2_nav_max_chars: int = 4000
    # scene_read 每轮（消息级）合计最大调用次数
    l2_scene_read_max_calls: int = 3
    # 整合时机（doc L2-1.2）：级联延迟 / 最小间隔 / 保底轮询
    l2_cascade_delay_seconds: int = 10
    l2_min_interval_seconds: int = 900
    l2_max_interval_seconds: int = 3600

    # ── L3 画像记忆（docs/chapters/5-记忆模块 第三部分） ──
    # 总开关：false 时不做生成、不注入画像。**依赖 L2**（L3 吃 l2_scenes 的产出），
    # 只开 L3 无意义 —— 装配处会打 warning。
    l3_enabled: bool = False
    # 生成用的模型；留空依次回落到 l1_extraction_model / compression_model
    l3_generation_model: str = ""
    # 提示词模式（doc L3-4"两套提示词的来源与选择"）：chat = 个人画像 | code = 团队
    # Operating Doctrine。只认这两个值，未知值按 chat。
    l3_mode: str = "chat"
    # P4 阈值：自上次画像以来新增的 L1 记忆数达到该值就重新生成画像（doc L3-1.2）
    l3_memory_threshold: int = 50

    # ── 流缓冲 ──
    # 流式事件的环形缓冲区容量；供客户端断线重连时补齐历史事件
    # 使用: server/engine/query_loop.py:310（构造 StreamBuffer）
    stream_buffer_max_size: int = 500

    # ── 服务器 ──
    # HTTP 监听地址
    # 使用: 当前代码未引用（实际由启动方式/反向代理决定），预留
    host: str = "127.0.0.1"
    # HTTP 监听端口
    # 使用: 当前代码未引用，预留
    port: int = 8000

    # ── 认证（docs/chapters/18-登录认证模块） ──
    # JWT 签名密钥（HS256）。必填，且至少 32 字节 —— 短密钥可被暴力破解。
    # 校验放在启动路径（validate_auth_config），不放在模块 import 处：
    # alembic 也 import 本模块，放这里会让"还没设密钥就迁移不了"。
    jwt_secret: str = ""
    # access token 有效期（分钟）
    auth_access_ttl_minutes: int = 15
    # refresh token 有效期（天）。每次轮换按 now + 此值重算，即滑动续期
    auth_refresh_ttl_days: int = 7
    # 连续登录失败几次触发锁定
    auth_max_failed_logins: int = 5
    # 触发锁定后锁多久（分钟）
    auth_lockout_minutes: int = 15
    # 轮换宽限期（秒）：旧 refresh token 吊销后这段时间内仍接受。
    # 客户端刷新响应丢失后会带旧 token 重试，直接判重放会把人误踢下线
    auth_refresh_grace_seconds: int = 30
    # ── RBAC bootstrap 管理员（docs/chapters/19-权限管理RBAC.md §5.5） ──
    # 首个管理员账号名。挂 admin 角色（短路成全集），不是"谁先注册谁当管理员"
    # 使用: server/db/seed.py（seed_rbac）
    bootstrap_admin_username: str = "admin"
    # 首个管理员密码。**仅在 admin 账号尚不存在时必填**，为空则拒绝启动
    # （已有 admin 时留空不报错，免得重启被卡住）
    # 使用: server/db/seed.py（seed_rbac）
    bootstrap_admin_password: str = ""

    # ── 数据库 ──
    # 异步 SQLAlchemy 连接串（asyncpg 驱动），必填，由 IWORK_DATABASE_URL 提供
    # （本地写在 i-work/.env，参考 .env.example；不入库）。为空则启动直接报错。
    # 使用: server/main.py:64（create_engine）、server/db/engine.py:6、server/alembic/env.py
    database_url: str = ""

    # ── Hooks ──
    # 是否启用 hooks 机制；false 时不加载任何 hook（钩子链为空）
    # 使用: server/engine/query_loop.py:334
    hooks_enabled: bool = True
    # hook 脚本所在目录，启动时扫描其中的脚本注册为钩子
    # 使用: server/engine/query_loop.py:333、server/hooks/config.py
    hooks_script_dir: str = "./hooks"

    # ── 可观测性 ──
    # 是否启用 OpenTelemetry 链路追踪导出（tracing + metrics 两条管线共用）
    # 使用: server/main.py:36,40
    otel_enabled: bool = False
    # OTLP 导出端点（otel_enabled=true 时使用的 collector 地址）
    # 使用: server/main.py:35
    otel_exporter_endpoint: str = ""
    # 上报到 tracing 后端时展示的服务名
    # 使用: server/main.py:34,39
    otel_service_name: str = "iwork-agent"
    # Prometheus 独立监听端口
    # 使用: 当前代码未引用（指标实际走 FastAPI 的 /metrics 路由），预留
    prometheus_port: int = 9090
    # 审计日志保留天数（用于清理过期审计记录）
    # 使用: 当前代码未引用，预留
    audit_retention_days: int = 365


settings = Settings()


def validate_auth_config() -> None:
    """启动时校验认证配置，不满足直接拒绝启动（doc 8.10）。"""
    if len(settings.jwt_secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "IWORK_JWT_SECRET 未配置或不足 32 字节：HS256 密钥至少 256 位随机。"
            "生成方式：python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
