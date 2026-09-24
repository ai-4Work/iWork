"""RBAC 权限点清单 —— 唯一声明处（doc 19-4.4）。

路由上的 `require_permission("...")`、前端的 `ENTRY_VIEWS` 与 `<Permi>` 都只是**引用**本清单。
权威在代码、不在文档那张契约表：新增/下线权限点改这里，启动时 `sync_catalog()` 把库对齐，
`verify_route_refs()` 拦住"路由引用了清单里没有的 perms"。

⚠️ **别把本模块叫 permissions**：`server/permissions.yaml` 与 `server/tools/permission.py`
是 13.1 那套 Agent 沙箱/工具审批策略，与本文档的业务 RBAC 毫无关系。

节点字段：
* `key`  权限标识（perms）；目录行为 None（库里存 NULL，见 doc 19-2.2）
* `name` 中文名 → `sys_permission.permission_name`
* `type` M=目录 / C=菜单 / F=按钮
* `path` / `component` 前端路由与组件；纯前端入口与按钮留空串
* `apis` 后端接口绑定 `(method, 客户端视角路径)`；无则空
* `planned` 接口还没写（doc 19-4.2 的"待建"），对账时不报"清单有、没人引用"
* `children` 下级节点
"""

from __future__ import annotations


def _dir(name: str, path: str = "", component: str = "", children: tuple = ()) -> dict:
    """目录：只作分组容器，没有权限标识（perms = NULL）。"""
    return {
        "key": None, "name": name, "type": "M",
        "path": path, "component": component, "children": tuple(children),
    }


def _menu(key: str, name: str, path: str = "", component: str = "",
          apis: tuple = (), planned: bool = False, children: tuple = ()) -> dict:
    """菜单 / 客户端入口：对应一个独立页面。"""
    return {
        "key": key, "name": name, "type": "C",
        "path": path, "component": component, "apis": tuple(apis),
        "planned": planned, "children": tuple(children),
    }


def _btn(key: str, name: str, apis: tuple = (), planned: bool = False) -> dict:
    """按钮：没有前端路由和组件，但一定对应后端 API。"""
    return {
        "key": key, "name": name, "type": "F",
        "path": "", "component": "", "apis": tuple(apis),
        "planned": planned, "children": (),
    }


# ═══════════════════════════════════════════════════════════════
# 权限点清单（doc 19-4.2 契约表的代码版）
# ═══════════════════════════════════════════════════════════════

