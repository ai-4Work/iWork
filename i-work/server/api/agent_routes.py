"""
Agent 管理 API — Expert / Team 浏览和插件下载。

GET    /experts                   — 浏览所有可用专家（元数据）
GET    /experts/{id}/download     — 下载专家插件包（zip）
GET    /teams                     — 浏览所有可用团队（含 members）
GET    /teams/{id}/download       — 下载团队插件包（zip）
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from server.api.deps import get_current_user

logger = logging.getLogger("iwork.api.agents")

router_agents = APIRouter()


def _get_expert_repo(request: Request):
    return request.app.state.engine_manager._expert_repo


def _get_team_repo(request: Request):
    return request.app.state.engine_manager._team_repo


# ═══════════════════════════════════════════════════════════════
# 专家列表 —— GET /experts
# ═══════════════════════════════════════════════════════════════

@router_agents.get("/experts")
async def list_experts(
    request: Request,
    _: UUID = Depends(get_current_user),
):
    """获取所有可用专家的元数据（不含 system_prompt）。"""
    repo = _get_expert_repo(request)
    if repo is None:
        return {"experts": []}

    experts = await repo.list_all()
    return {
        "experts": [
            {
                "id": str(e.id),
                "name": e.name,
                "display_name": e.display_name,
                "description": e.description,
                "version": e.version,
                "tags": [],
                "config": {
                    "max_turn": e.max_turn or 25,
                    "max_tokens": e.max_tokens,
                    "timeout_seconds": e.timeout_seconds or 300,
                },
            }
            for e in experts
        ]
    }


# ═══════════════════════════════════════════════════════════════
# 下载专家插件 —— GET /experts/{id}/download
# ═══════════════════════════════════════════════════════════════

@router_agents.get("/experts/{expert_id}/download")
async def download_expert(
    expert_id: UUID, request: Request,
    _: UUID = Depends(get_current_user),
):
    """下载专家插件包为 zip 文件。"""
    logger.info("download_expert  request  expert_id=%s", expert_id)

    repo = _get_expert_repo(request)
    if repo is None:
        raise HTTPException(500, "Expert repository not available")

    experts = await repo.list_all()
    expert = next((e for e in experts if e.id == expert_id), None)
    if expert is None:
        raise HTTPException(404, detail={"error": "not_found", "message": "专家不存在"})

    logger.info("download_expert  found  name=%s  version=%s", expert.name, expert.version)

    plugin_path = str(Path(__file__).parent.parent / "plugins" / "experts" / expert.name)
    if not os.path.isdir(plugin_path):
        fallback = expert.plugin_path
        if not os.path.isabs(fallback):
            fallback = str(Path(__file__).parent.parent / fallback)
        if os.path.isdir(fallback):
            plugin_path = fallback
    if not os.path.isdir(plugin_path):
        logger.error("download_expert  plugin_dir_missing  path=%s", plugin_path)
        raise HTTPException(500, detail={"error": "plugin_missing", "message": "插件目录不存在"})

    logger.info("download_expert  plugin_path=%s", plugin_path)

    try:
        zip_buffer = io.BytesIO()
        file_count = 0
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(plugin_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, plugin_path)
                    zf.write(file_path, arcname)
                    file_count += 1
                    logger.debug("download_expert  add_file  %s", arcname)

        zip_buffer.seek(0)
        zip_size = zip_buffer.getbuffer().nbytes
        filename = f"{expert.name}-v{expert.version}.zip"
        logger.info("download_expert  zip_done  files=%d  size=%d  filename=%s", file_count, zip_size, filename)
    except Exception:
        logger.exception("Failed to create zip for expert %s at %s", expert.name, plugin_path)
        raise HTTPException(500, detail={"error": "zip_failed", "message": "插件打包失败"})

    logger.info("download_expert  returning response  size=%d", zip_size)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(zip_size),
        },
    )


# ═══════════════════════════════════════════════════════════════
# 下载团队插件 —— GET /teams/{id}/download
# ═══════════════════════════════════════════════════════════════

@router_agents.get("/teams/{team_id}/download")
async def download_team(
    team_id: UUID, request: Request,
    _: UUID = Depends(get_current_user),
):
    """下载团队插件包为 zip 文件。"""
    logger.info("download_team  request  team_id=%s", team_id)

    repo = _get_team_repo(request)
    if repo is None:
        raise HTTPException(500, "Team repository not available")

    teams = await repo.list_all()
    team = next((t for t in teams if t.id == team_id), None)
    if team is None:
        logger.warning("download_team  not_found  team_id=%s  teams_count=%d  available=%s",
            team_id, len(teams), [(t.id, t.name) for t in teams])
        raise HTTPException(404, detail={"error": "not_found", "message": "团队不存在"})

    logger.info("download_team  found  name=%s  version=%s", team.name, team.version)

    plugin_path = str(Path(__file__).parent.parent / "plugins" / "experts" / team.name)
    if not os.path.isdir(plugin_path):
        fallback = team.plugin_path
        if not os.path.isabs(fallback):
            fallback = str(Path(__file__).parent.parent / fallback)
        if os.path.isdir(fallback):
            plugin_path = fallback
    if not os.path.isdir(plugin_path):
        logger.error("download_team  plugin_dir_missing  path=%s", plugin_path)
        raise HTTPException(500, detail={"error": "plugin_missing", "message": "插件目录不存在"})

    logger.info("download_team  plugin_path=%s", plugin_path)

    try:
        zip_buffer = io.BytesIO()
        file_count = 0
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(plugin_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, plugin_path)
                    zf.write(file_path, arcname)
                    file_count += 1
                    logger.debug("download_team  add_file  %s", arcname)

        zip_buffer.seek(0)
        zip_size = zip_buffer.getbuffer().nbytes
        filename = f"{team.name}-v{team.version}.zip"
        logger.info("download_team  zip_done  files=%d  size=%d  filename=%s", file_count, zip_size, filename)
    except Exception:
        logger.exception("Failed to create zip for team %s at %s", team.name, plugin_path)
        raise HTTPException(500, detail={"error": "zip_failed", "message": "插件打包失败"})

    logger.info("download_team  returning response  size=%d", zip_size)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(zip_size),
        },
    )


# ═══════════════════════════════════════════════════════════════
# 团队列表 —— GET /teams
# ═══════════════════════════════════════════════════════════════

@router_agents.get("/teams")
async def list_teams(
    request: Request,
    _: UUID = Depends(get_current_user),
):
    """获取所有可用团队（含成员详情）。"""
    repo = _get_team_repo(request)
    if repo is None:
        return {"teams": []}

    teams = await repo.list_all()
    return {
        "teams": [
            {
                "id": str(t.id),
                "name": t.name,
                "display_name": t.display_name,
                "description": t.description,
                "version": t.version,
                "members": t.members,
                "skills": t.skills,
                "mcp": t.mcp,
            }
            for t in teams
        ]
    }
