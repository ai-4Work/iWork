"""
Skill 注册中心 — DB-backed 注册表，管理 Skill 的加载、安装/卸载。

Skill 采用 tool-based 按需加载模式：
  - 所有已安装 Skill 的 name + description 以 <available_skills> XML 注入 system prompt
  - LLM 根据任务需要主动调用 skill 工具加载特定 Skill 的 SKILL.md
  - 脚本/示例通过现有工具（read_file/bash）在后续流程中按需读取

与 MCP 的本质区别：
  - MCP = 进程管理运行时（spawn 子进程、JSON-RPC、工具发现、重连）
  - Skill = 文件夹 + SKILL.md 核心指令文件，按需通过 skill 工具加载
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger("iwork.skills")


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

class SkillDefinition(BaseModel):
    """Skill 的元数据定义。核心指令在文件夹的 SKILL.md 中。"""
    skill_id: str
    skill_name: str
    description: str
    folder_path: str = ""       # Skill 文件夹路径（相对于 definitions_dir）
    version: str = "1.0.0"
    category: str = "通用"
    icon: str = "⚡"
    author: str = ""
    tags: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 内置 Skill 定义（始终可用，不需要安装）
# ═══════════════════════════════════════════════════════════════

BUILTIN_SKILLS: list[SkillDefinition] = [
    SkillDefinition(
        skill_id="bi1",
        skill_name="office-assistant",
        description="日常办公场景的默认技能，覆盖文档撰写、邮件、表格处理等",
        folder_path="",  # 内置 skill 无文件夹
        version="1.0.0",
        category="内置",
        icon="📋",
        author="iWork Team",
        tags=["office", "document", "email"],
    ),
    SkillDefinition(
        skill_id="bi2",
        skill_name="programming-assistant",
        description="编程场景的默认技能，覆盖代码编写、调试、重构等",
        folder_path="",  # 内置 skill 无文件夹
        version="1.0.0",
        category="内置",
        icon="💻",
        author="iWork Team",
        tags=["code", "debug", "refactor"],
    ),
]

# 内置 skill 的核心指令（从旧 prompt 迁移）
_BUILTIN_SKILL_MD: dict[str, str] = {
    "bi1": (
        "## Skill: 办公助手\n"
        "你是办公效率专家。处理办公任务时请遵循：\n"
        "1. 文档撰写：结构清晰、语言得体、格式规范\n"
        "2. 邮件处理：主题明确、礼貌专业、重点突出\n"
        "3. 表格处理：数据准确、公式正确、格式美观\n"
        "4. 输出优先使用 Markdown 表格和列表组织信息\n"
    ),
    "bi2": (
        "## Skill: 编程助手\n"
        "你是资深软件工程师。编写代码时请遵循：\n"
        "1. 优先使用标准库和成熟框架，避免引入不必要的依赖\n"
        "2. 代码风格遵循语言社区最佳实践（PEP8、Effective Go 等）\n"
        "3. 安全性优先：防范 OWASP Top 10 漏洞\n"
        "4. 错误处理完善，边界条件覆盖\n"
        "5. 代码注释精简，解释 WHY 而非 WHAT\n"
    ),
}


# ═══════════════════════════════════════════════════════════════
# skill 工具定义（常量）
# ═══════════════════════════════════════════════════════════════

SKILL_TOOL_DEFINITION = {
    "name": "skill",
    "description": (
        "当任务匹配 <available_skills> 中列出的 Skill 时，加载对应 Skill。"
        "name 参数必须与可用 Skill 列表中的名称一致。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name to load, must match one from <available_skills>",
            }
        },
        "required": ["name"],
    },
}


# ═══════════════════════════════════════════════════════════════
# SkillRegistry
# ═══════════════════════════════════════════════════════════════

class SkillRegistry:
    """DB-backed Skill 注册表，管理 Hub 目录 + 用户安装状态。

    不再使用 JSON 文件存储状态，全部走 PostgreSQL。
    SKILL.md 文件仍从文件系统按需读取。
    """

    def __init__(
        self,
        skill_hub_repo=None,   # SkillHubRepo
        user_skill_repo=None,  # UserSkillRepo
        definitions_dir: str | None = None,
    ):
        from server.storage.postgres import SkillHubRepo, UserSkillRepo
        self._hub_repo: SkillHubRepo = skill_hub_repo
        self._user_repo: UserSkillRepo = user_skill_repo

        base = Path(__file__).parent.parent
        self._definitions_dir = Path(definitions_dir) if definitions_dir else base / "skills" / "definitions"

    # ── 查询 ─────────────────────────────────────────────────

    async def get(self, skill_id: str) -> SkillDefinition | None:
        """按 ID 查找 Skill（查 Hub → 查内置）。"""
        # 先查 Hub DB
        if self._hub_repo:
            skill = await self._hub_repo.get_by_id(skill_id)
            if skill:
                return skill
        # 再查内置
        for s in BUILTIN_SKILLS:
            if s.skill_id == skill_id:
                return s
        return None

    async def lookup_by_name(self, user_id: UUID, skill_name: str) -> SkillDefinition | None:
        """按 skill_name 查找已安装的 Skill 定义。"""
        if not self._user_repo:
            return None
        installed = await self._user_repo.get_installed(user_id)
        for s in installed:
            if s.skill_name == skill_name:
                return s
        return None

    def get_skill_md(self, skill: SkillDefinition) -> str:
        """读取 Skill 的核心指令内容。

        对于有 folder_path 的 Skill，读取 SKILL.md 文件。
        对于内置 Skill（无 folder_path），返回内置的 prompt 文本。
        """
        if skill.folder_path:
            md_path = self._definitions_dir / skill.folder_path / "SKILL.md"
            if not md_path.exists():
                raise FileNotFoundError(f"SKILL.md 不存在: {md_path}")
            return md_path.read_text(encoding="utf-8").strip()

        # 内置 skill fallback
        if skill.skill_id in _BUILTIN_SKILL_MD:
            return _BUILTIN_SKILL_MD[skill.skill_id]

        raise FileNotFoundError(f"Skill '{skill.skill_name}' 没有可用的核心指令")

    async def build_available_skills_xml(self, user_id: UUID) -> str:
        """构建 <available_skills> XML 块，注入 system prompt。"""
        if not self._user_repo:
            return ""
        installed = await self._user_repo.get_installed(user_id)
        if not installed:
            return ""
        lines = ["<available_skills>"]
        for s in installed:
            lines.append(f"  <skill>")
            lines.append(f"    <name>{s.skill_name}</name>")
            lines.append(f"    <description>{s.description}</description>")
            lines.append(f"  </skill>")
        lines.append("</available_skills>")
        return "\n".join(lines)

    async def get_available(self, user_id: UUID) -> list[SkillDefinition]:
        """返回所有已安装的 Skill（用于错误提示中列出可用 skill）。"""
        if not self._user_repo:
            return []
        return await self._user_repo.get_installed(user_id)

    async def is_installed(self, user_id: UUID, skill_id: str) -> bool:
        if not self._user_repo:
            return False
        return await self._user_repo.is_installed(user_id, skill_id)

    # ── 安装/卸载 ────────────────────────────────────────────

    async def install(self, user_id: UUID, skill_id: str) -> None:
        """安装 Skill（纯状态写入，无进程操作）。"""
        if not self._user_repo:
            raise RuntimeError("UserSkillRepo not configured")

        if await self._user_repo.is_installed(user_id, skill_id):
            raise ValueError(f"Skill 已安装: {skill_id}")

        skill = await self.get(skill_id)
        if skill is None:
            raise ValueError(f"Skill 不存在: {skill_id}")

        await self._user_repo.install(user_id, skill)
        logger.info("skill.installed  skill_id=%s  name=%s", skill_id, skill.skill_name)

    async def uninstall(self, user_id: UUID, skill_id: str) -> bool:
        """卸载 Skill。内置 Skill 不可卸载。返回 True 表示成功。"""
        if not self._user_repo:
            return False

        if any(s.skill_id == skill_id for s in BUILTIN_SKILLS):
            raise ValueError(f"内置 Skill 不可卸载: {skill_id}")

        if not await self._user_repo.is_installed(user_id, skill_id):
            return False

        await self._user_repo.uninstall(user_id, skill_id)
        logger.info("skill.uninstalled  skill_id=%s", skill_id)
        return True

    # ── 自定义 Skill ─────────────────────────────────────────

    async def create_custom(self, user_id: UUID, skill: SkillDefinition) -> SkillDefinition:
        """创建自定义 Skill，自动生成 cs 前缀 ID 并自动安装。"""
        if not self._user_repo:
            raise RuntimeError("UserSkillRepo not configured")

        skill_id = await self._user_repo.create_custom(user_id, skill)
        skill.skill_id = skill_id
        logger.info("skill.custom_created  skill_id=%s  name=%s", skill_id, skill.skill_name)
        return skill

    async def update_custom(self, user_id: UUID, skill_id: str, skill: SkillDefinition) -> bool:
        """更新自定义 Skill。返回 True 表示成功。"""
        if not self._user_repo:
            return False
        return await self._user_repo.update_custom(user_id, skill_id, skill)

    async def delete_custom(self, user_id: UUID, skill_id: str) -> bool:
        """删除自定义 Skill（同时卸载并删除文件夹）。"""
        if not self._user_repo:
            return False

        # 先获取 skill 信息以删除文件夹
        installed = await self._user_repo.get_custom(user_id)
        for s in installed:
            if s.skill_id == skill_id and s.folder_path:
                import shutil
                skill_dir = self._definitions_dir / s.folder_path
                if skill_dir.exists():
                    shutil.rmtree(skill_dir, ignore_errors=True)
                break

        ok = await self._user_repo.delete_custom(user_id, skill_id)
        if ok:
            logger.info("skill.custom_deleted  skill_id=%s", skill_id)
        return ok

    # ── Hub 查询 ─────────────────────────────────────────────

    async def get_hub_skills(self) -> list[SkillDefinition]:
        """获取 Hub 中所有 Skill。"""
        if not self._hub_repo:
            return []
        return await self._hub_repo.list_all()

    async def get_hub_by_id(self, skill_id: str) -> SkillDefinition | None:
        """按 ID 查 Hub。"""
        if not self._hub_repo:
            return None
        return await self._hub_repo.get_by_id(skill_id)

    async def create_hub(self, skill: SkillDefinition) -> str:
        """添加 Skill 到 Hub，返回 skill_id。"""
        if not self._hub_repo:
            raise RuntimeError("SkillHubRepo not configured")
        return await self._hub_repo.create(skill)

    async def update_hub(self, skill_id: str, skill: SkillDefinition) -> bool:
        """更新 Hub 中的 Skill。"""
        if not self._hub_repo:
            return False
        return await self._hub_repo.update(skill_id, skill)

    async def delete_hub(self, skill_id: str) -> bool:
        """从 Hub 中删除 Skill。"""
        if not self._hub_repo:
            return False
        return await self._hub_repo.delete(skill_id)

    # ── 用户 Skill 查询 ──────────────────────────────────────

    async def get_installed_skills(self, user_id: UUID) -> list[SkillDefinition]:
        """获取已安装的 Skill 完整信息。"""
        if not self._user_repo:
            return []
        return await self._user_repo.get_installed(user_id)

    async def get_custom_skills(self, user_id: UUID) -> list[SkillDefinition]:
        """获取自定义 Skill 列表。"""
        if not self._user_repo:
            return []
        return await self._user_repo.get_custom(user_id)
