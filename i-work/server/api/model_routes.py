"""LLM 模型配置接口（docs/chapters/19-权限管理RBAC.md 的模型配置一节）。

两套接口刻意分在两个 router 上，权限口径完全不同：

* **管理面** `router_model`（`/system/model/*`）—— 管理员维护模型清单。写接口要
  `system:model:*`，读接口要 `system:model:list`。
* **用户面** `router_models`（`/models`）—— 聊天下拉的数据源，**只要求登录**。
  刻意不挂权限点：没有它谁都拉不到模型清单、也就发不出消息，把"能不能聊天"绑在
  一个可被取消的权限点上只会造出一类"登录了但不能用"的账号。

写入路径一律四步，顺序不能变：`校验 → repo 写 → 清解析器缓存 → audit_log + commit`。
**清缓存必须紧跟 repo 写**：解析器把模型配置缓存 60 秒，不清的话管理员改完参数
最久要等一分钟才生效，看起来就是"保存没反应"。
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server.api.deps import get_current_user, get_db, require_permission
from server.config import settings
from server.llm.secrets import SecretKeyError, encrypt_key, mask_key
from server.observability.audit import audit_log
from server.storage.postgres import LlmModelRepo

router_model = APIRouter(prefix="/system/model", tags=["system"])
router_models = APIRouter(tags=["models"])

# `model_key` 会同时出现在 URL 路径、`sessions.model`、`messages.model` 里，
# 所以只允许不折腾 URL 的字符。改它等于让存量会话解析不到模型（见 LlmModelRepo）。
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,49}$")
_PROTOCOLS = ("openai_compatible", "anthropic")
_DEPLOYMENT_TYPES = ("public", "intranet")


class ModelCreateRequest(BaseModel):
    model_key: str
    display_name: str
    deployment_type: str = "public"
    protocol: str = "openai_compatible"
    vendor: str = ""
    model_api_name: str
    base_url: str = ""
    # 明文只在请求体里出现一次；入库前立即加密，响应永不回传（只回掩码）。
    api_key: str | None = None
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_retries: int = Field(default=3, ge=0, le=10)
    extra_body: dict = Field(default_factory=dict)
    context_window: int = Field(default=65536, ge=1024)
    # 留空 = 按 context_window 折算触发线（build 80% / ask 55%），与改造前一致
    compress_threshold_tokens: int | None = Field(default=None, ge=1024)
    max_output_tokens: int = Field(default=20000, ge=1)
    supports_tools: bool = True
    supports_thinking: bool = False
    thinking_budget_tokens: int = Field(default=4096, ge=0)
    max_concurrency: int = Field(default=0, ge=0)
    price_input_per_1m: float | None = Field(default=None, ge=0)
    price_output_per_1m: float | None = Field(default=None, ge=0)
    enabled: bool = True
    remark: str = ""


class ModelUpdateRequest(BaseModel):
    """局部更新：只改传了字段的那些列。

    `api_key` **缺省 / null / 空串都表示"不改"** —— 编辑弹窗里 key 只显示掩码，
    用户不动那个框就不该把库里的密钥抹掉。要清空密钥请停用整个模型。
    """

    display_name: str | None = None
    deployment_type: str | None = None
    protocol: str | None = None
    vendor: str | None = None
    model_api_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    extra_body: dict | None = None
    context_window: int | None = Field(default=None, ge=1024)
    # 显式传 null = 改回"按窗口折算"；不传 = 不改
    compress_threshold_tokens: int | None = Field(default=None, ge=1024)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_tools: bool | None = None
    supports_thinking: bool | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=0)
    price_input_per_1m: float | None = Field(default=None, ge=0)
    price_output_per_1m: float | None = Field(default=None, ge=0)
    enabled: bool | None = None
    remark: str | None = None


def _repo(request: Request) -> LlmModelRepo:
    return LlmModelRepo(request.app.state.db_session_factory)


def _resolver(request: Request):
    return request.app.state.model_resolver


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"error": code, "message": message})


def _validate_common(
    *, protocol: str, deployment_type: str, model_api_name: str, base_url: str,
    context_window: int, compress_threshold_tokens: int | None = None,
) -> None:
    if protocol not in _PROTOCOLS:
        raise _bad("INVALID_PROTOCOL", f"协议只能是 {' / '.join(_PROTOCOLS)}")
    if deployment_type not in _DEPLOYMENT_TYPES:
        raise _bad(
            "INVALID_DEPLOYMENT_TYPE",
            f"部署类型只能是 {' / '.join(_DEPLOYMENT_TYPES)}",
        )
    if not model_api_name.strip():
        raise _bad("INVALID_MODEL_API_NAME", "模型名不能为空")
    if protocol == "openai_compatible" and not base_url.strip():
        # 空 base_url 会被拼成 `/v1/chat/completions` 这种相对地址，请求根本发不出去。
        # 公网模型（如 DeepSeek）在表单里预填端点，内网模型本来就必须手填。
        raise _bad("BASE_URL_REQUIRED", "OpenAI 兼容协议必须填写接口地址")
    # 客户端会自行拼 `/v1/chat/completions`（见 llm/client.py）。地址里再带一份就是
    # `/v1/v1/chat/completions` —— 端点只会回 404，而且从报错里看不出是地址填多了。
    # 自己 rstrip：创建路径传进来的是原始值，尾部斜杠要到写库前才被剥掉。
    if re.search(r"/(v1|chat/completions)$", base_url.strip().rstrip("/"), re.IGNORECASE):
        raise _bad(
            "INVALID_BASE_URL",
            "接口地址只要填到域名，客户端会自行拼 /v1/chat/completions；"
            "请去掉末尾的 /v1 或 /chat/completions",
        )
    if compress_threshold_tokens is not None and compress_threshold_tokens > context_window:
        # 触发线在窗口之外 = 还没轮到压缩，上下文已经超窗被端点拒了。
        raise _bad(
            "COMPRESS_THRESHOLD_EXCEEDS_WINDOW",
            f"压缩阈值（{compress_threshold_tokens}）不能超过上下文窗口（{context_window}）",
        )


def _encrypt_or_reject(api_key: str) -> dict:
    """明文 key → 待写库的两列。主密钥没配就明确拒绝，不静默丢密钥。

    静默丢弃是最坏的结果：管理员以为存上了，实际每次请求都 401。
    """
    try:
        cipher = encrypt_key(api_key, settings.model_api_key_encryption_key)
    except SecretKeyError as exc:
        raise _bad(
            "SECRET_KEY_NOT_CONFIGURED",
            f"{exc}（未配置时密钥只能继续写在 .env 里）",
        )
    return {"api_key_enc": cipher or None, "api_key_hint": mask_key(api_key)}


# ═══════════════════════════════════════════════════════════════
# 用户面：聊天下拉的候选（只要求登录）
# ═══════════════════════════════════════════════════════════════

@router_models.get("/models")
async def list_model_options(request: Request, _: UUID = Depends(get_current_user)):
    """启用中的模型，只回下拉需要的三个字段（不回端点、上下文窗口、单价）。"""
    return {"models": await _repo(request).list_enabled_options()}


# ═══════════════════════════════════════════════════════════════
# 管理面：读
# ═══════════════════════════════════════════════════════════════

@router_model.get("/list")
async def list_models(
    request: Request,
    _: None = Depends(require_permission("system:model:list")),
):
    """全量模型（含停用）。`api_key_enc` 不在这里的返回值里，只出 `has_api_key`。"""
    return {"models": await _repo(request).list_all()}


# ═══════════════════════════════════════════════════════════════
# 管理面：写
# ═══════════════════════════════════════════════════════════════

@router_model.post("")
async def create_model(
    body: ModelCreateRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:model:add")),
):
    key = body.model_key.strip()
    if not _KEY_RE.match(key):
        raise _bad(
            "INVALID_MODEL_KEY",
            "模型标识只能由字母数字与 . _ : - 组成，且以字母数字开头（最长 50 字符）",
        )
    _validate_common(
        protocol=body.protocol, deployment_type=body.deployment_type,
        model_api_name=body.model_api_name, base_url=body.base_url,
        context_window=body.context_window,
        compress_threshold_tokens=body.compress_threshold_tokens,
    )
    repo = _repo(request)
    if await repo.get_by_key(key) is not None:
        raise _bad("MODEL_KEY_EXISTS", f"模型标识 {key} 已存在", status=409)

    entry = body.model_dump(exclude={"model_key", "api_key"})
    entry["display_name"] = body.display_name.strip()
    entry["base_url"] = body.base_url.strip().rstrip("/")
    if not entry["display_name"]:
        raise _bad("INVALID_DISPLAY_NAME", "显示名不能为空")
    if body.api_key:
        entry.update(_encrypt_or_reject(body.api_key))

    await repo.create(key, entry)
    _resolver(request).clear()
    await audit_log(
        db,
        action="admin.model_created",
        user_id=user_id,
        resource=f"llm_model/{key}",
        detail={
            "model_key": key,
            "protocol": body.protocol,
            "deployment_type": body.deployment_type,
            "base_url": entry["base_url"],
            "has_api_key": bool(body.api_key),
        },
    )
    await db.commit()
    return {"model_key": key}


@router_model.put("/{model_key}")
async def update_model(
    model_key: str,
    body: ModelUpdateRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:model:edit")),
):
    repo = _repo(request)
    current = await repo.get_by_key(model_key)
    if current is None:
        raise _bad("MODEL_NOT_FOUND", "模型不存在", status=404)

    entry = body.model_dump(exclude={"api_key"}, exclude_unset=True)
    if "display_name" in entry:
        entry["display_name"] = (body.display_name or "").strip()
        if not entry["display_name"]:
            raise _bad("INVALID_DISPLAY_NAME", "显示名不能为空")
    if "base_url" in entry and body.base_url is not None:
        entry["base_url"] = body.base_url.strip().rstrip("/")

    # 校验用"改完之后"的样子：只改协议不改端点这类局部更新，也要能拦住非法组合。
    _validate_common(
        protocol=entry.get("protocol", current["protocol"]),
        deployment_type=entry.get("deployment_type", current["deployment_type"]),
        model_api_name=entry.get("model_api_name", current["model_api_name"]),
        base_url=entry.get("base_url", current["base_url"]),
        context_window=entry.get("context_window", current["context_window"]),
        compress_threshold_tokens=entry.get(
            "compress_threshold_tokens", current["compress_threshold_tokens"]
        ),
    )
    if body.api_key:
        entry.update(_encrypt_or_reject(body.api_key))

    await repo.update(model_key, entry)
    _resolver(request).clear()
    await audit_log(
        db,
        action="admin.model_updated",
        user_id=user_id,
        resource=f"llm_model/{model_key}",
        detail={"model_key": model_key, "fields": sorted(entry.keys())},
    )
    await db.commit()
    return {"updated": True}


@router_model.delete("/{model_key}")
async def delete_model(
    model_key: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
    _: None = Depends(require_permission("system:model:remove")),
):
    """删模型。存量会话里残留的旧 `model_key` 由解析器回落处理，不在这里拦。"""
    repo = _repo(request)
    if not await repo.delete(model_key):
        raise _bad("MODEL_NOT_FOUND", "模型不存在", status=404)
    _resolver(request).clear()
    await audit_log(
        db,
        action="admin.model_removed",
        user_id=user_id,
        resource=f"llm_model/{model_key}",
        detail={"model_key": model_key},
    )
    await db.commit()
    return {"deleted": True}


@router_model.post("/{model_key}/test")
async def test_model(
    model_key: str,
    request: Request,
    _: None = Depends(require_permission("system:model:test")),
):
    """连接测试：拿这一行的配置**真发一次**最小请求。

    刻意不用 `resolve()`：它会回落，实测打到默认模型上、测了个寂寞。这里按
    `model_key` 精确取（`client_for`），停用的模型也允许测。

    走的是引擎同一条代码路径（同一个客户端类、同样的请求体拼装），所以报错原文
    就直接是运行时那份 —— 端口写错、密钥不对、端点不认 `thinking` 字段，都能当场看出来。
    """
    resolved = await _resolver(request).client_for(model_key)
    if resolved is None:
        raise _bad("MODEL_NOT_FOUND", "模型不存在", status=404)

    import httpx

    sample: list[str] = []
    stop_reason: str | None = None
    try:
        async for chunk in resolved.client.stream(
            messages=[{"role": "user", "content": "只回复两个字：OK"}],
            system="这是一次连接测试。",
        ):
            if chunk.type in ("text", "thinking") and chunk.delta:
                sample.append(chunk.delta)
                if len("".join(sample)) >= 20:
                    break
            if chunk.type == "end_turn":
                stop_reason = chunk.stop_reason
                break
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "status": exc.response.status_code,
            "model_api_name": resolved.model_api_name,
            "detail": str(exc)[:1000],
        }
    except Exception as exc:  # noqa: BLE001 —— 测试接口的意义就是把原文带回去
        return {
            "ok": False,
            "status": None,
            "model_api_name": resolved.model_api_name,
            "detail": f"{type(exc).__name__}: {exc}"[:1000],
        }
    return {
        "ok": True,
        "status": 200,
        "model_api_name": resolved.model_api_name,
        "sample": "".join(sample)[:100],
        "stop_reason": stop_reason,
    }