PERMISSIONS: tuple[dict, ...] = (
    _dir("系统管理", "/system", "Layout", children=(
        # 六个点，一页装得下：列表 + 建号 + 改显示名 + 重置密码 + 停用启用 + 解锁。
        # 曾声明的「详情 / 删除 / 导出」已下线：详情没有界面消费它，导出没有落点，
        # 删除会把 12 张表连带清掉（`users.id` 的 11 个 CASCADE + login_logs 的 SET NULL）
        # —— 封号该用停用，不该用删除。
        _menu("system:user:list", "用户管理", "/system/user", "system/user/index",
              apis=(("GET", "/api/system/user/list"),), children=(
                  _btn("system:user:add", "新增",
                       apis=(("POST", "/api/system/user"),)),
                  _btn("system:user:edit", "编辑",
                       apis=(("PUT", "/api/system/user/{user_id}"),)),
                  _btn("system:user:resetPwd", "重置密码",
                       apis=(("PUT", "/api/system/user/{user_id}/password"),)),
                  _btn("system:user:status", "停用/启用",
                       apis=(("PUT", "/api/system/user/{user_id}/status"),)),
                  _btn("system:user:unlock", "解锁",
                       apis=(("PUT", "/api/system/user/{user_id}/unlock"),)),
              )),
        _menu("system:skill:list", "技能 Hub", "/system/skill", "system/skill/index",
              apis=(("GET", "/api/skills/hub"),), children=(
                  _btn("system:skill:add", "新增", apis=(("POST", "/api/skills/hub"),)),
                  _btn("system:skill:edit", "编辑",
                       apis=(("PUT", "/api/skills/hub/{skill_id}"),)),
                  _btn("system:skill:remove", "删除",
                       apis=(("DELETE", "/api/skills/hub/{skill_id}"),)),
              )),
        _menu("system:mcp:list", "MCP Hub", "/system/mcp", "system/mcp/index",
              apis=(("GET", "/api/mcp/hub"),), children=(
                  _btn("system:mcp:add", "新增", apis=(("POST", "/api/mcp/hub"),)),
                  _btn("system:mcp:edit", "编辑",
                       apis=(("PUT", "/api/mcp/hub/{server_id}"),)),
                  _btn("system:mcp:remove", "删除",
                       apis=(("DELETE", "/api/mcp/hub/{server_id}"),)),
              )),
        # 模型配置：管理员维护 LLM 清单（公网 / 内网自建两类，见 doc 19 的模型配置一节）。
        # `system:model:test` 刻意单列一个点：它是唯一会拿库里的密钥真发一次请求的接口，
        # 给"能看能改"的人顺带放开等于给了一条外带探测通道，与其它只读点的信任级不同。
        _menu("system:model:list", "模型配置", "/system/model", "system/model/index",
              apis=(("GET", "/api/system/model/list"),), children=(
                  _btn("system:model:add", "新增",
                       apis=(("POST", "/api/system/model"),)),
                  _btn("system:model:edit", "编辑",
                       apis=(("PUT", "/api/system/model/{model_key}"),)),
                  _btn("system:model:remove", "删除",
                       apis=(("DELETE", "/api/system/model/{model_key}"),)),
                  _btn("system:model:test", "连接测试",
                       apis=(("POST", "/api/system/model/{model_key}/test"),)),
              )),
        _menu("system:stats:view", "全局统计", "/system/stats", "system/stats/index",
              apis=(("GET", "/api/system/stats/view"),), planned=True),
        _menu("system:audit:list", "审计日志", "/system/audit", "system/audit/index",
              apis=(("GET", "/api/admin/audit"),)),
        _menu("system:role:list", "角色管理", "/system/role", "system/role/index",
              apis=(("GET", "/api/system/role/list"),), children=(
                  _btn("system:permission:list", "查看权限点清单",
                       apis=(("GET", "/api/system/permission/list"),)),
                  _btn("system:role:query", "查看授权",
                       apis=(("GET", "/api/system/role/{role_id}/permissions"),)),
                  _btn("system:role:grant", "修改授权",
                       apis=(("PUT", "/api/system/role/{role_id}/permissions"),)),
                  _btn("system:role:edit", "修改数据范围",
                       apis=(("PUT", "/api/system/role/{role_id}"),)),
              )),
        _menu("system:dept:list", "部门管理", "/system/dept", "system/dept/index",
              apis=(("GET", "/api/system/dept/list"),), children=(
                  # 顶级与子部门其实是同一支 `POST /dept`，靠请求体里的 `parent_id == 0`
                  # 分流。路由依赖看不到请求体，所以依赖按「任一即可」放行、handler 里
                  # 再按 parent_id 挑一次点（dept_routes.create_dept）。
                  _btn("system:dept:addRoot", "新建顶级部门",
                       apis=(("POST", "/api/system/dept"),)),
                  _btn("system:dept:addChild", "新建子部门",
                       apis=(("POST", "/api/system/dept"),)),
                  # 建号在用户管理页也有控件（system:user:add），两页是同一支 API。
                  # 但部门页那个「添加用户」是这一页的控件，所以另给一个点 ——
                  # 两点任一即可加人，不是两个后端行为。
                  _btn("system:dept:addUser", "添加用户",
                       apis=(("POST", "/api/system/user"),)),
                  _btn("system:dept:edit", "改名",
                       apis=(("PUT", "/api/system/dept/{dept_id}"),)),
                  _btn("system:dept:remove", "删除",
                       apis=(("DELETE", "/api/system/dept/{dept_id}"),)),
                  # 挪人与挂角色这两件事，部门页和用户管理页都有控件，于是共用同一批点、
                  # 同一支 API（`user_routes.py`）—— 同一件事不开两个点，开了两页的可见性
                  # 就会各走各的。点挂在哪一页的菜单下，看的是这一页有没有那个控件。
                  # 建号时 body 带 `role_ids` 也归 `system:role:assign`：那道闸在 handler 里
                  # （`assert_permission`），**不写进 `apis`** —— 这一支 POST 的依赖是
                  # 「add / addUser 任一即可」，把 role:assign 加进依赖会让只有换角色权限的人
                  # 也能建号；写进 `apis` 又会让启动对账多一处漂移告警（它只比对依赖级引用）。
                  _btn("system:dept:assign", "所属部门",
                       apis=(("PUT", "/api/system/user/{user_id}/dept"),)),
                  _btn("system:role:assign", "角色",
                       apis=(("PUT", "/api/system/user/{user_id}/roles"),)),
              )),
    )),
    # ── 纯前端入口：没有后端 API，只在 agent-client 侧边栏里决定显不显示（doc 19-4.2）──
    _menu("client:skills:config", "Skills 配置"),
    _menu("client:mcp:config", "MCP 配置"),
    _menu("client:memory:config", "记忆配置"),
    _menu("client:expert:config", "专家和专家团"),
    # 系统管理那一组的入口（用户 / 角色 / 部门 / 模型）连着写：同一组的先后就是页签先后
    _menu("client:user:config", "用户管理"),
    _menu("client:rbac:config", "角色权限配置"),
    _menu("client:dept:config", "部门配置"),
    _menu("client:model:config", "模型配置"),
)


