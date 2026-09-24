"""LLM 模型配置：新建 llm_model 表

阶段：多模型支持（把「只跑一种模型」扩成管理员可维护的模型清单）。

一件事：`llm_model` 表。一条记录 = 一个模型，自带厂商、协议、端点与能力参数。
在此之前这些值是散在 `server/config.py` 的全局 settings 与 `llm/client.py` 的模块
常量里的（max_tokens=20000 / timeout=120 / 无条件 thinking / 全局 context_limit），
多模型下每一个都不成立，所以整组下移到行级。

几个刻意的选择：

1. `api_key_enc` 是 Fernet 密文，不是明文也不是哈希 —— 哈希没法还原出 key 去发请求，
   明文进共享库不可接受（本仓唯一先例 `refresh_tokens.token_hash` 只存 sha256）。
   主密钥留在本地 `.env`（`IWORK_MODEL_API_KEY_ENCRYPTION_KEY`），不随本表落库。
2. `price_*_per_1m` 可空：内网自建模型没有单价，空 = 不计费，区别于"单价为 0"。
3. `max_concurrency` 默认 0 = 不限。公网模型靠厂商配额，内网单机 GPU 必须自己设闸门，
   所以这个字段只在 intranet 场景下才有意义，但两类型共用同一列。
4. 建表后由 `seed_llm_models`（server/db/seed.py）把现有 `.env` 的 DeepSeek 配置
   迁成一行，否则升级后原配置凭空消失、引擎解析不到任何模型。

Revision ID: 023
Revises: 022
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_model",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True,
                  comment="自增主键"),
        sa.Column("model_key", sa.String(length=50), nullable=False,
                  comment="模型唯一标识；聊天下拉与消息的 model 字段存的就是它"),
        sa.Column("display_name", sa.String(length=200), nullable=False,
                  comment="显示名（下拉里给用户看的）"),
        sa.Column("deployment_type", sa.String(length=20), nullable=False,
                  server_default="public",
                  comment="部署类型：public 公网 | intranet 内网自建"),
        sa.Column("protocol", sa.String(length=30), nullable=False,
                  server_default="openai_compatible",
                  comment="协议：openai_compatible | anthropic；决定装配哪个客户端类"),
        sa.Column("vendor", sa.String(length=50), nullable=False, server_default="",
                  comment="厂商，仅用于分组与图标，不参与逻辑"),
        sa.Column("model_api_name", sa.String(length=200), nullable=False,
                  comment="请求体里真正传的模型名（如 vLLM 的 served-model-name）"),
        sa.Column("base_url", sa.String(length=500), nullable=False, server_default="",
                  comment="接口根地址；客户端自行拼 /v1/chat/completions"),
        sa.Column("api_key_enc", sa.Text, nullable=True,
                  comment="API Key 的 Fernet 密文；主密钥在本地 .env（IWORK_MODEL_API_KEY_ENCRYPTION_KEY）"),
        sa.Column("api_key_hint", sa.String(length=50), nullable=False, server_default="",
                  comment="掩码预览（如 sk-ab…a1b2），供管理页显示；不含明文信息"),
        sa.Column("timeout_seconds", sa.Integer, nullable=False, server_default="120",
                  comment="单次请求超时（秒）；内网自建有冷启动与排队，需要调大"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="3",
                  comment="网络异常 / 429 / 502 / 503 的自动重试次数"),
        sa.Column("extra_body", postgresql.JSONB, nullable=False, server_default="{}",
                  comment="透传进请求体的额外字段（如 vLLM 的 chat_template_kwargs）"),
        sa.Column("context_window", sa.Integer, nullable=False, server_default="65536",
                  comment="上下文窗口；上下文压缩按它算触发阈值（自建模型需手填）"),
        sa.Column("max_output_tokens", sa.Integer, nullable=False, server_default="20000",
                  comment="单次输出上限；同时是截断续写的推断阈值"),
        sa.Column("supports_tools", sa.Boolean(), nullable=False,
                  server_default=sa.true(),
                  comment="是否支持 function calling；false 时请求体不带 tools"),
        sa.Column("supports_thinking", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="是否支持思考；false 时请求体不带 thinking 字段（部分端点会 400）"),
        sa.Column("thinking_budget_tokens", sa.Integer, nullable=False,
                  server_default="4096", comment="思考预算 token 数"),
        sa.Column("max_concurrency", sa.Integer, nullable=False, server_default="0",
                  comment="并发上限；0=不限。内网单机 GPU 必须设，否则并发流式会打爆显存"),
        sa.Column("price_input_per_1m", sa.Float, nullable=True,
                  comment="每 1M 输入 token 单价；内网模型留空表示不计费"),
        sa.Column("price_output_per_1m", sa.Float, nullable=True,
                  comment="每 1M 输出 token 单价；内网模型留空表示不计费"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(),
                  comment="是否启用；停用后不出现在下拉里"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0",
                  comment="排序号，决定下拉里的先后"),
        sa.Column("remark", sa.Text, nullable=False, server_default="", comment="备注"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now(), comment="最后更新时间"),
        comment="LLM 模型配置表：管理员维护的可选模型清单，引擎按行解析客户端与能力参数",
    )
    # model_key 既是业务键也是下拉值，唯一；建索引而不是只加 UNIQUE 约束，
    # 是为了让 get_by_key 走索引查找（解析器每轮 LLM 调用都要按它查，虽有缓存兜底）。
    op.create_index("uq_llm_model_key", "llm_model", ["model_key"], unique=True)
    op.create_index("idx_llm_model_enabled", "llm_model", ["enabled", "sort_order"])


def downgrade() -> None:
    op.drop_index("idx_llm_model_enabled", table_name="llm_model")
    op.drop_index("uq_llm_model_key", table_name="llm_model")
    op.drop_table("llm_model")
