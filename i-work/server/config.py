from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 环境变量加载规则：所有字段从带 IWORK_ 前缀的变量读取（如 IWORK_MAX_TURNS），
    # 并加载当前目录 .env；extra="allow" 允许存在未声明的额外变量而不报错。
    model_config = {"env_prefix": "IWORK_", "env_file": ".env", "extra": "allow"}

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
    # 如 skill / recall / load_memory 会真的读库与跑 TF-IDF）
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

    # ── 数据库 ──
    # 异步 SQLAlchemy 连接串（asyncpg 驱动），启动时建连接池
    # 使用: server/main.py:64（create_engine）、server/db/engine.py:6
    database_url: str = "postgresql+asyncpg://bmsmart:Bmzt2016_postgres@10.0.60.165:5432/agent_test"

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
