"""
Skill 管理 API — Hub 浏览/管理、安装/卸载、自定义 Skill 管理。

GET    /skills/hub                     — 浏览 Hub 中所有 Skill
POST   /skills/hub                     — 添加 Skill 到 Hub
PUT    /skills/hub/{skill_id}           — 更新 Hub Skill
DELETE /skills/hub/{skill_id}           — 删除 Hub Skill
GET    /skills/installed               — 查看已安装的 Skill
POST   /skills/install                 — 安装 Hub 中的 Skill
DELETE /skills/uninstall/{skill_id}    — 卸载已安装的 Skill
GET    /skills/custom                  — 查看自定义 Skill 列表
POST   /skills/custom                  — 创建自定义 Skill
PUT    /skills/custom/{skill_id}       — 更新自定义 Skill
DELETE /skills/custom/{skill_id}       — 删除自定义 Skill
"""

from __future__ import annotations

import io
import logging
import zipfile
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import Response

from server.models.message import SkillInstallRequest, SkillCustomCreate, SkillCustomUpdate
from server.skills.skill_registry import SkillDefinition
from server.api.deps import get_current_user, get_db
from server.observability.audit import audit_log

logger = logging.getLogger("iwork.api.skills")

router_skill = APIRouter(prefix="/skills")


def _get_skill_registry(request: Request):
    return request.app.state.skill_registry


# ═══════════════════════════════════════════════════════════════
# Hub 浏览 —— GET /skills/hub
# ═══════════════════════════════════════════════════════════════

@router_skill.get("/hub")
async def list_hub(request: Request):
    """获取 Hub 中所有可安装的 Skill。"""
    registry = _get_skill_registry(request)
    skills = await registry.get_hub_skills()
    return {
        "skills": [
            {
                "skill_id": s.skill_id,
                "skill_name": s.skill_name,
                "description": s.description,
                "version": s.version,
                "category": s.category,
                "icon": s.icon,
                "author": s.author,
                "tags": s.tags,
            }
            for s in skills
        ]
    }


# ═══════════════════════════════════════════════════════════════
# Hub 管理 —— POST /skills/hub
# ═══════════════════════════════════════════════════════════════

@router_skill.post("/hub", status_code=201)
async def add_hub_skill(body: dict, request: Request):
    """添加 Skill 到 Hub。"""
    registry = _get_skill_registry(request)
    skill = SkillDefinition(**body)
    skill_id = await registry.create_hub(skill)
    return {"skill_id": skill_id}


# ═══════════════════════════════════════════════════════════════
# Hub 更新 —— PUT /skills/hub/{skill_id}
# ═══════════════════════════════════════════════════════════════

@router_skill.put("/hub/{skill_id}")
async def update_hub_skill(skill_id: str, body: dict, request: Request):
    """更新 Hub 中的 Skill。"""
    registry = _get_skill_registry(request)
    existing = await registry.get_hub_by_id(skill_id)
    if existing is None:
        raise HTTPException(404, f"Skill 不存在: {skill_id}")
    merged = existing.model_copy(update=body)
    await registry.update_hub(skill_id, merged)
    return {"updated": True, "skill_id": skill_id}


# ═══════════════════════════════════════════════════════════════
# Hub 删除 —— DELETE /skills/hub/{skill_id}
# ═══════════════════════════════════════════════════════════════

@router_skill.delete("/hub/{skill_id}")
async def delete_hub_skill(skill_id: str, request: Request):
    """删除 Hub 中的 Skill。"""
    registry = _get_skill_registry(request)
    ok = await registry.delete_hub(skill_id)
    if not ok:
        raise HTTPException(404, f"Skill 不存在: {skill_id}")
    return {"deleted": True, "skill_id": skill_id}


# ═══════════════════════════════════════════════════════════════
# 已安装列表 —— GET /skills/installed
# ═══════════════════════════════════════════════════════════════

@router_skill.get("/installed")
async def list_installed(request: Request, user_id: UUID = Depends(get_current_user)):
    """获取已安装的 Skill 完整信息。"""
    registry = _get_skill_registry(request)
    skills = await registry.get_installed_skills(user_id)
    return {
        "installed": [
            {
                "skill_id": s.skill_id,
                "skill_name": s.skill_name,
                "description": s.description,
                "version": s.version,
                "category": s.category,
                "icon": s.icon,
                "author": s.author,
                "tags": s.tags,
                "folder_path": s.folder_path,
            }
            for s in skills
        ]
    }


# ═══════════════════════════════════════════════════════════════
# 安装 Hub Skill —— POST /skills/install
# ═══════════════════════════════════════════════════════════════

