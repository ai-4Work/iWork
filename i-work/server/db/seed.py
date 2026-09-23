import json
import logging
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from server.config import settings
from server.db.models import (
    OrmUser, OrmSkillHub, OrmMcpHub, OrmExpertHub, OrmExpertTeamHub,
)

logger = logging.getLogger("iwork.db.seed")


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


async def seed_rbac(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """RBAC 字典表对账 + 角色/首个管理员（doc 19-5.5）。

    分两段，写法刻意不同：

    * **字典表**（`sys_permission` / `sys_permission_api`）→ upsert 对账，
      每次启动都跑。若只在初始化时插一次，之后每新增一个权限点库里都会落后，
      校验时查不到就是 403。
    * **业务表**（`sys_role` / `sys_role_permission` / `sys_user_role`）→
      insert-if-missing，缺了才插、**绝不覆盖已有行**。种子每次启动都会跑到，
      若 upsert，管理员改过的角色名、禁用过的角色、调整过的授权，
      会在下一次重启时被静默抹回种子值。
    """
    from server.auth.service import AuthError, _validate_username, normalize_username
    from server.auth.security import PasswordPolicyError, hash_password
    from server.authz.catalog import (
        ADMIN_ROLE_KEY, ADMIN_ROLE_NAME,
        DEFAULT_ROLE_KEY, DEFAULT_ROLE_NAME, DEFAULT_ROLE_PERMS,
        DEPT_ADMIN_ROLE_KEY, DEPT_ADMIN_ROLE_NAME,
    )
    from server.authz.service import sync_catalog
    from server.storage.postgres import (
        PermissionRepo, RolePermissionRepo, RoleRepo, UserRoleRepo,
    )

    # ── 1. 字典表对账（含新增与下线） ──
    async with session_factory() as db:
        await sync_catalog(db)

    role_repo = RoleRepo(session_factory)
    role_perm_repo = RolePermissionRepo(session_factory)
    perm_repo = PermissionRepo(session_factory)
    user_role_repo = UserRoleRepo(session_factory)

    # ── 2. 三个内置角色：都必须显式写 data_scope ──
    # 漏了就会吃 sys_role 的默认 'SELF' —— admin 权限全通、数据却只剩自己（doc 19-2.2）
    admin_role = await role_repo.get_by_key(ADMIN_ROLE_KEY)
    if admin_role is None:
        admin_role = await role_repo.create(ADMIN_ROLE_KEY, ADMIN_ROLE_NAME, "ALL")
        logger.info("seed.rbac  role=%s created", ADMIN_ROLE_KEY)
    elif admin_role.role_name != ADMIN_ROLE_NAME:
        # 显示名对账：这一行历史上叫「管理员」，现在要叫「超级管理员」（doc 19-5.5）。
        # 只比名字，不是每次启动都写一遍库。
        await role_repo.set_role_name(admin_role.id, ADMIN_ROLE_NAME)
        logger.info("seed.rbac  role=%s renamed=%s", ADMIN_ROLE_KEY, ADMIN_ROLE_NAME)

    dept_admin_role = await role_repo.get_by_key(DEPT_ADMIN_ROLE_KEY)
    if dept_admin_role is None:
        dept_admin_role = await role_repo.create(
            DEPT_ADMIN_ROLE_KEY, DEPT_ADMIN_ROLE_NAME, "DEPT",
        )
        logger.info("seed.rbac  role=%s created", DEPT_ADMIN_ROLE_KEY)

    user_role = await role_repo.get_by_key(DEFAULT_ROLE_KEY)
    if user_role is None:
        user_role = await role_repo.create(DEFAULT_ROLE_KEY, DEFAULT_ROLE_NAME, "SELF")
        logger.info("seed.rbac  role=%s created", DEFAULT_ROLE_KEY)

    # ── 3. `user` 角色的默认入口：只在这个角色从未配过授权时给一次 ──
    # 判据用"一条授权都没有"，而不是逐条 upsert —— 否则管理员取消的那一项，
    # 下次重启会被种子重新勾上（同 5.5.2 的 insert-if-missing 规则）。
    if not await role_perm_repo.list_permission_ids(user_role.id):
        by_perms = {p.perms: p.id for p in await perm_repo.list_all() if p.perms}
        wanted = [by_perms[k] for k in DEFAULT_ROLE_PERMS if k in by_perms]
        await role_perm_repo.replace(user_role.id, wanted)
        logger.info("seed.rbac  role=%s grants=%d", user_role.role_key, len(wanted))

    # `admin` 与 `dept_admin` **一行授权都不写**：它们的"拥有所有权限"靠
    # `load_user_permissions` 按 `ALL_PERMS_ROLE_KEYS` 短路成全集，以后每加一个
    # 权限点自动就有（doc 19-5.1 / 19-5.5）。两者的区别只在上面那行 `data_scope`。

    # ── 4. 首个管理员账号 ──
    username = normalize_username(settings.bootstrap_admin_username)
    if not username:
        raise RuntimeError("IWORK_BOOTSTRAP_ADMIN_USERNAME 为空：无法确定首个管理员账号")

    admin_id = None
    async with session_factory() as db:
        admin_user = (await db.execute(
            select(OrmUser).where(OrmUser.username == username)
        )).scalar_one_or_none()

        if admin_user is None:
            # 只在真的要建账号时才强制要密码：账号已存在时留空不该拦住重启
            if not settings.bootstrap_admin_password:
                raise RuntimeError(
                    "IWORK_BOOTSTRAP_ADMIN_PASSWORD 未配置：首次启动要建管理员账号，"
                    "请在 i-work/.env 中设置（参考 .env.example）"
                )
            try:
                _validate_username(username)
                password_hash = hash_password(settings.bootstrap_admin_password)
            except (AuthError, PasswordPolicyError) as exc:
                raise RuntimeError(f"bootstrap 管理员账号不合法：{exc}")
            admin_user = OrmUser(
                username=username,
                display_name=username,
                password_hash=password_hash,
                status="active",
            )
            db.add(admin_user)
            await db.commit()
            await db.refresh(admin_user)
            logger.info("seed.rbac  admin username=%s created", username)

        # 幂等：已存在就只补角色关联，**不覆盖密码、不覆盖 status**
        # —— 管理员自己改过的密码不能被下次启动抹回去
        admin_id = admin_user.id

    await user_role_repo.assign(admin_id, admin_role.id)

    # ── 5. 存量账号回填默认角色 ──
    # 注册流程补默认角色只对之后注册的人生效；认证上线前建的账号一个角色都没有，
    # RBAC 一生效它们的 permissions 就是空的、侧边栏一个入口都不显示。
    backfilled = await user_role_repo.assign_default_to_roleless(user_role.id)
    if backfilled:
        logger.info("seed.rbac  role=%s backfilled=%d", DEFAULT_ROLE_KEY, backfilled)


async def seed_dept(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """默认部门 + 存量用户归属回填（doc 19-2.2）。

    两段写法与 `seed_rbac` 一致：

    * **默认部门** → insert-if-missing：按 `dept_key` 查，缺了才插。**绝不覆盖已有行** ——
      管理员把「默认部门」改成了「总部」是正当操作，下次重启不能被抹回去。
      也**不给它写死 id**：PG 手写 id 不推进自增序列，之后正常插入就撞主键。
    * **用户归属** → 每次启动都把 `dept_id IS NULL` 补成默认部门。后台没建组织架构时，
      所有人就都在默认部门里 —— 这正是这一层要的兜底语义。
    """
    from server.authz.catalog import DEFAULT_DEPT_KEY, DEFAULT_DEPT_NAME
    from server.storage.postgres import DeptRepo

    dept_repo = DeptRepo(session_factory)

    default_dept = await dept_repo.get_by_key(DEFAULT_DEPT_KEY)
    if default_dept is None:
        default_dept = await dept_repo.create(
            parent_id=0, dept_name=DEFAULT_DEPT_NAME, order_num=0,
            dept_key=DEFAULT_DEPT_KEY,
        )
        logger.info("seed.dept  key=%s created", DEFAULT_DEPT_KEY)

    backfilled = await dept_repo.backfill_null_dept(default_dept.id)
    if backfilled:
        logger.info("seed.dept  dept_id backfilled=%d", backfilled)
