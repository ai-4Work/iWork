"""模型解析器：把 `model_key` 解析成「可直接发请求的客户端 + 能力参数」。

引擎侧**唯一**知道"有哪些模型、各有何参数"的地方。上层（query_loop / 压缩 /
L1-L3 记忆）只调 `resolve()` 拿一个 `ResolvedModel`，不查 `llm_model` 表、
也不读 `settings` 里那几个模型项 —— 这样新增厂商只要插一行配置，代码不用动。

两级缓存都在本类里，因为它们要一起失效：

* **配置缓存**（`llm_model` 全量行，TTL 60s）：每轮 LLM 调用都要解析模型，
  无缓存等于每轮查一次库。管理页写入时显式 `clear()`，TTL 只是兜底
  （多进程部署、或有人绕过 API 直接改库）。
* **客户端缓存**（按 `model_key` 存实例）：客户端构造时会建 httpx 连接池，
  每轮重建会丢掉 keep-alive，长会话下延迟肉眼可见。

`clear()` 不立刻关被淘汰客户端的连接池 —— 清缓存那一刻可能有流式请求正拿着
旧实例在读，立刻 `aclose()` 会把在途流掐断。改为放进退休队列，
`RETIRE_GRACE_SECONDS` 之后才关。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from server.config import settings
from server.llm.client import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_THINKING_BUDGET_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    AnthropicLLMClient,
    FakeLLMClient,
    LLMClient,
    OpenAICompatLLMClient,
)
from server.llm.secrets import decrypt_key_or_none

logger = logging.getLogger("iwork.llm.resolver")

# 配置缓存有效期。管理页写入会显式清缓存，这里只防"绕过 API 改库"。
TTL_SECONDS = 60
# 被淘汰客户端推迟多久才关连接池（给在途流式请求留出收尾时间）。
RETIRE_GRACE_SECONDS = 60

# chars/token 经验值。走到这里说明两件事：这个模型没走校准（`TokenCounter.calibrate`
# 只在拿到真实 usage 后才生效），且没有按模型单独配置的口径。
# 中文密集的 OpenAI 兼容模型（DeepSeek 系）是 2.5，Anthropic 系是 3.5 —— 沿用
# `token_counter.py` 原有的两个常数，不在这里重新发明。
_OPENAI_COMPAT_CHARS_PER_TOKEN = 2.5
_ANTHROPIC_CHARS_PER_TOKEN = 3.5

# 零配置时的兜底客户端在 `_clients` 里的键（不是合法 model_key，不会撞）。
_SETTINGS_FALLBACK_KEY = "\x00settings-fallback"


@dataclass
class ResolvedModel:
    """一次解析的结果：发请求要的客户端，以及引擎算上下文/日志要的能力参数。"""

    client: LLMClient
    model_key: str
    display_name: str
    protocol: str
    model_api_name: str
    context_window: int
    max_output_tokens: int
    chars_per_token: float
    supports_tools: bool
    supports_thinking: bool
    max_concurrency: int
    # 压缩触发线（绝对 token 数）。None = 按 `context_window` × 模式比例折算。
    compress_threshold_tokens: int | None = None
    price_input_per_1m: float | None = None
    price_output_per_1m: float | None = None

    @property
    def billable(self) -> bool:
        """单价留空 = 不计费（内网自建模型的常态，见 `OrmLlmModel` 的列注释）。"""
        return self.price_input_per_1m is not None or self.price_output_per_1m is not None


async def _aclose(client: LLMClient) -> None:
    """关连接池。`FakeLLMClient` 没有 `aclose`，忽略即可。"""
    close = getattr(client, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception as exc:  # noqa: BLE001 —— 关池失败不该影响调用方
        logger.warning("llm.client_close_failed  error=%s", exc)


class ModelResolver:
    def __init__(self, repo) -> None:
        self._repo = repo
        self._configs: list[dict] | None = None
        self._configs_at = 0.0
        self._clients: dict[str, LLMClient] = {}
        self._retired: list[tuple[float, LLMClient]] = []
        # 同一条回落告警只打一次：它出现在每条消息的路径上，不去重会把日志淹掉。
        self._warned: set[str] = set()

    # ── 缓存 ──

    async def _ensure_configs(self, force: bool = False) -> list[dict]:
        if (
            not force
            and self._configs is not None
            and time.monotonic() - self._configs_at < TTL_SECONDS
        ):
            return self._configs
        try:
            self._configs = await self._repo.load_engine_configs()
        except Exception as exc:  # noqa: BLE001 —— 查库失败不能连带把消息处理打挂
            logger.warning(
                "llm.model_config_load_failed  error=%s  → 本次回落 .env 默认模型", exc,
            )
            # 保留上一次的成功结果（可能为空）：DB 抖一下不至于把所有请求都推去兜底。
            self._configs = self._configs or []
        self._configs_at = time.monotonic()
        return self._configs

    def clear(self) -> None:
        """模型配置变更后调用（管理页新增/编辑/删除/启停）。

        同步方法 —— 路由层写完库直接调，不需要 await。被淘汰的客户端实例进退休队列，
        连接池由 `_reap_retired()` 延迟关闭。
        """
        self._configs = None
        self._configs_at = 0.0
        if self._clients:
            now = time.monotonic()
            self._retired.extend((now, c) for c in self._clients.values())
            self._clients.clear()
        self._warned.clear()

    async def _reap_retired(self) -> None:
        if not self._retired:
            return
        now = time.monotonic()
        keep: list[tuple[float, LLMClient]] = []
        for since, client in self._retired:
            if now - since < RETIRE_GRACE_SECONDS:
                keep.append((since, client))
                continue
            await _aclose(client)
        self._retired = keep

    async def aclose(self) -> None:
        """关掉全部连接池（进程关停时调）。"""
        for client in self._clients.values():
            await _aclose(client)
        for _, client in self._retired:
            await _aclose(client)
        self._clients.clear()
        self._retired.clear()

    # ── 解析 ──

    async def resolve(self, model_key: str | None = None) -> ResolvedModel:
        """`model_key` → `ResolvedModel`。**不抛异常**：任何解析不出来都回落。

        回落顺序（`_pick` + `_settings_fallback`）：指定的 key → `IWORK_DEFAULT_MODEL`
        → 最早建的那条启用项（`id` 最小） → `.env` 里的单模型配置 → 假客户端。
        未知模型只打 warning 不硬拒：`tests/` 里大量用 `"m"` / `"x"` 这类任意名字
        构造 Session，入口硬校验会让几十个用例集体失败，而线上更该"能跑"而不是"拒绝"。
        """
        await self._reap_retired()
        configs = await self._ensure_configs()
        entry = self._pick(configs, model_key)
        if entry is None:
            return self._settings_fallback()
        return self._build(entry)

    async def client_for(self, model_key: str) -> ResolvedModel | None:
        """按 `model_key` **精确**取模型，不做任何回落；找不到返回 `None`。

        给"连接测试"用。`resolve()` 的回落在这里是有害的：管理员填错 `model_key`
        或该模型被停用时，测试会悄悄打到默认模型上，回一个 `ok=True` ——
        测了个寂寞，还会让人以为配错了的那条已经通了。

        刻意复用全部行（含停用）：停用的模型也允许测，不然"先停用再调通"这个
        修配置的姿势就断了。
        """
        key = (model_key or "").strip()
        if not key:
            return None
        for entry in await self._ensure_configs():
            if entry["model_key"] == key:
                return self._build(entry)
        return None

    def _pick(self, configs: list[dict], model_key: str | None) -> dict | None:
        enabled = [c for c in configs if c.get("enabled")]
        key = (model_key or "").strip()
        if key:
            for entry in enabled:
                if entry["model_key"] == key:
                    return entry
        fallback = self._preferred_fallback(enabled)
        if fallback is None:
            return None
        if key:
            self._warn_once(
                f"unknown:{key}",
                f"llm.unknown_model  model_key={key}  → 回落 {fallback['model_key']}",
            )
        return fallback

    @staticmethod
    def _preferred_fallback(enabled: list[dict]) -> dict | None:
        """回落目标：`.env` 里那个默认模型在库且启用就用它，否则最早建的那条（`id` 最小）。

        优先认 `IWORK_DEFAULT_MODEL` 是刻意的：老会话的 `sessions.model` 可能是空串
        （认证/RBAC 之前建的），那时"默认模型"应该是管理员指定的那一个，
        而不是碰巧排在最前面的那个。库里的顺序就是建库先后 —— 模型配置没有"排序"
        这个概念了（原先的 `sort_order` 列连界面带逻辑一起拆掉）。
        """
        if not enabled:
            return None
        default_key = (settings.default_model or "").strip()
        if default_key:
            for entry in enabled:
                if entry["model_key"] == default_key:
                    return entry
        return enabled[0]

    def _warn_once(self, tag: str, message: str) -> None:
        if tag in self._warned:
            return
        self._warned.add(tag)
        logger.warning("%s", message)

    # ── 装配 ──

    def _build(self, entry: dict) -> ResolvedModel:
        key = entry["model_key"]
        client = self._clients.get(key)
        if client is None:
            client = self._make_client(entry)
            self._clients[key] = client

        protocol = (entry.get("protocol") or "openai_compatible").strip().lower()
        return ResolvedModel(
            client=client,
            model_key=key,
            display_name=entry.get("display_name") or key,
            protocol=protocol,
            # 客户端构造时已把模型定好，正常路径不需要再传 model。
            model_api_name=entry.get("model_api_name") or key,
            context_window=int(entry.get("context_window") or settings.model_context_limit),
            # 0 与 NULL 同义（路由层已限 ge=1024，这里只是不信任库里的历史值）
            compress_threshold_tokens=int(entry["compress_threshold_tokens"])
            if entry.get("compress_threshold_tokens") else None,
            max_output_tokens=int(
                entry.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS
            ),
            chars_per_token=(
                _ANTHROPIC_CHARS_PER_TOKEN if protocol == "anthropic"
                else _OPENAI_COMPAT_CHARS_PER_TOKEN
            ),
            supports_tools=bool(entry.get("supports_tools", True)),
            supports_thinking=bool(entry.get("supports_thinking", False)),
            max_concurrency=int(entry.get("max_concurrency") or 0),
            price_input_per_1m=entry.get("price_input_per_1m"),
            price_output_per_1m=entry.get("price_output_per_1m"),
        )

    def _make_client(self, entry: dict) -> LLMClient:
        protocol = (entry.get("protocol") or "openai_compatible").strip().lower()
        label = entry.get("display_name") or entry["model_key"]
        api_key = self._resolve_key(entry, protocol)

        if protocol == "anthropic":
            return AnthropicLLMClient(
                api_key=api_key,
                model=entry.get("model_api_name") or None,
                max_output_tokens=int(
                    entry.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS
                ),
                supports_thinking=bool(entry.get("supports_thinking")),
                thinking_budget_tokens=int(
                    entry.get("thinking_budget_tokens") or DEFAULT_THINKING_BUDGET_TOKENS
                ),
                display_name=label,
            )

        return OpenAICompatLLMClient(
            api_key=api_key,
            model=entry.get("model_api_name") or None,
            base_url=entry.get("base_url") or "",
            timeout_seconds=int(entry.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
            max_retries=max(1, int(entry.get("max_retries") or settings.max_retries)),
            max_output_tokens=int(
                entry.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS
            ),
            supports_tools=bool(entry.get("supports_tools", True)),
            supports_thinking=bool(entry.get("supports_thinking", False)),
            extra_body=entry.get("extra_body") or {},
            display_name=label,
        )

    @staticmethod
    def _resolve_key(entry: dict, protocol: str) -> str:
        """取这个模型该用的明文密钥。

        三种情形刻意分开，**不允许笼统地"没有 key 就用 `.env` 的"**：

        1. 有密文且解得开 → 用它。
        2. 有密文但解不开（换过主密钥 / 密文损坏）→ 空串。让它到端点那报 401，
           比悄悄换个厂商的 key 去试探要好定位。
        3. 没密文 → 只有当这一行的端点**就是 `.env` 里那个端点**时才用 `.env` 的 key。
           这覆盖"没配 `IWORK_MODEL_API_KEY_ENCRYPTION_KEY` 时种子行只能把密钥留在 `.env`"的场景
           （见 `db/seed.py` 的 `seed_llm_models`）。内网自建模型端点不同、本就不带 key，
           不会因此被塞上 DeepSeek 的密钥。
        """
        plain = decrypt_key_or_none(
            entry.get("api_key_enc"), settings.model_api_key_encryption_key,
        )
        if plain:
            return plain
        if entry.get("has_api_key"):
            return ""
        if protocol == "anthropic":
            # Anthropic 端点不参数化（客户端不吃 base_url），全进程只有 `.env` 那一个。
            return settings.anthropic_api_key or ""
        base_url = (entry.get("base_url") or "").rstrip("/")
        env_base_url = (settings.deepseek_base_url or "").rstrip("/")
        if base_url and env_base_url and base_url == env_base_url:
            return settings.deepseek_api_key or ""
        return ""

    def _settings_fallback(self) -> ResolvedModel:
        """库里一个启用的模型都没有（或全表为空）时的兜底。

        走到这里通常意味着：还没跑迁移的库、种子跳过了（`IWORK_DEFAULT_MODEL` 为空）、
        或者管理员把全部模型都停用了。此时按 `.env` 的单模型配置装配，
        行为与改造前一致 —— 保证"升级不改变行为"。
        """
        client = self._clients.get(_SETTINGS_FALLBACK_KEY)
        if client is None:
            client = self._make_settings_client()
            self._clients[_SETTINGS_FALLBACK_KEY] = client

        provider = (settings.llm_provider or "deepseek").strip().lower()
        protocol = "anthropic" if provider == "anthropic" else "openai_compatible"
        model_name = (settings.default_model or "").strip()
        return ResolvedModel(
            client=client,
            model_key=model_name,
            display_name=model_name or "默认模型",
            protocol=protocol,
            model_api_name=model_name,
            context_window=settings.model_context_limit,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            chars_per_token=(
                _ANTHROPIC_CHARS_PER_TOKEN if protocol == "anthropic"
                else _OPENAI_COMPAT_CHARS_PER_TOKEN
            ),
            supports_tools=True,
            # 改造前对 DeepSeek 是无条件塞 thinking 的，兜底路径必须保持原样，
            # 否则"停用全部模型"会静默关掉思考能力。
            supports_thinking=True,
            max_concurrency=0,
        )

    @staticmethod
    def _make_settings_client() -> LLMClient:
        provider = (settings.llm_provider or "deepseek").strip().lower()
        model_name = (settings.default_model or "").strip() or None
        if provider == "anthropic" and settings.anthropic_api_key:
            return AnthropicLLMClient(
                api_key=settings.anthropic_api_key,
                model=model_name,
                supports_thinking=True,
                display_name=model_name or "anthropic",
            )
        if settings.deepseek_api_key:
            return OpenAICompatLLMClient(
                api_key=settings.deepseek_api_key,
                model=model_name,
                base_url=settings.deepseek_base_url,
                max_retries=max(1, settings.max_retries),
                supports_thinking=True,
                display_name=model_name or "deepseek",
            )
        # 与 `main.py` 原有的三选一一致：没有任何 key 时用假客户端，让本地能跑起来。
        logger.warning("llm.no_api_key  → 使用 FakeLLMClient")
        return FakeLLMClient()


class StaticModelResolver:
    """单客户端解析器：任何 `model_key` 都解析成同一个客户端。

    给两种场景用：测试（`EngineManager(repo, repo, FakeLLMClient())`），以及没有
    模型仓库的调用路径。存在意义是让上层只认 `resolver.resolve()` 这一种取客户端的
    方式 —— 否则每个调用点都要 `if self.resolver is None: 用 self.llm_client`，
    分支会散到压缩、主调用、L1-L3 各处。

    能力参数取 `settings` 的全局值，等价于改造前的行为。
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        context_window: int | None = None,
        chars_per_token: float | None = None,
        supports_tools: bool = True,
        supports_thinking: bool = True,
    ) -> None:
        provider = (settings.llm_provider or "deepseek").strip().lower()
        self._client = client
        self._protocol = "anthropic" if provider == "anthropic" else "openai_compatible"
        self._context_window = (
            context_window if context_window is not None else settings.model_context_limit
        )
        self._chars_per_token = (
            chars_per_token if chars_per_token is not None
            else (
                _ANTHROPIC_CHARS_PER_TOKEN if self._protocol == "anthropic"
                else _OPENAI_COMPAT_CHARS_PER_TOKEN
            )
        )
        self._supports_tools = supports_tools
        self._supports_thinking = supports_thinking

    async def resolve(self, model_key: str | None = None) -> ResolvedModel:
        key = (model_key or "").strip() or (settings.default_model or "").strip()
        return ResolvedModel(
            client=self._client,
            model_key=key,
            display_name=key or "默认模型",
            protocol=self._protocol,
            model_api_name=key,
            context_window=self._context_window,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            chars_per_token=self._chars_per_token,
            supports_tools=self._supports_tools,
            supports_thinking=self._supports_thinking,
            max_concurrency=0,
        )

    async def client_for(self, model_key: str) -> ResolvedModel | None:
        """恒为 `None`：本类没有按 key 的配置，"精确取某一行"无从谈起。

        返回 `self.resolve()` 是错的 —— 连接测试拿到的是同一个客户端，任何 key
        都"成功"。宁可让测试接口报 404，也不要回一个假阳性。
        """
        return None

    def clear(self) -> None:
        """无缓存可清，存在只是为了与 `ModelResolver` 接口一致。"""

    async def aclose(self) -> None:
        await _aclose(self._client)