@router_skill.post("/install")
async def install_skill(
    body: SkillInstallRequest,
    request: Request,
    user_id: UUID = Depends(get_current_user),
    db=Depends(get_db),
):
    """安装 Hub 中的 Skill。登记安装 + 返回 skill 目录的 zip 包。"""
    registry = _get_skill_registry(request)

    logger.info("skill.install.request  skill_id=%s  user_id=%s", body.skill_id, str(user_id))

    skill = await registry.get(body.skill_id)
    if skill is None:
        logger.warning("skill.install.not_found  skill_id=%s", body.skill_id)
        raise HTTPException(404, detail={"error": "not_found", "message": "skill_id 不在 hub 中"})

    if await registry.is_installed(user_id, body.skill_id):
        logger.warning("skill.install.already_installed  skill_id=%s", body.skill_id)
        raise HTTPException(409, detail={"error": "already_installed", "message": "该 skill 已安装"})

    await registry.install(user_id, body.skill_id)

    # 审计：安装 Skill
    await audit_log(
        db,
        action="user.skill_installed",
        user_id=user_id,
        resource=f"skill/{body.skill_id}",
        detail={
            "skill_id": body.skill_id,
            "skill_name": skill.skill_name,
            "source": "hub",
        },
    )

    # Zip skill 目录返回给客户端
    if skill.folder_path:
        skill_dir = registry._definitions_dir / skill.folder_path
        if skill_dir.exists():
            buf = io.BytesIO()
            file_count = 0
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in skill_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = str(file_path.relative_to(skill_dir))
                        zf.write(file_path, arcname)
                        file_count += 1
            buf.seek(0)
            filename = f"{skill.skill_name}.zip"
            zip_size = len(buf.getvalue())
            logger.info(
                "skill.install.return_zip  skill_id=%s  folder_path=%s  files=%d  size=%d",
                body.skill_id, skill.folder_path, file_count, zip_size,
            )
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            logger.warning(
                "skill.install.dir_missing  skill_id=%s  folder_path=%s  resolved=%s",
                body.skill_id, skill.folder_path, str(skill_dir),
            )
    else:
        logger.warning(
            "skill.install.no_folder_path  skill_id=%s  skill_name=%s",
            body.skill_id, skill.skill_name,
        )

    logger.info("skill.install.return_json  skill_id=%s", body.skill_id)
    return {"installed": True, "skill_id": body.skill_id}


# ═══════════════════════════════════════════════════════════════
# 卸载 Skill —— DELETE /skills/uninstall/{skill_id}
# ═══════════════════════════════════════════════════════════════

@router_skill.delete("/uninstall/{skill_id}")
async def uninstall_skill(
    skill_id: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """卸载已安装的 Skill（内置 Skill 不可卸载）。"""
    registry = _get_skill_registry(request)
    ok = await registry.uninstall(user_id, skill_id)
    if not ok:
        raise HTTPException(404, detail={"error": "not_found", "message": "未安装该 skill"})
    return {"success": True, "uninstalled": skill_id}


# ═══════════════════════════════════════════════════════════════
# 自定义 Skill 列表 —— GET /skills/custom
# ═══════════════════════════════════════════════════════════════

@router_skill.get("/custom")
async def list_custom(request: Request, user_id: UUID = Depends(get_current_user)):
    """获取自定义 Skill 列表。"""
    registry = _get_skill_registry(request)
    skills = await registry.get_custom_skills(user_id)
    return {"custom": [s.model_dump() for s in skills]}


# ═══════════════════════════════════════════════════════════════
# 创建自定义 Skill —— POST /skills/custom
# ═══════════════════════════════════════════════════════════════

@router_skill.post("/custom", status_code=201)
async def create_custom(
    body: SkillCustomCreate,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """创建自定义 Skill（自动生成 cs 前缀 ID 并自动安装）。"""
    registry = _get_skill_registry(request)
    skill = SkillDefinition(
        skill_id="",
        skill_name=body.skill_name,
        description=body.description,
        version=body.version,
        category=body.category,
        icon=body.icon,
        author=body.author,
        tags=body.tags,
        folder_path=body.folder_path,
    )
    result = await registry.create_custom(user_id, skill)
    return result.model_dump()


# ═══════════════════════════════════════════════════════════════
# 更新自定义 Skill —— PUT /skills/custom/{skill_id}
# ═══════════════════════════════════════════════════════════════

@router_skill.put("/custom/{skill_id}")
async def update_custom(
    skill_id: str,
    body: SkillCustomUpdate,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """更新自定义 Skill。"""
    registry = _get_skill_registry(request)
    existing = await registry.get(skill_id)
    if existing is None or not skill_id.startswith("cs"):
        raise HTTPException(404, f"自定义 Skill 不存在: {skill_id}")

    update_data = body.model_dump(exclude_unset=True)
    merged = existing.model_copy(update=update_data)
    ok = await registry.update_custom(user_id, skill_id, merged)
    if not ok:
        raise HTTPException(404, f"自定义 Skill 不存在: {skill_id}")
    merged.skill_id = skill_id
    return merged.model_dump()


# ═══════════════════════════════════════════════════════════════
# 删除自定义 Skill —— DELETE /skills/custom/{skill_id}
# ═══════════════════════════════════════════════════════════════

@router_skill.delete("/custom/{skill_id}")
async def delete_custom(
    skill_id: str,
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """删除自定义 Skill（同时卸载）。"""
    registry = _get_skill_registry(request)
    ok = await registry.delete_custom(user_id, skill_id)
    if not ok:
        raise HTTPException(404, f"自定义 Skill 不存在: {skill_id}")
    return {"deleted": True, "skill_id": skill_id}
