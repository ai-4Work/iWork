import json
import logging
from uuid import UUID
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from server.db.models import OrmUser, OrmSkillHub, OrmMcpHub, OrmExpertHub, OrmExpertTeamHub

logger = logging.getLogger("iwork.db.seed")

DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def seed_default_user(session_factory: async_sessionmaker[AsyncSession]) -> UUID:
    """确保 default-user 存在，返回其 UUID。已存在则跳过。"""
    async with session_factory() as db:
        result = await db.execute(
            select(OrmUser).where(OrmUser.username == "default-user")
        )
        user = result.scalar_one_or_none()
        if user:
            return user.id
        user = OrmUser(
            id=DEFAULT_USER_ID,
            username="default-user",
            display_name="默认用户",
        )
        db.add(user)
        await db.commit()
        logger.info("seed.default_user  created")
        return user.id


async def seed_hub_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """导入 skill-hub.json / mcp-hub.json 到对应表。表非空则跳过。"""
    base = Path(__file__).parent.parent  # server/
    async with session_factory() as db:
        # ── Skill hub ──
        result = await db.execute(select(func.count(OrmSkillHub.id)))
        if result.scalar() == 0:
            hub_path = base / "skill-hub.json"
            if hub_path.exists():
                data = json.loads(hub_path.read_text(encoding="utf-8"))
                for s in data.get("skills", []):
                    if "tags" in s and isinstance(s["tags"], list):
                        s = dict(s)
                    db.add(OrmSkillHub(**s))
                await db.commit()
                logger.info("seed.skill_hub  count=%d", len(data.get("skills", [])))

        # ── MCP hub ──
        result = await db.execute(select(func.count(OrmMcpHub.id)))
        if result.scalar() == 0:
            hub_path = base / "mcp-hub.json"
            if hub_path.exists():
                data = json.loads(hub_path.read_text(encoding="utf-8"))
                servers = data.get("servers", data) if isinstance(data, dict) else data
                if isinstance(servers, list):
                    for s in servers:
                        if "headers" in s:
                            s = {k: v for k, v in s.items() if k != "headers"}
                        if "args" in s and not isinstance(s["args"], list):
                            s["args"] = []
                        if "env" in s and not isinstance(s["env"], dict):
                            s["env"] = {}
                        db.add(OrmMcpHub(**s))
                    await db.commit()
                    logger.info("seed.mcp_hub  count=%d", len(servers))


async def seed_expert_hub_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """扫描 plugins/experts/ 和 plugins/ 目录下的 .iwork-plugin 标记目录，
    从 plugin.json 初始化专家/团队数据。表非空则跳过。"""
    import uuid
    from pathlib import Path

    experts_dir = Path(__file__).parent.parent / "plugins" / "experts"

    def _extract_display_name(data: dict) -> str:
        dn = data.get("displayName", data["name"])
        if isinstance(dn, dict):
            return dn.get("zh", dn.get("en", data["name"]))
        return str(dn)

    async with session_factory() as db:
        # ── Expert hub: 扫描 server/plugins/experts/<name>/ ──
        result = await db.execute(select(func.count(OrmExpertHub.id)))
        if result.scalar() == 0 and experts_dir.is_dir():
            seeded = 0
            for plugin_dir in experts_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                marker = plugin_dir / ".iwork-plugin"
                if not marker.is_dir():
                    continue
                config_path = marker / "plugin.json"
                if not config_path.exists():
                    config_path = plugin_dir / "plugin.json"
                    if not config_path.exists():
                        continue

                data = json.loads(config_path.read_text(encoding="utf-8"))
                if data.get("expertType") == "team":
                    continue
                expert_cfg = data.get("config", {}) or {}
                expert_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"expert:{data['name']}")
                db.add(OrmExpertHub(
                    id=expert_id,
                    name=data["name"],
                    display_name=_extract_display_name(data),
                    description=data.get("description", ""),
                    plugin_path=str(plugin_dir.relative_to(Path(__file__).parent.parent)) + "/",
                    version=data.get("version", "1.0.0"),
                    max_turn=expert_cfg.get("max_turn"),
                    max_tokens=expert_cfg.get("max_tokens"),
                    timeout_seconds=expert_cfg.get("timeout_seconds"),
                    permissions=expert_cfg.get("permissions", []),
                ))
                seeded += 1
                logger.info("seed.expert  name=%s  dir=%s", data["name"], plugin_dir.name)

            await db.commit()
            logger.info("seed.expert_hub  count=%d", seeded)

        # ── Team hub: 扫描 server/plugins/experts/<name>/ 中 expertType=="team" ──
        result = await db.execute(select(func.count(OrmExpertTeamHub.id)))
        if result.scalar() == 0 and experts_dir.is_dir():
            seeded = 0
            for plugin_dir in experts_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                marker = plugin_dir / ".iwork-plugin"
                if not marker.is_dir():
                    continue
                config_path = marker / "plugin.json"
                if not config_path.exists():
                    config_path = plugin_dir / "plugin.json"
                    if not config_path.exists():
                        continue

                data = json.loads(config_path.read_text(encoding="utf-8"))
                if data.get("expertType") != "team":
                    continue

                team_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"team:{data['name']}")
                db.add(OrmExpertTeamHub(
                    id=team_id,
                    name=data["name"],
                    display_name=_extract_display_name(data),
                    description=data.get("description", ""),
                    plugin_path=str(plugin_dir.relative_to(Path(__file__).parent.parent)) + "/",
                    version=data.get("version", "1.0.0"),
                    lead_agent_id=data.get("leadAgent") or (data.get("teamInfo", {}) or {}).get("leadAgent", ""),
                    members=data.get("members", []),
                    skills=data.get("skills", []),
                    mcp=data.get("mcp", []),
                ))
                seeded += 1
                logger.info("seed.team  name=%s  dir=%s", data["name"], plugin_dir.name)

            await db.commit()
            logger.info("seed.team_hub  count=%d", seeded)
