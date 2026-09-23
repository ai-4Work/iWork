"""
MCP 管理 API — Hub 浏览/管理、安装/卸载、自定义 MCP 管理。

GET    /mcp/hub                     — 浏览 Hub 中所有可安装的 MCP
POST   /mcp/hub                     — 添加 MCP 到 Hub
PUT    /mcp/hub/{server_id}          — 更新 Hub MCP
DELETE /mcp/hub/{server_id}          — 删除 Hub MCP
GET    /mcp/installed               — 查看已安装的 MCP（含完整配置）
POST   /mcp/install                 — 安装 Hub 中的 MCP
DELETE /mcp/uninstall/{server_id}   — 卸载已安装的 MCP
POST   /mcp/tools                   — 客户端上报 MCP 工具清单（安装/卸载后）
GET    /mcp/custom                  — 查看自定义 MCP 列表
POST   /mcp/custom                  — 创建自定义 MCP
DELETE /mcp/custom/{server_id}      — 删除自定义 MCP
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Depends

from server.models.message import MCPInstallRequest, MCPCustomCreate
from server.api.deps import get_current_user, get_db, require_permission
from server.storage.postgres import McpHubRepo, UserMcpRepo
from server.observability.audit import audit_log

logger = logging.getLogger("iwork.api.mcp")

router_mcp = APIRouter(prefix="/mcp")


def _get_hub_repo(request: Request) -> McpHubRepo:
    return McpHubRepo(request.app.state.db_session_factory)


def _get_user_mcp_repo(request: Request) -> UserMcpRepo:
    return UserMcpRepo(request.app.state.db_session_factory)


# ═══════════════════════════════════════════════════════════════
# Hub 浏览 —— GET /mcp/hub
# ═══════════════════════════════════════════════════════════════

@router_mcp.get("/hub")
async def list_hub(
    request: Request,
    _: None = Depends(require_permission("system:mcp:list", "client:mcp:config")),
):
    """浏览 Hub。

    两个权限点任一即可：`system:mcp:list` 是将来后台管理页的入口，而客户端
    "MCP 配置"页也要列 Hub 才能装东西，那条入口对应 `client:mcp:config`
    （普通用户只有后者，只挂前者会把客户端功能一起关掉）。
    """
    hub = _get_hub_repo(request)
    return {"servers": await hub.list_all()}


# ═══════════════════════════════════════════════════════════════
# Hub 管理 —— POST /mcp/hub
# ═══════════════════════════════════════════════════════════════

@router_mcp.post("/hub", status_code=201)
async def add_hub(
    body: dict, request: Request,
    _: None = Depends(require_permission("system:mcp:add")),
):
    hub = _get_hub_repo(request)
    result = await hub.create(body)
    return result


# ═══════════════════════════════════════════════════════════════
# Hub 更新 —— PUT /mcp/hub/{server_id}
# ═══════════════════════════════════════════════════════════════

@router_mcp.put("/hub/{server_id}")
async def update_hub(
    server_id: str, body: dict, request: Request,
    _: None = Depends(require_permission("system:mcp:edit")),
):
    hub = _get_hub_repo(request)
    existing = await hub.get_by_id(server_id)
    if existing is None:
        raise HTTPException(404, f"MCP 不存在: {server_id}")
    await hub.update(server_id, body)
    return {"updated": True, "server_id": server_id}


# ═══════════════════════════════════════════════════════════════
# Hub 删除 —— DELETE /mcp/hub/{server_id}
# ═══════════════════════════════════════════════════════════════

@router_mcp.delete("/hub/{server_id}")
async def delete_hub(
    server_id: str, request: Request,
    _: None = Depends(require_permission("system:mcp:remove")),
):
    hub = _get_hub_repo(request)
    ok = await hub.delete(server_id)
    if not ok:
        raise HTTPException(404, f"MCP 不存在: {server_id}")
    return {"deleted": True, "server_id": server_id}


# ═══════════════════════════════════════════════════════════════
# 已安装列表 —— GET /mcp/installed
# ═══════════════════════════════════════════════════════════════

@router_mcp.get("/installed")
async def list_installed(
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    repo = _get_user_mcp_repo(request)
    return {"installed": await repo.get_installed(user_id)}


# ═══════════════════════════════════════════════════════════════
# 安装 Hub MCP —— POST /mcp/install
# ═══════════════════════════════════════════════════════════════

@router_mcp.post("/install")
async def install_mcp(
    body: MCPInstallRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    hub = _get_hub_repo(request)
    entry = await hub.get_by_id(body.server_id)
    if entry is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "server_id 不在 hub 中"})

    user_mcp = _get_user_mcp_repo(request)
    if await user_mcp.is_installed(user_id, body.server_id):
        raise HTTPException(409, detail={"error": "already_installed", "message": "该 MCP 已安装"})

    await user_mcp.install(user_id, entry)

    # 审计：连接 MCP 服务
    await audit_log(
        db,
        action="user.mcp_connected",
        user_id=user_id,
        resource=f"mcp/{body.server_id}",
        detail={
            "server_id": entry.get("server_id", ""),
            "server_name": entry.get("server_name", ""),
            "transport": entry.get("transport", ""),
        },
    )

    logger.info("mcp.installed  server_id=%s", body.server_id)

    # 返回配置供客户端建立连接
    transport = entry.get("transport", "stdio")
    config: dict = {
        "server_id": entry.get("server_id", ""),
        "server_name": entry.get("server_name", ""),
        "transport": transport,
    }
    if transport == "stdio":
        config["command"] = entry.get("command")
        config["args"] = entry.get("args", [])
        config["env"] = entry.get("env", {})
    elif transport in ("streamable-http", "sse"):
        config["url"] = entry.get("url")
    return config


# ═══════════════════════════════════════════════════════════════
# 卸载 MCP —— DELETE /mcp/uninstall/{server_id}
# ═══════════════════════════════════════════════════════════════

@router_mcp.delete("/uninstall/{server_id}")
async def uninstall_mcp(
    server_id: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    repo = _get_user_mcp_repo(request)
    if not await repo.is_installed(user_id, server_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "MCP 未安装"})

    await repo.uninstall(user_id, server_id)
    logger.info("mcp.uninstalled  server_id=%s", server_id)
    return {"success": True, "uninstalled": server_id}


# ═══════════════════════════════════════════════════════════════
# 工具上报 —— POST /mcp/tools
# ═══════════════════════════════════════════════════════════════

@router_mcp.post("/tools")
async def report_mcp_tools(
    body: dict,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """客户端发现 MCP 工具后上报（安装/卸载后）。身份从 access token 取。"""
    server_id = body.get("server_id")
    tools: list[dict] = body.get("tools", [])

    if not server_id:
        raise HTTPException(400, detail={"error": "invalid_request", "message": "server_id 不能为空"})

    repo = _get_user_mcp_repo(request)
    await repo.save_tools(user_id, server_id, tools)

    logger.info(
        "mcp.tools_reported  user=%s  server=%s  tools=%d",
        str(user_id), server_id, len(tools),
    )
    return {"received": True, "tool_count": len(tools)}


# ═══════════════════════════════════════════════════════════════
# 自定义 MCP 列表 —— GET /mcp/custom
# ═══════════════════════════════════════════════════════════════

@router_mcp.get("/custom")
async def list_custom(
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    repo = _get_user_mcp_repo(request)
    return {"custom": await repo.get_custom(user_id)}


# ═══════════════════════════════════════════════════════════════
# 创建自定义 MCP —— POST /mcp/custom
# ═══════════════════════════════════════════════════════════════

@router_mcp.post("/custom", status_code=201)
async def create_custom(
    body: MCPCustomCreate,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    repo = _get_user_mcp_repo(request)
    entry = body.model_dump()
    result = await repo.create_custom(user_id, entry)
    logger.info("mcp.custom.created  server_id=%s  name=%s", result["server_id"], body.server_name)
    return result


# ═══════════════════════════════════════════════════════════════
# 删除自定义 MCP —— DELETE /mcp/custom/{server_id}
# ═══════════════════════════════════════════════════════════════

@router_mcp.delete("/custom/{server_id}")
async def delete_custom(
    server_id: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    repo = _get_user_mcp_repo(request)
    ok = await repo.delete_custom(user_id, server_id)
    if not ok:
        raise HTTPException(404, f"自定义 MCP 不存在: {server_id}")
    logger.info("mcp.custom.deleted  server_id=%s", server_id)
    return {"deleted": True, "server_id": server_id}
