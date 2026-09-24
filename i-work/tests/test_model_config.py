"""模型配置：解析器回落、密钥口径、管理面视图、连接测试。

分两层：

* `ModelResolver` 的行为 —— 用假 repo 喂行，不起库、不连端点。这是引擎每轮都会走的
  路径，回落逻辑错了表现为"选什么都发同一个模型"，前端看不出来。
* 两个管理面入口的取值 —— 直接调路由函数（`_` 那个 `Depends` 有默认值，直调不解析），
  `request.app.state` 用桩对象。刻意不拉 TestClient：那要么起 lifespan 连库，
  要么把 `require_permission` 的闭包逐个 override，都比这里要验的东西重。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.api.model_routes import _validate_common as validate_common
from server.api.model_routes import test_model as run_connection_test
from server.config import settings
from server.db.models import OrmLlmModel
from server.llm.client import FakeLLMClient, OpenAICompatLLMClient
from server.llm.resolver import ModelResolver, ResolvedModel
from server.llm.secrets import mask_key

LOG = "iwork.llm.resolver"


def _row(key: str, **over) -> dict:
    """一行 `to_engine_dict()` 形状的配置。默认是"公网 DeepSeek、无加密密钥"。"""
    row = {
        "model_key": key,
        "display_name": key.upper(),
        "deployment_type": "public",
        "protocol": "openai_compatible",
        "vendor": "",
        "model_api_name": f"{key}-api",
        "base_url": "https://api.deepseek.com",
        "api_key_enc": None,
        "api_key_hint": None,
        "has_api_key": False,
        "timeout_seconds": 120,
        "max_retries": 1,
        "extra_body": {},
        "context_window": 65536,
        "compress_threshold_tokens": None,
        "max_output_tokens": 20000,
        "supports_tools": True,
        "supports_thinking": False,
        "thinking_budget_tokens": 4096,
        "max_concurrency": 0,
        "price_input_per_1m": None,
        "price_output_per_1m": None,
        "enabled": True,
        "remark": "",
    }
    row.update(over)
    return row


class _FakeRepo:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.loads = 0

    async def load_engine_configs(self) -> list[dict]:
        self.loads += 1
        return self._rows


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """把 `.env` 那几项钉成确定值 —— 开发机上的 .env 会把这批用例全带偏。"""
    monkeypatch.setattr(settings, "default_model", "")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setattr(settings, "deepseek_base_url", "")
    monkeypatch.setattr(settings, "model_api_key_encryption_key", "")
    monkeypatch.setattr(settings, "max_retries", 3)
    # 钉死 provider：不然开发机上 .env 写了 anthropic 会走进 `import anthropic` 那条路
    monkeypatch.setattr(settings, "llm_provider", "deepseek")


# ── 回落 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_model_falls_back_and_warns_only_once(monkeypatch, caplog):
    """未知 `model_key` 不硬拒、回落到默认模型，且**只打一次** warning。

    每轮对话都会经过这里，不去重的话一条模型名的笔误会把日志淹掉。
    """
    monkeypatch.setattr(settings, "default_model", "b")
    resolver = ModelResolver(_FakeRepo([_row("a"), _row("b")]))

    with caplog.at_level(logging.WARNING, logger=LOG):
        first = await resolver.resolve("typo-model")
        second = await resolver.resolve("typo-model")

    assert first.model_key == "b"
    assert second.model_key == "b"
    warnings = [r for r in caplog.records if "llm.unknown_model" in r.getMessage()]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_fallback_prefers_env_default_model(monkeypatch):
    """回落目标优先认 `IWORK_DEFAULT_MODEL`，而不是库里最早建的那一行。

    老会话的 `sessions.model` 可能是空串（认证/RBAC 之前建的），那时"默认模型"该是
    管理员指定的那个。
    """
    monkeypatch.setattr(settings, "default_model", "b")
    resolver = ModelResolver(_FakeRepo([_row("a"), _row("b")]))

    assert (await resolver.resolve(None)).model_key == "b"
    assert (await resolver.resolve("")).model_key == "b"


@pytest.mark.asyncio
async def test_disabled_model_is_not_a_candidate(monkeypatch):
    """停用的模型不参与解析 —— 既不是直接命中，也不做回落目标。"""
    monkeypatch.setattr(settings, "default_model", "off")
    resolver = ModelResolver(_FakeRepo([_row("live"), _row("off", enabled=False)]))

    assert (await resolver.resolve("off")).model_key == "live"


@pytest.mark.asyncio
async def test_empty_table_takes_settings_fallback(monkeypatch):
    """全表为空时按 `.env` 的单模型配置装配 —— 保证"升级不改变行为"。"""
    monkeypatch.setattr(settings, "default_model", "env-model")
    monkeypatch.setattr(settings, "deepseek_api_key", "env-key")
    resolver = ModelResolver(_FakeRepo([]))

    resolved = await resolver.resolve(None)
    assert resolved.model_key == "env-model"
    # 改造前对 DeepSeek 是无条件塞 thinking 的，兜底路径必须保持原样
    assert resolved.supports_thinking is True


@pytest.mark.asyncio
async def test_db_error_keeps_last_good_config(monkeypatch):
    """查库失败不把请求打挂，也不把所有请求推去 `.env` 兜底 —— 保留上一次的成功结果。

    注意是**不过 `clear()`** 的情形：`clear()` 是"配置变了、必须重读"的信号，
    它会把上次结果一并丢掉，那时查库失败确实只能回落（那是另一条路径，不该由本用例断言）。
    这里把 TTL 调到 0 逼出一次真实重读。
    """
    import server.llm.resolver as resolver_mod

    monkeypatch.setattr(resolver_mod, "TTL_SECONDS", 0)
    repo = _FakeRepo([_row("a")])
    resolver = ModelResolver(repo)
    assert (await resolver.resolve(None)).model_key == "a"

    async def boom():
        raise RuntimeError("db down")

    repo.load_engine_configs = boom
    assert (await resolver.resolve(None)).model_key == "a"


# ── 密钥口径 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intranet_model_does_not_borrow_the_env_key(monkeypatch):
    """内网模型端点不同、本就不带 key —— 不许被塞上 .env 里 DeepSeek 的密钥。

    这是 `_resolve_key` 三分支里最容易写错的一条：笼统地"没 key 就用 .env 的"会让
    内网请求带着别的厂商的密钥发出去。
    """
    monkeypatch.setattr(settings, "deepseek_api_key", "env-key")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    resolver = ModelResolver(_FakeRepo([
        _row("intranet", base_url="http://10.0.0.8:8000", deployment_type="intranet"),
    ]))

    resolved = await resolver.resolve("intranet")
    assert resolved.client._api_key == ""


@pytest.mark.asyncio
async def test_seed_row_reuses_env_key_when_endpoint_matches(monkeypatch):
    """种子行没有密文、端点又**就是** `.env` 那个 → 继续用 `.env` 的密钥。

    这覆盖"没配 `IWORK_MODEL_API_KEY_ENCRYPTION_KEY` 时密钥只能留在 .env"的场景，否则升级后
    存量配置会因为拿不到 key 全部 401。
    """
    monkeypatch.setattr(settings, "deepseek_api_key", "env-key")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    resolver = ModelResolver(_FakeRepo([_row("seed")]))

    resolved = await resolver.resolve("seed")
    assert resolved.client._api_key == "env-key"


@pytest.mark.asyncio
async def test_undecryptable_cipher_yields_empty_key(monkeypatch):
    """有密文但解不开（换过主密钥）→ 空串，让它到端点报 401。

    悄悄换一个厂商的密钥去试探会更难定位。
    """
    monkeypatch.setattr(settings, "deepseek_api_key", "env-key")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com")
    resolver = ModelResolver(_FakeRepo([
        _row("rot", api_key_enc="gAAAAA-不是合法密文", has_api_key=True),
    ]))

    resolved = await resolver.resolve("rot")
    assert resolved.client._api_key == ""


def test_management_view_never_carries_ciphertext():
    """管理面视图不出密文，密钥只以掩码形式露面。"""
    orm = OrmLlmModel(
        model_key="k", display_name="K", deployment_type="public",
        protocol="openai_compatible", model_api_name="m",
        base_url="https://api.deepseek.com",
        api_key_enc="gAAAAA-secret", api_key_hint="sk-a…9f2c",
    )

    data = orm.to_dict()
    assert "api_key_enc" not in data
    assert data["has_api_key"] is True
    assert data["api_key_hint"] == "sk-a…9f2c"
    # 明文只出现在请求体里一次，任何地方都不该有它
    assert not any(v == "gAAAAA-secret" for v in data.values())


def test_mask_key_hides_short_keys_entirely():
    """短 key 一律回 `…` —— 露 4 头 4 尾对短串来说就是露全部。"""
    assert mask_key("") == ""
    assert mask_key("sk-12345678") == "…"  # 11 位，仍在阈值内
    assert mask_key("sk-1234567890abcdef") == "sk-1…cdef"


# ── 精确查（连接测试用）──────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_for_is_exact_and_allows_disabled_rows():
    """`client_for` 不回落：未知 key 回 None，停用的行照样能取（允许"先停用再调通"）。

    回落在这里是有害的 —— 管理员填错 key 时测试会悄悄打到默认模型上，回一个 ok=True。
    """
    resolver = ModelResolver(_FakeRepo([_row("live"), _row("off", enabled=False)]))

    assert await resolver.client_for("nope") is None
    assert await resolver.client_for("") is None
    assert (await resolver.client_for("live")).model_key == "live"
    assert (await resolver.client_for("off")).model_key == "off"


@pytest.mark.asyncio
async def test_config_cache_is_used_until_cleared():
    """配置有 TTL 缓存，管理页写完之后靠 `clear()` 立刻生效（而不是等 60 秒）。"""
    repo = _FakeRepo([_row("a")])
    resolver = ModelResolver(repo)

    await resolver.resolve(None)
    await resolver.resolve(None)
    assert repo.loads == 1

    repo._rows = [_row("b")]
    assert (await resolver.resolve(None)).model_key == "a"  # 还在缓存里

    resolver.clear()
    assert (await resolver.resolve(None)).model_key == "b"


# ── 连接测试接口 ────────────────────────────────────────────────

class _StubRequest:
    def __init__(self, resolver):
        self.app = SimpleNamespace(state=SimpleNamespace(model_resolver=resolver))


class _ResolverWith:
    """只实现 `client_for` 的桩解析器 —— `test_model` 只调这一个方法。"""

    def __init__(self, resolved: ResolvedModel | None):
        self._resolved = resolved

    async def client_for(self, model_key: str):
        return self._resolved


def _resolved_with(client, api_name: str = "m") -> ResolvedModel:
    return ResolvedModel(
        client=client, model_key="k", display_name="K",
        protocol="openai_compatible", model_api_name=api_name,
        context_window=65536, max_output_tokens=20000, chars_per_token=2.5,
        supports_tools=True, supports_thinking=False, max_concurrency=0,
    )


@pytest.mark.asyncio
async def test_test_endpoint_404s_for_unknown_model():
    """不回落 = 找不到就 404，而不是拿默认模型回一个假的 ok。"""
    with pytest.raises(HTTPException) as exc:
        await run_connection_test("nope", _StubRequest(_ResolverWith(None)))
    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "MODEL_NOT_FOUND"


@pytest.mark.asyncio
async def test_test_endpoint_reports_endpoint_error_verbatim():
    """端点报错时把原文与状态码带回去 —— 端口写错 / 密钥不对都靠这一条定位。"""
    class _Resp:
        status_code = 401
        request = None

        async def aread(self):
            return b'{"error":"invalid api key"}'

    class _Ctx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    class _Http:
        def stream(self, *a, **kw):
            return _Ctx()

    real = OpenAICompatLLMClient(
        api_key="bad", model="m", base_url="http://127.0.0.1:1",
        max_retries=1, display_name="测试模型",
    )
    real._client = _Http()  # 换掉 httpx，不起真实网络；401 由上面的假响应给

    result = await run_connection_test("k", _StubRequest(_ResolverWith(_resolved_with(real))))
    assert result["ok"] is False
    assert result["status"] == 401
    assert result["model_api_name"] == "m"
    assert "invalid api key" in result["detail"]


@pytest.mark.asyncio
async def test_test_endpoint_returns_sample_on_success():
    """成功路径回一小段样例文本，确认真的通到了模型。"""
    fake = FakeLLMClient()  # 默认回「假回复。」+ end_turn
    result = await run_connection_test("k", _StubRequest(_ResolverWith(_resolved_with(fake))))

    assert result["ok"] is True
    assert result["status"] == 200
    assert result["sample"] == "假回复。"
    assert result["stop_reason"] == "end_turn"


# ── 发出去的请求体（"选模真正生效"的回归锚点）──────────────────────
#
# 改造前这里整条链是断的：`stream()` 没有 model 形参、`_model` 在构造时就定死成
# `settings.default_model`，所以**界面选什么都发同一个模型**。下面几条钉住
# "行里的字段 → 请求体"这条链，避免以后又被某个"顺手"的改动接回 settings。

def _body_of(resolved) -> dict:
    """取这个已解析模型**真正会发出去**的请求体（`stream()` 的第一步就是它）。"""
    return resolved.client._build_body(
        messages=[{"role": "user", "content": "hi"}], system="s",
        tools=[{"name": "echo", "description": "", "input_schema": {}}],
        tool_choice="auto", model=None,
    )


@pytest.mark.asyncio
async def test_request_carries_the_rows_api_name_not_the_key():
    """请求体的 `model` 是行里的 `model_api_name`，不是 `model_key`、更不是 settings。"""
    monkeypatch_default = "deepseek-v4-pro"
    resolver = ModelResolver(_FakeRepo([
        _row("my-key", model_api_name="vendor/x-32b-instruct"),
    ]))
    resolved = await resolver.resolve("my-key")
    assert resolved.model_api_name == "vendor/x-32b-instruct"
    assert _body_of(resolved)["model"] == "vendor/x-32b-instruct"
    # 顺带确认没被 settings.default_model 抢走（改造前的症状）
    assert _body_of(resolved)["model"] != monkeypatch_default


@pytest.mark.asyncio
async def test_thinking_field_follows_the_row_flag():
    """`supports_thinking` 决定请求体带不带 `thinking` —— 对不认这个字段的端点是 400 与 200 之别。"""
    resolver = ModelResolver(_FakeRepo([
        _row("thinks", supports_thinking=True),
        _row("plain", supports_thinking=False),
    ]))

    assert (await resolver.resolve("thinks")).supports_thinking is True
    assert _body_of(await resolver.resolve("thinks"))["thinking"] == {"type": "enabled"}
    assert "thinking" not in _body_of(await resolver.resolve("plain"))


@pytest.mark.asyncio
async def test_tools_are_omitted_when_the_row_says_unsupported():
    """`supports_tools=False` 时即便调用方传了 tools 也不发 —— 部分端点见到就 400。"""
    resolver = ModelResolver(_FakeRepo([_row("notools", supports_tools=False)]))
    body = _body_of(await resolver.resolve("notools"))
    assert "tools" not in body and "tool_choice" not in body


@pytest.mark.asyncio
async def test_usage_is_requested_so_trailing_usage_chunk_arrives():
    """主动请求 `include_usage`。不发它各家流式**都不报** token 数 ——
    于是截断只能靠猜（见 `client.py` 的 `llm.unconfirmed_termination` 分支）。"""
    resolver = ModelResolver(_FakeRepo([_row("a")]))
    assert _body_of(await resolver.resolve("a"))["stream_options"] == {
        "include_usage": True
    }


@pytest.mark.asyncio
async def test_context_window_and_estimate_come_from_the_row():
    """窗口与估算系数都按**这一行**走，不再是全局 65536；估算系数按协议分。"""
    resolver = ModelResolver(_FakeRepo([
        _row("tiny", context_window=2048),
        _row("claude", protocol="anthropic", model_api_name="claude-x",
             base_url=""),
    ]))

    tiny = await resolver.resolve("tiny")
    assert tiny.context_window == 2048
    assert tiny.chars_per_token == 2.5          # openai 兼容
    assert (await resolver.resolve("claude")).chars_per_token == 3.5


@pytest.mark.asyncio
async def test_compress_threshold_is_null_unless_the_row_sets_it():
    """`None` = 让引擎按窗口折算；配了就以绝对值透传，不被静默换掉。"""
    resolver = ModelResolver(_FakeRepo([
        _row("auto"),
        _row("pinned", compress_threshold_tokens=1500),
    ]))

    assert (await resolver.resolve("auto")).compress_threshold_tokens is None
    assert (await resolver.resolve("pinned")).compress_threshold_tokens == 1500


@pytest.mark.asyncio
async def test_price_flows_through_and_gates_billability():
    """单价留空 = 不计费（内网自建常态），填了才算可计费。"""
    resolver = ModelResolver(_FakeRepo([
        _row("paid", price_input_per_1m=1.0, price_output_per_1m=2.0),
        _row("intranet", deployment_type="intranet", base_url="http://10.0.0.8:8000"),
    ]))

    paid = await resolver.resolve("paid")
    assert (paid.price_input_per_1m, paid.price_output_per_1m) == (1.0, 2.0)
    assert paid.billable is True
    assert (await resolver.resolve("intranet")).billable is False


# ── 接口地址口径：到域名为止 ─────────────────────────────────────
#
# `kimi-k3` 那次 404 的根因：地址填成 `https://api.moonshot.cn/v1`，而
# `llm/client.py` 是无条件 `f"{base_url}/v1/chat/completions"` —— 拼成
# `/v1/v1/chat/completions`，端点只回 `url.not_found`，从报错里看不出是地址填多了。
# 所以把「不许自带客户端要拼的那一段」钉成写库前的守卫。

def _validate(**over):
    args = dict(
        protocol="openai_compatible", deployment_type="public",
        model_api_name="m", base_url="https://api.moonshot.cn",
        context_window=65536, compress_threshold_tokens=None,
    )
    args.update(over)
    validate_common(**args)


@pytest.mark.parametrize("bad", [
    "https://api.moonshot.cn/v1",
    "https://api.moonshot.cn/v1/",          # 尾部斜杠：守卫自己 rstrip，不能漏
    "https://api.moonshot.cn/chat/completions",
    "https://host/openai/V1",               # 大小写不敏感
])
def test_base_url_must_not_carry_the_part_the_client_appends(bad):
    with pytest.raises(HTTPException) as exc:
        _validate(base_url=bad)
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "INVALID_BASE_URL"


@pytest.mark.parametrize("ok", [
    "https://api.moonshot.cn",
    "http://10.0.0.8:8000",
    "https://host/api",            # 网关挂在自定义前缀下：前缀照填，客户端往后接 /v1/chat/completions
    "https://host/openai/v1beta",  # 不是 /v1 结尾，别误伤
])
def test_base_url_without_that_part_passes(ok):
    _validate(base_url=ok)  # 不抛就是过