# ═══════════════════════════════════════════════════════════════
# 派生视图
# ═══════════════════════════════════════════════════════════════

ADMIN_ROLE_KEY = "admin"
"""超级管理员角色标识：权限短路成全集，数据范围 `ALL`（doc 19-5.1 / 19-5.5）。"""

DEFAULT_ROLE_KEY = "user"
"""新注册账号的默认角色（doc 19-5.5 的 `user`）。"""

DEFAULT_ROLE_NAME = "普通用户"

DEFAULT_ROLE_PERMS = (
    "client:skills:config",
    "client:mcp:config",
    "client:memory:config",
)
"""`user` 角色首次建立时授的三条纯前端入口。

**不给** `client:expert:config` —— 与 doc 19-4.5 的例子对齐。
"""

ADMIN_ROLE_NAME = "超级管理员"
"""`admin` 的显示名。库里那行叫「管理员」是历史，种子每次启动对账改名（doc 19-5.5）。"""

DEPT_ADMIN_ROLE_KEY = "dept_admin"
"""中间档：管理部门数据的管理员，`data_scope='DEPT'`（doc 19-5.3）。"""

DEPT_ADMIN_ROLE_NAME = "管理员"

ALL_PERMS_ROLE_KEYS = frozenset({ADMIN_ROLE_KEY, DEPT_ADMIN_ROLE_KEY})
"""短路成「全部权限点」的角色标识（doc 19-5.1）。

这几个角色**一行授权都不写**，区别只在角色行上的 `data_scope`：

*   `admin` → `ALL`，看得见全部数据；
*   `dept_admin` → `DEPT`，只看得见本部门及下级的人。

之所以短路、而不是给它们显式挂满权限点：后者以后每加一个权限点都要记得补一行，
忘一次就静默少个入口。名单放在代码里（角色本来就由种子在代码里定义），
权限配置页上这几列只读恒勾选。

→ 副作用是它们**必然**拿到 `system:role:edit` 与 `system:dept:*`，那两处写接口
因此各加了一道范围守卫（doc 19-5.3.1），否则 `DEPT` 档一步就能改成 `ALL`。
"""

DEFAULT_DEPT_KEY = "default"
"""默认部门在 `sys_dept.dept_key` 上的业务键（doc 19-2.2）。

后台没建组织架构时所有用户都落在这里。种子按它认行插入、删除接口按它拒绝 ——
都不能按 `dept_name` 认：管理员改个名字就找不着了。
"""

DEFAULT_DEPT_NAME = "默认部门"


def walk(nodes: tuple[dict, ...] = PERMISSIONS, parent: dict | None = None):
    """深度优先遍历，产出 (节点, 父节点)。"""
    for node in nodes:
        yield node, parent
        yield from walk(node.get("children", ()), node)


def all_perms() -> list[str]:
    """清单里全部权限标识（不含目录）。admin 短路用的就是它。"""
    return [n["key"] for n, _ in walk() if n["key"]]


def planned_perms() -> set[str]:
    """接口还没写的权限点，对账时不报"清单有、没人引用"。"""
    return {n["key"] for n, _ in walk() if n["key"] and n.get("planned")}


def perm_apis() -> dict[str, tuple[tuple[str, str], ...]]:
    """权限标识 → 后端接口绑定；目录与其他无绑定的点不在返回值里。"""
    return {n["key"]: n["apis"] for n, _ in walk() if n["key"] and n["apis"]}


def module_of(perms: str) -> str:
    """权限点的模块位（`system:user:add` → `system`）。"""
    return perms.split(":", 1)[0]


def as_json() -> list[dict]:
    """给 CI / 前端比对脚本用的扁平导出（doc 19-4.4）。

    落点是「前端源码里的 perms 字符串」与它双向比对，脚本还没搭（见 5.5 备注）。
    """
    out = []
    for node, parent in walk():
        out.append({
            "perms": node["key"],
            "name": node["name"],
            "type": node["type"],
            "path": node.get("path", ""),
            "component": node.get("component", ""),
            "apis": [{"method": m, "path": p} for m, p in node.get("apis", ())],
            "module": module_of(node["key"]) if node["key"] else None,
            "planned": bool(node.get("planned")),
            "parent": parent["key"] if parent else None,
        })
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(as_json(), ensure_ascii=False, indent=2))
