
# 自主 Agent 办公系统 RBAC 权限体系设计指南

> **目标**：实现管理员可见「用户管理、Hub 管理、全局统计」等菜单，普通用户不可见；做到“用户在客户端操作不了无权限的功能和数据”。
>
> **范围**：纯业务系统的 RBAC（功能权限 + 数据范围），不做 Agent 工具层面的权限，不在 Agent 问答层做权限拦截。
>
> **核心链路公式**：**业务功能 → 权限点（perms） → 前端路由/按钮 + 后端 API**。

---

## 一、核心理念与误区澄清

### 1.1 工具权限 vs 业务 RBAC
*   **工具执行权限**：决定 Agent 能不能调用某个底层工具（如 `删除数据库`、`调用外部 API`），这是 Agent 的能力边界。
*   **业务 RBAC**：决定“谁（User）能在什么条件下，操作哪些业务数据（Resource）”。这是企业级系统的安全底线。
*   **结论**：本指南聚焦于业务 RBAC，不碰工具层面的权限拦截。

### 1.2 为什么不在 Agent 问答层做权限？
*   **前端隐藏**：如果用户没权限，前端界面根本不应该渲染对应的菜单和按钮。用户不会去问一个不存在的功能。
*   **安全底线在后端**：不要把权限判断写在 System Prompt 里。LLM 的自觉性不可靠，且 Prompt 容易被注入攻击。真正的安全永远在后端 API 层。
*   **Agent 的角色**：Agent 只是一个“带着用户 JWT 去调用后端接口的普通前端”。它不需要理解权限体系，只需要在收到 `403 Forbidden` 时能翻译成人话即可。

### 1.3 功能权限 vs 数据权限

RBAC 里「权限」其实是两件事，不能混：

| 维度 | 功能权限 | 数据权限 |
| :--- | :--- | :--- |
| **管什么** | 这个操作**能不能调** | 这个操作**能作用到哪些行** |
| **表现** | 直接 `403`，请求进不来 | 不报错，**结果集被裁剪**（拿到的列表更短） |
| **挂在哪** | 权限点 `perms`，经角色授予 | 角色身上的 `data_scope` 字段 |
| **本指南落地** | §4 权限点、§5.1 校验 | §2.2 `data_scope`、§5.3 过滤 |

**判据**：同一个权限点、同一行代码，不同角色跑出来的结果集不同 → 那是数据权限，不是功能权限。

### 1.4 目录 vs 菜单 vs 按钮的区别

| 维度 | 目录（Directory） | 菜单（Menu） | 按钮（Button） |
| :--- | :--- | :--- | :--- |
| **前端表现** | 左侧导航栏可展开的分组 | 左侧导航栏可点击的条目 | 页面内的操作按钮 |
| **用户点击后** | 展开/折叠子菜单，不跳页面 | 跳转到新页面（前端路由切换） | 弹窗/触发请求（页面不跳转） |
| **前端路由** | ✅ 有（通常指向 Layout 容器） | ✅ 有（指向具体页面组件） | ❌ 没有 |
| **前端组件** | ✅ 有（如 `Layout`） | ✅ 有（如 `UserPage.vue`） | ❌ 没有 |
| **后端 API** | ❌ 通常无（仅作分组） | ✅ 有（如列表查询接口） | ✅ 有（如新增/删除接口） |
| **数据库 `permission_type`** | `M` | `C` | `F` |
| **权限点示例** | （通常不配 `perms`） | `system:user:list` | `system:user:add` |
| **典型例子** | “系统管理” | “用户管理”、“Hub 管理” | “新增用户”、“删除用户” |

> **补充说明**：
> - **目录**只是分组容器，本身不承载业务逻辑，因此通常不配置权限点。只要用户对其下任意子菜单有权限，目录就会自动显示。
> - **菜单**对应一个独立页面，必须有前端路由、前端组件和后端 API。
> - **按钮**没有前端路由和组件，但它一定对应后端 API，用于触发数据读写操作。
> - 目录/菜单/按钮都只是**功能权限**的落地形态。**数据权限没有这些形态**——它不画在界面上，只作用在数据行上（见 1.3）。



---

## 二、RBAC 数据库设计（标准六张表 + 组织架构）

### 2.1 表结构总览

| 表名 | 说明 | 作用 |
| :--- | :--- | :--- |
| `users` | 用户表（**已存在**） | 存储系统用户基本信息，由登录认证模块建立；`dept_id` 是本轮加的 |
| `sys_dept` | 部门表 | 组织架构树；用户通过 `users.dept_id` 归属到一个部门 |
| `sys_role` | 角色表 | 存储角色定义及数据范围 |
| `sys_permission` | 菜单/权限表 | 存储目录、菜单、按钮（权限树） |
| `sys_permission_api` | 权限点-API 映射表 | 权限点与后端接口的多对多关系 |
| `sys_user_role` | 用户-角色关联表 | 用户与角色的多对多关系 |
| `sys_role_permission` | 角色-菜单关联表 | 角色与菜单的多对多关系 |

> RBAC 六张表里只有 `users` 已存在，其余五张随 RBAC 一起建。`sys_dept` 不属于这六张 ——
> 它是部门层单加的一张，`data_scope` 的 `DEPT` 档就落在它上面（见 5.3）。


| 概念 | 英文 | 说明 | 例子 |
| :--- | :--- | :--- | :--- |
| **用户** | User | 系统的实际操作者，一个人一个账号 | 张三、李四 |
| **角色** | Role | 一组权限的集合，代表一种“身份”或“岗位” | 管理员、普通员工、财务主管 |
| **权限** | Permission | 一个具体的可操作项，对应前端目录/菜单/按钮或后端 API | `system:user:list`、`system:user:add` |
| **数据范围** | Data Scope | 一个角色能看到哪些数据行；**挂在角色上，不是权限点** | 全量 / 仅本人 |

---

### 2.2 表结构详细定义

#### 1. `users`（用户表，已存在）

> 本表由登录认证模块建立，**RBAC 不新建用户表**，只在其上挂角色关联。
>
> 下表的类型与约束以代码为准（`server/db/models.py` 的 `OrmUser`、`server/alembic/versions/019_auth.py`）。`18-登录认证模块` §12.1 只给了宽松类型，查不到 `NOT NULL` / `UNIQUE` 这些约束。

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | UUID | PK | 用户ID |
| `username` | VARCHAR(100) | NOT NULL, UNIQUE | 登录名；入库和登录查询前都 `.lower()`，长度 3–32，限 `[a-z0-9_-]` |
| `display_name` | VARCHAR(200) | NULL 可空 | 显示名称；空时界面回退显示 `username` |
| `password_hash` | VARCHAR(200) | — | bcrypt 哈希；NULL = 不可密码登录 |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT 'active' | 仅管理员态：`active` / `disabled`；到期自解的自动锁定走 `locked_until` |
| `failed_login_count` | INT | NOT NULL, DEFAULT 0 | 连续登录失败次数；登录成功或锁定期满即清零 |
| `locked_until` | TIMESTAMPTZ | — | 自动锁定截止时间；NULL = 未锁定 |
| `dept_id` | BIGINT | FK → `sys_dept.id`, ON DELETE RESTRICT | 所属部门；建号时由管理员选定，种子只把存量的 NULL 回填成默认部门 |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT NOW() | 最后更新时间 |

#### 2. `sys_role`（角色表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | PK, AUTO_INCREMENT | 角色ID |
| `role_name` | VARCHAR(50) | NOT NULL | 角色名称（如：管理员） |
| `role_key` | VARCHAR(50) | NOT NULL, UNIQUE | 角色标识（如：admin） |
| `data_scope` | VARCHAR(20) | DEFAULT 'SELF' | 数据范围，三档：`ALL` 全部 / `DEPT` 本部门及下级 / `SELF` 仅本人（见 5.3） |
| `status` | TINYINT | DEFAULT 1 | 状态（1=正常 0=禁用） |

> **`DEFAULT 'SELF'` 是个陷阱**：不显式赋值就会收范围。种子建三个内置角色时**必须显式写 `data_scope`**
> ——`admin` 写 `'ALL'`、`dept_admin` 写 `'DEPT'`、`user` 写 `'SELF'`（见 5.5.3）。
>
> **数据范围只认角色行，不做短路**：permissions 有全量角色的短路（见 5.1），数据范围没有——两套规则只会互相打架。
> 这也正是 `admin` 与 `dept_admin` 能共用同一套权限点、只靠这一个字段分档的原因（见 5.3.1）。

#### 3. `sys_permission`（菜单/权限表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | PK, AUTO_INCREMENT | 菜单ID |
| `parent_id` | BIGINT | DEFAULT 0 | 父级ID（0=顶级） |
| `permission_name` | VARCHAR(50) | NOT NULL | 菜单/按钮名称 |
| `permission_type` | CHAR(1) | NOT NULL | 类型（M=目录 C=菜单 F=按钮） |
| `path` | VARCHAR(200) | DEFAULT '' | 前端路由地址 |
| `component` | VARCHAR(255) | DEFAULT '' | 前端组件路径 |
| `perms` | VARCHAR(100) | NULL, UNIQUE | 权限标识（前后端共用）；**目录行（M）填 NULL** |
| `icon` | VARCHAR(100) | DEFAULT '' | 图标 |
| `order_num` | INT | DEFAULT 0 | 排序号 |
| `visible` | TINYINT | DEFAULT 1 | 是否可见（1=显示 0=隐藏） |
| `status` | TINYINT | DEFAULT 1 | 状态（1=正常 0=禁用） |

> 本表只存「有哪些权限点」，**不存权限点与 API 的绑定**——绑定单独放 `sys_permission_api`（见本节的第 6 张表）。不能挤进本表的原因：一个权限点可对应多个 API，单字段放不下（见 4.1 原则 1）。
>
> **`perms` 为什么是 NULL + UNIQUE，不是 `DEFAULT ''`**：
> *   启动对账要按 `perms` 认行（见 4.4），这是个稳定键。
> *   目录行（`M`，如「系统管理」）本来就没有权限标识。若都填空串，多个空串在 UNIQUE 下互相冲突、也认不出谁是谁。
> *   填 NULL 即可——NULL 之间不冲突。

#### 4. `sys_user_role`（用户-角色关联表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `user_id` | UUID | PK, FK | 用户ID（关联 `users.id`） |
| `role_id` | BIGINT | PK, FK | 角色ID（关联 `sys_role.id`） |

#### 5. `sys_role_permission`（角色-菜单关联表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `role_id` | BIGINT | PK, FK | 角色ID（关联 `sys_role.id`） |
| `permission_id` | BIGINT | PK, FK ON DELETE CASCADE | 菜单ID（关联 `sys_permission.id`） |

#### 6. `sys_permission_api`（权限点-API 映射表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `permission_id` | BIGINT | PK, FK ON DELETE CASCADE | 权限点ID（关联 `sys_permission.id`） |
| `method` | VARCHAR(10) | PK | HTTP 方法（GET/POST/PUT/DELETE） |
| `path` | VARCHAR(200) | PK | 接口路径（如 `/api/system/user/{id}`），区别于 `sys_permission.path`（前端路由） |

> 一个权限点可对应多个 API、一个 API 也可被多个权限点复用，故三列共同作主键。
>
> 这层绑定在代码里由 `Depends(require_permission(...))` 表达（见 5.1），本表是它在库里的镜像——**两处必须一致**，否则会无声漂移（库里改了、代码没改，或反之）。
>
> **怎么比**：
> *   启动时遍历 `app.routes`，从每个接口的 dependant 里取出它声明的 `require_permission(...)` 实参，凑成 `(perms, method, path)` 与本表比。
> *   多出来的、少掉的都告警。
> *   这一步在 `sync_catalog()` 之后跑（见 4.4）。

#### 7. `sys_dept`（部门表）

| 字段名 | 类型 | 约束 | 说明 |
| :--- | :--- | :--- | :--- |
| `id` | BIGINT | PK, AUTO_INCREMENT | 部门ID |
| `parent_id` | BIGINT | NOT NULL, DEFAULT 0 | 父级ID（0=顶级）；层数不设限 |
| `dept_name` | VARCHAR(50) | NOT NULL | 部门名称 |
| `dept_key` | VARCHAR(50) | NULL, UNIQUE | 业务键；见下 |
| `order_num` | INT | DEFAULT 0 | 排序号 |
| `status` | TINYINT | DEFAULT 1 | 状态（1=正常 0=禁用） |

> **`dept_key` 为什么是 NULL + UNIQUE**：和 `sys_permission.perms` 同一套理由。种子每次启动都要认出「默认部门」那一行（去回填还没归属的人），而 `dept_name` 管理员可以改 —— 按名字找，改个名就会建出第二个默认部门。只有代码要按业务键认的行才填（默认部门 = `'default'`），用户自建的部门填 NULL，NULL 之间不冲突。
>
> **不给默认部门写死 id**：PG 手写 id 不推进自增序列，之后正常插入就会撞主键（同 4.2 的告诫）。所以按 `dept_key` 查、缺了才插。
>
> **不存 `ancestors` 路径列**：没有任何「按子树查成员」的需求（DEPT 档没接线），先不造。
>
> **`users.dept_id` 用 `ON DELETE RESTRICT`**：不是 CASCADE / SET NULL。删部门由接口层守卫（有下级或有成员就拒删），数据库这层再把意图写死，万一有路径绕过接口，也不会静默把人的归属清掉。

---



---

## 三、权限梳理思路

功能权限与数据权限是**两条独立的梳理线**：起点不同、产出物不同（见 1.3），一条链路走不完。

### 3.1 功能权限梳理

**链路**：业务功能 → 权限点（perms） → 前端路由/按钮 + 后端 API

梳理从**业务功能**出发，不从 API 出发：

1.  **列业务功能**：产品给出功能清单——有哪些页面、页面上有哪些按钮和操作。这是唯一起点。业务功能在系统里的落地形态，就是前端的**目录、菜单、按钮**（见 1.4）。
2.  **收敛成权限点**：每个*可独立授予*的业务操作，对应*一个*权限点，命名格式 `模块:资源:操作`（见 4.1）。
3.  **向两端投影**：同一个权限点分别落到前端和后端——
    *   **前端**：决定菜单/路由显不显示（见 6.4）、按钮渲不渲染（见 6.5）。
    *   **后端**：决定 API 让不让调（见 5.1）。
4.  **落库**：把「权限点 ↔ 前端路由/组件 ↔ 后端 API」的对应关系写进 `sys_permission` 字典表（见 4.2）。

**粒度判据**：一个权限点 = 一个可以单独授予的操作。

*   两个操作若**永远同时授予、同时撤销**，说明拆细了，应合并。
*   一个操作若**有人能改一半、有人不能**，说明拆粗了，应拆分。

> 前端隐藏只是渲染开关，不是安全边界——真正的拦截在后端 API（见 1.2）。

### 3.2 数据权限梳理

**链路**：数据对象 → 可见档位 → 给角色定档 → 落到查询条件

数据权限**不从业务功能出发**，也不产出权限点。它回答的是「同一个页面、同一行代码，谁看到哪些行」：

1.  **列数据对象**：哪些业务数据需要划范围（用户、会话、Hub 条目……）。判据：**这个东西是不是「多人共用一份、但各自该看到的不一样」**。只给自己用的数据（如私有会话）不需要划范围。
2.  **定档位**：全量（`ALL`）/ 仅本人（`SELF`）。档位是**枚举**、不是自由条件，客户端不传、由服务端按角色自己算——否则前端传个 `ALL` 就绕过了。
3.  **给角色定档**：`data_scope` 是**角色**的属性，不是权限点的属性（见 2.1），一个角色一个值。
4.  **落到查询**：梳理的对象是 **DAO 层（本项目是 Repo）里「查这个数据对象的全部语句」**，产出一份**落点清单**。

    *   不在路由层补、不在前端判、不进 `perms`（见 5.3）。
    *   为什么必须在 DAO 层：只有 SQL 那一层能一次覆盖 count / join / 子查询，放到上层就会出现「列表过滤了、导出没过滤」。

    **哪些查询要落**——三个筛子，全过才落：

    *   **结果集会因「谁在调」而异？** 不会（全局字典、系统配置、Hub 目录）→ 不落。
    *   **已有天然归属键？** 有（查自己的会话/消息，本来就是 `where user_id = 我`）→ 已覆盖，不落。
    *   **调用方有身份？** 没有（定时任务、队列 worker、种子脚本）→ 不落，但要**显式标白名单**，别靠「actor 为空就放行」静默通过。

    收敛成一句：**只落在「以用户身份发起 + 默认查全量」的查询上**——数据权限是给跨用户 / 管理视角的查询**收范围**，不是给普通用户的查询加条件。

    **一个数据对象要落齐四类入口**，最容易漏的是后三类：

    *   列表
    *   **详情（按 id 查）**
    *   **COUNT 分页总数**
    *   **导出、统计聚合与关联查询**

    另有两处：

    *   **缓存 key 必须带 scope**，否则裁剪后的结果被换个人复用。
    *   **分页是先 `where` 再 `limit`**。

    **落法二选一**：

    *   **显式传参**：方法签名带 actor / scope，手写 `where`。可读可 review，但靠人记得写。
    *   **统一注入**：切面 / ORM 过滤器自动生效。不会漏，但隐式难调、原生 SQL 直接绕过。

    判据：**入口能枚举就用显式传参 + 落点清单；漏不住了才上统一注入。**

**本项目的落点清单**（当前已落的三处，全部落地情况见 5.5.4）：

*   **会话（`sessions`）** —— `SELF` 档。
    *   落点：`/{session_id}` 下的 19 个 handler 共用一个 `require_session_access` 依赖（读路径参数里的 `session_id`，比对 `session.user_id`，`ALL` 放行）。
    *   判据：这些是「知道 id 就能读别人数据」的入口，正是上面第一个筛子要拦的。
*   **审计日志（`audit_logs`）** —— `ALL` 档专属。
    *   落点：`GET /admin/audit` 在 handler 里，当调用者 `scope != ALL` 时把查询的 `user_id` 覆盖成调用者自己。
    *   判据：它是以用户身份发起、默认查全量的跨用户查询（细节见 5.5.4 表后的说明）。
*   **用户（`users`）** —— `SELF` 档，**只落了列表这一类**。
    *   落点：`UserRepo.list_visible(actor_id, scope)`，`scope == 'SELF'` 时加 `where id == actor_id`；调用方是 `GET /system/user/list`。
    *   判据：它以用户身份发起、默认查全量。同一对象的详情 / 分页 COUNT / 导出聚合三类入口还没建（见 5.3 的告诫）。

**粒度判据**：

*   同一个数据对象，各角色的可见范围**都一样** → 不必单独定档，跟着角色划分走即可。
*   同一个角色，对 A 对象要全量、对 B 对象只要本人 → **当前模型表达不了**：
    *   原因：`sys_role.data_scope` 是角色级单值，不是「角色 × 数据对象」二维。
    *   绕法：要么拆成两个角色，要么把 `data_scope` 改成按对象存——后者不在这份设计里，先记下。

> 数据权限**不进 §4.2 契约表**——契约表是功能权限的口径（见 4.2 注）。

---

## 四、权限点设计与前后端契约

### 4.1 权限点命名规范
**格式**：`模块:资源:操作`（如 `system:user:add`）

*   **原则 1**：按业务功能定义，不按 API 定义。一个权限点可能对应多个 API，一个 API 也可能被多个权限点复用。
*   **原则 2**：粒度适中。一个业务操作对应一个权限点（如“查看用户列表”对应 `system:user:list`）。
*   **原则 3**：前后端共用同一套标识符，字符串必须完全一致。
*   **原则 4（模块位决定归谁）**
    *   `client` = **agent-client 的前端**。
        *   `permission_type = C` → 侧边栏入口，必须在 `ENTRY_VIEWS` 里有键，没有 `path` / `component`（见 4.2 表 19–23、34 行）。
        *   `permission_type = F` → 页面内按钮，用 `<Permi>` 包（见 6.5）。
    *   `system` 等模块位 = 服务端 / 带路由的后台，`C` 才配 `path` + `component`。

### 4.2 完整的权限点字典表（前后端契约表）
这张表由**后端主导起草，前端评审补充**，是**后端权限清单（`catalog.py` 的 `PERMISSIONS`）的人类可读版**——落库不靠手写 SQL，由启动对账自动生成（见 4.4）。

> 注意这张表是**全系统的可授予域**，不是某个用户的权限。用户实际有什么，见 4.5。

| # | 业务功能 | 权限点（perms） | permission_type | path | component | 按钮文案 | parent_id | 对应后端 API |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | 系统管理 | NULL | M | `/system` | `Layout` | 系统管理 | 0 | — |
| 2 | 用户管理 | `system:user:list` | C | `/system/user` | `system/user/index` | 用户管理 | 1 | `GET /api/system/user/list` |
| 3 | 查看详情 | `system:user:query` | F | — | — | 详情 | 2 | `GET /api/system/user/{id}` |
| 4 | 新增用户 | `system:user:add` | F | — | — | 新增 | 2 | `POST /api/system/user` |
| 5 | 编辑用户 | `system:user:edit` | F | — | — | 编辑 | 2 | `PUT /api/system/user` |
| 6 | 删除用户 | `system:user:remove` | F | — | — | 删除 | 2 | `DELETE /api/system/user/{id}` |
| 7 | 导出用户 | `system:user:export` | F | — | — | 导出 | 2 | `GET /api/system/user/export` |
| 8 | 重置密码 | `system:user:resetPwd` | F | — | — | 重置密码 | 2 | `PUT /api/system/user/resetPwd` |
| 9 | 技能 Hub 管理 | `system:skill:list` | C | `/system/skill` | `system/skill/index` | 技能 Hub | 1 | `GET /api/skills/hub` |
| 10 | 新增技能 | `system:skill:add` | F | — | — | 新增 | 9 | `POST /api/skills/hub` |
| 11 | 编辑技能 | `system:skill:edit` | F | — | — | 编辑 | 9 | `PUT /api/skills/hub/{skill_id}` |
| 12 | 删除技能 | `system:skill:remove` | F | — | — | 删除 | 9 | `DELETE /api/skills/hub/{skill_id}` |
| 13 | MCP Hub 管理 | `system:mcp:list` | C | `/system/mcp` | `system/mcp/index` | MCP Hub | 1 | `GET /api/mcp/hub` |
| 14 | 新增 MCP | `system:mcp:add` | F | — | — | 新增 | 13 | `POST /api/mcp/hub` |
| 15 | 编辑 MCP | `system:mcp:edit` | F | — | — | 编辑 | 13 | `PUT /api/mcp/hub/{server_id}` |
| 16 | 删除 MCP | `system:mcp:remove` | F | — | — | 删除 | 13 | `DELETE /api/mcp/hub/{server_id}` |
| 17 | 全局统计 | `system:stats:view` | C | `/system/stats` | `system/stats/index` | 全局统计 | 1 | `GET /api/system/stats/view` |
| 18 | 审计日志 | `system:audit:list` | C | `/system/audit` | `system/audit/index` | 审计日志 | 1 | `GET /api/admin/audit` |
| 19 | Skills 配置入口 | `client:skills:config` | C | — | — | Skills 配置 | 0 | — |
| 20 | MCP 配置入口 | `client:mcp:config` | C | — | — | MCP 配置 | 0 | — |
| 21 | 记忆配置入口 | `client:memory:config` | C | — | — | 记忆配置 | 0 | — |
| 22 | 专家和专家团入口 | `client:expert:config` | C | — | — | 专家和专家团 | 0 | — |
| 23 | 角色权限配置入口 | `client:rbac:config` | C | — | — | 角色权限配置 | 0 | — |
| 24 | 角色管理 | `system:role:list` | C | `/system/role` | `system/role/index` | 角色管理 | 1 | `GET /api/system/role/list` |
| 25 | 查看权限点清单 | `system:permission:list` | F | — | — | 查看权限点清单 | 24 | `GET /api/system/permission/list` |
| 26 | 查看授权 | `system:role:query` | F | — | — | 查看授权 | 24 | `GET /api/system/role/{role_id}/permissions` |
| 27 | 修改授权 | `system:role:grant` | F | — | — | 修改授权 | 24 | `PUT /api/system/role/{role_id}/permissions` |
| 28 | 修改数据范围 | `system:role:edit` | F | — | — | 修改数据范围 | 24 | `PUT /api/system/role/{role_id}` |
| 29 | 部门管理 | `system:dept:list` | C | `/system/dept` | `system/dept/index` | 部门管理 | 1 | `GET /api/system/dept/list` |
| 30 | 新增部门 | `system:dept:add` | F | — | — | 新增 | 29 | `POST /api/system/dept` |
| 31 | 编辑部门 | `system:dept:edit` | F | — | — | 编辑 | 29 | `PUT /api/system/dept/{dept_id}` |
| 32 | 删除部门 | `system:dept:remove` | F | — | — | 删除 | 29 | `DELETE /api/system/dept/{dept_id}` |
| 33 | 分配成员 | `system:dept:assign` | F | — | — | 分配成员 | 29 | `PUT /api/system/user/{user_id}/dept` |
| 34 | 部门配置入口 | `client:dept:config` | C | — | — | 部门配置 | 0 | — |
| 35 | 分配用户 | `system:role:assign` | F | — | — | 分配用户 | 24 | `PUT /api/system/user/{user_id}/roles` |

> **第 23–28 行是「角色管理」页**（这个权限点的名字仍是「角色权限配置」，见上表第 23 行——侧边栏那一页叫「角色管理」）：23 是 agent-client 的入口，24–28 是它调用的五个接口。23 授予谁，谁就能在客户端里配其余角色的权限。
>
> **第 29–33 行是部门层，34 是它的客户端入口**。29–33 是服务端接口（部门增删改 + 挪人），34 是 agent-client 侧边栏的「部门配置」页 —— 谁拿到 34 谁才看得见那一页。`system:user:list`（第 2 行）与 `system:user:add`（第 4 行）的接口是为这一页落的 —— 一个读人、一个给部门加人；其余 `system:user:*` 仍待建。
>
> **第 35 行挂在「角色管理」下（parent 24），但服务的是部门配置页**：给用户挂角色这件事属于角色命名空间（同 33 行挂在部门下的切法），可它落在 client 那一侧的那个成员表上。所以「部门配置」页要显示角色，除了 34 还得有 24（读角色名）—— 或 35（可写）。

*   **`#` 是给人读的逻辑序号，不是库里的 `id`**。
    *   `id` 由 `AUTO_INCREMENT` 生成，**种子不要手写**：PG 手写 id 不推进自增序列，之后正常插入就会撞主键。
    *   落库时按 `perms` 反查真实 id，`parent_id` 也在这时解析成真实 id（见 4.4）。
*   **第 9–18 行、24–28 行、29–33 行和 35 行是 `system` 模块位（带路由的后台）**，接口是否已存在要分开看：
    *   **已存在的**：2、4（用户列表与建号，只为部门配置页落的，见下）、9、13（对应 `/skills/hub`、`/mcp/hub`，各 1 读 3 写）、18（`/admin/audit`）、24–28（`/system/role/*`、`/system/permission/*`）、29–33（`/system/dept/*`、`/system/user/*/dept`）和 35（`/system/user/*/roles`）。
    *   **待建的**：3、5–8 的用户管理其余 5 个点、17 的全局统计。点先定下来，接口随后写。
    *   **切法和 2–8 行一致**：列表 + 增删改各一个点。判据是 3.1 的粒度——「能装技能但不能下架技能」是真实存在的授权需求，拆开才表达得了。
    *   **`path` / `component` 是给将来那个后台页面留的占位**：这些页面还没建，先按后台约定填上，不影响先挂 API 权限。
*   **第 19–23 行和 34 行没有对应后端 API**。
    *   原因：`client:*` 是纯前端入口，在 agent-client 里就是一个侧边栏入口，`path` / `component` 留空（见 6.4），所以 `sys_permission_api` 里不会有它们。
    *   这正是「权限清单必须由代码声明、不能靠扫路由生成」的理由（见 4.4）。

**本表会随代码变，且以代码为准。**

权限点不是录入一次就固定的静态数据。三种变化都由**发版**带来：

*   **新增功能** → 加一行（清单 `catalog.py` 里多一条，路由再去引用它）。
*   **删功能 / 改名** → 下线一行。改名本质是删 + 增：字符串变了就是另一个权限点。
*   **粒度调整** → 一个拆成两个（拆分条件见 3.1 的粒度判据）。

由此定两件事：

1.  **权威在代码，不在此表。**
    *   `perms` 字符串的唯一声明处是后端清单 `catalog.py`（见 4.4），路由上的 `require_permission(...)` 和前端入口表都只是**引用**它。本表是这份清单在文档里的镜像。
    *   注意区分两种「管理界面」：**授权**有界面（客户端侧边栏的「角色管理」页，把清单里的点勾给角色，见 4.2 表 23–28 行）；**新增权限点**没有——它必须随代码走一遍 §4.6 那五步。库永远只是清单的镜像，不是可凭空长出新点的目录。
2.  **因此种子要「每次启动对账」，不是「表空才插一次」。** 按 `perms` 增改、把代码里已没有的下线，保证库里始终等于代码。若只在初始化时插一次，之后每新增一个权限点库里都会落后——校验时查不到，就是 403。

> ⚠️ **拆分权限点要单独处理**：原来的 `system:user:edit` 拆成两个点后，已经授权过的角色不会自动获得新点，一上线就全部失去权限。对账只保证字典表与代码一致，**授权迁移**是发版时的人工动作，别漏。

### 4.3 制定流程与角色分工
1.  **产品经理**：提供业务功能清单（页面、按钮、操作）。
2.  **后端（主导）**：根据业务功能，按命名规范制定权限点、绑定 API——都写进 `catalog.py`，落库交给启动对账。
3.  **前端（评审）**：确认权限点覆盖度，补充 `permission_type`、`path`、`component`、`parent_id`、按钮文案。
4.  **共同定稿**：冻结字典表，前后端严格遵守。
5.  **落入代码**：写进后端权限清单 `catalog.py`（唯一声明处），启动对账自动同步到 `sys_permission` + `sys_permission_api`。不手写 SQL。

### 4.4 权限清单是唯一声明处

权限点只有**一个**声明处：后端 `catalog.py` 的 `PERMISSIONS`。

> ⚠️ **先躲开一个撞名**：`server/permissions.yaml` 和 `server/tools/permission.py` 是 `13.1-权限控制.md` 那套 Agent 沙箱 / 工具审批策略，跟本文档的 RBAC 毫无关系。清单别也叫 `permissions`——建议 `server/authz/catalog.py`；`require_permission` 放 `server/api/deps.py`（见 5.1）。否则 grep 一个词出来两套东西。

其他地方的 perms 字符串都只是**引用**：

*   路由上的 `require_permission("...")`。
*   前端的入口表和 `<Permi>`。

声明处不能是路由的原因：路由只表达得了「这个 API 要什么权限」，表达不了**没有 API 的权限点**（纯前端入口，就是上表 19–23 行）、中文名和层级。所以清单单独立一处，路由负责引用它。

```
后端 catalog.py（唯一声明处）
   ├─ 启动对账 ──▶ 字典表 sys_permission / sys_permission_api
   │                    │
   └─ 启动校验 ──▶ app.routes 的 require_permission(...)   └─ GET /auth/me ──▶ { permissions: [...] }
前端 ENTRY_VIEWS { perms → 组件 } ──按 permissions 取交集──▶ 渲染
前端 <Permi perms={[...]}>（按钮）─────── CI 双向比对 ──────┘
```

1.  **后端 → 字典表**：启动时两步。
    *   `sync_catalog()`：按 `perms` upsert，并把清单里已经没有的下线。
    *   `verify_route_refs()`：扫 `app.routes` 取实际引用的 perms 与清单比，**引用不在清单里 → 抛错、不启动**。
    *   比对前**必须归一化路径**，否则两边永远对不上、全是假告警：
        *   **剥 `/api`**：契约表写的是客户端视角的 `/api/...`，服务端路由没有这个前缀（见 5.2）。
        *   **占位符统一**：契约表写 `{id}`，FastAPI 里常是 `{user_id}` / `{skill_id}` / `{server_id}`，两边都折成 `{}` 再比。

    为什么（见 4.2）：字典表是代码的镜像，落后就会 403。
2.  **字典表 → 前端**：`/auth/me` **只加 `permissions`，不下发入口清单**（见 5.4）。前端自己持有 `perms → 组件` 映射，拿 `permissions` 取交集渲染（见 6.4）。
3.  **前端 ↔ 清单（CI，双向）**：抽前端源码里的 perms 字符串（`ENTRY_VIEWS` 的键 + `<Permi perms={[...]}>`），与 `catalog.py --json` 的导出双向比对：
    *   **正向**：前端用了、清单里没有 → 构建失败（拼错字、用了已下线的点）。
    *   **反向**：清单里「模块位 = `client` 且 `permission_type = C`」的入口，`ENTRY_VIEWS` 里没有对应的键 → 构建失败。
        *   专拦「后端加了入口、前端漏配」。
        *   漏配在运行时是**完全静默**的：渲染遍历的是 `ENTRY_VIEWS` 的键，漏掉的 perms 根本进不了遍历集合，连 admin 也看不到（见 4.5）。
    *   反向的判据用清单里已有的 `permission_type`（C=菜单/入口，F=按钮），不发明新的命名后缀；`system:*` 那几条 C 因为模块位不是 `client`，天然不误报。

两条配套要点：

*   **新增一个前端入口 = 前后端都要动**：清单加一行 + 前端 `pages.ts` 加一行（还要补图标和 `AppLayout` 的渲染分支，完整四处见 6.4）。
    *   不是冗余：权限点必须进了字典表才能挂到角色上（`sys_role_permission` 要 `permission_id`），否则管理员无从授权，该 perms 永远不会出现在任何人的 `permissions` 里。
    *   反过来让前端定义权限点也不行：客户端不可信。
*   **前端遇到不认识的 perms 不用特殊处理**。
    *   渲染遍历的是 `ENTRY_VIEWS` 的键，不认识的 perms 进不了遍历集合，既不会崩、也无从告警。
    *   「新后端 + 旧客户端」是常态（后端加了 `client:foo:config`，老客户端不认识），表现就是**该入口不显示**——这是可接受的。
    *   真正要防的是**同一份代码里前端漏配**，那靠 CI 反向比对拦（见 6.4）。

> ⚠️ **下面这张表描述的是要搭的东西，不是「已有一步 CI」**。现状：仓库里没有 CI 配置，`agent-client/package.json` 没有 typecheck / test 脚本，`catalog.py --json` 也不存在——两处比对都得从零写。建议落点：
> *   **后端正向 / 反向比对** → 与现有 pytest 同级写用例（`i-work/tests/`），跑测试即校验。
> *   **前端扫 `ENTRY_VIEWS` 键 + `<Permi perms={[...]}>`** → 一段 node 脚本，挂到 `npm run build` 前置。

每处漂移要么**响亮**（启动失败 / 构建失败），要么是**明确可接受**的，不能有说不清的静默失效：

| 处 | 谁保证 | 漂移表现 |
| :--- | :--- | :--- |
| 路由引用 | 启动校验 | 启动失败 |
| 字典表 | 启动对账 | 不存在漂移 |
| 前端用了清单里没有的 perms | CI 正向比对 | 构建失败 |
| 清单有、但前端 `ENTRY_VIEWS` 漏配 | CI 反向比对 | 构建失败 |
| 旧客户端遇新权限点 | — | 该入口不显示（可接受） |

### 4.5 三层的关系：清单 / 角色授予 / 用户权限

三个东西都叫"权限"，但不是一个东西：

| | 是什么 | 在哪 | 什么时候定 |
| :--- | :--- | :--- | :--- |
| **权限清单** `catalog.py` 的 `PERMISSIONS` | 全系统**一共有哪些**权限点可被授予 | 代码 | 开发期，跟代码走 |
| **角色的权限点** `sys_role_permission` | 某个**角色**被授予了哪些 | 数据库，管理员配 | 运行期，随时可改 |
| **用户的 `permissions`** | 这个人**实际有**哪些 | `/auth/me` 下发的数组 | 运行期，每次请求算 |

层层取子集：

```
清单（全集，声明处）
  ⊇ 角色授予（管理员从清单里勾）
    ⊇ 用户的 permissions（= 他所有 status=1 角色的并集；admin 短路成全集）
```

用吃饭打比方：清单是**菜单上所有的菜**（写死在菜单上，改菜单要发版），角色的授予是**套餐里含哪几道**（后厨随时调），用户的 `permissions` 是**这个人今天能点的菜**。

**正常使用走一遍**：alice 是 `user` 角色，bob 是 `admin` 角色，同一天各开各的客户端。前提：清单 35 条；`user` 角色被授了 skills / mcp / memory 三条入口；没人动过配置。

1.  **alice 打开客户端** → 启动即调 `GET /auth/me` → 服务端跑 `load_user_permissions`：
    *   查她的角色（`user`）→ 查这个角色被授予的点 → 过滤 `status=1`。
    *   返回三条，`permissions = [client:skills:config, client:mcp:config, client:memory:config]`。

    > 清单那 35 条对她没有直接意义，剩下 32 条她一整天碰不到。结果按 `user_id` 缓存在进程内存（见 5.1），她一天点多少次都不重查库。

2.  **alice 的侧边栏渲染**：`ENTRY_VIEWS` 六个键按 `section` 分组后逐条查 `hasPermi` → 「配置」组下 skills / mcp / memory 显示，「专家和专家团」与「系统管理」整组（「角色管理」「部门配置」）**不渲染**。
3.  **alice 点「Skills 配置」** → `configPage = 'client:skills:config'` → 渲染 `<SkillsConfig />` 浮层。
    *   点入口本身没有鉴权开销——前端只是查了个数组。
    *   真正的边界在接口那头：挂了 `require_permission` 的后端会**独立复核一次**，不信前端藏没藏（见 1.2）。
4.  **bob 在另一台机器上打开同一个发行包** → `/auth/me` → `admin` 短路 → 返回清单全集 35 条 → 他的六个入口全显示。
5.  **差异从哪来**：同一份二进制、同一份 `ENTRY_VIEWS`，差别**只在 `/auth/me` 返回的那个数组里**。这就是"权限在数据里、不在代码里"——加人、调权限都不用重新打包客户端。

三层的活跃度其实很不对称：

| 层 | 正常使用时 |
| :--- | :--- |
| **清单** | **完全静止**。只在构建期（CI 比对）和启动期（对账）各被读一次，用户一整天的操作根本不碰它 |
| **角色授予** | 启动和登录时**只读**。管理员不点保存，它一行都不变 |
| **用户的 `permissions`** | **唯一每次都在动的**。每次登录（或重拉 `/auth/me`）算一次，然后被前端反复读 |

> 所以别看 4.2 / 4.4 花了很大篇幅讲清单——**清单在日常使用中是个静态背景**，天天跑的是后面两层。4.2 那张契约表是「全系统的可授予域」，不是「某个用户的权限」。

### 4.6 开发期新增一个权限点：走一遍

4.5 说的是静止状态。真到了要加功能的时候，怎么改？假设要给客户端加一个「模型配置」页，页里有个「新增模型」按钮。

一个需求拆出两个权限点——正好把前端两条路径各走一遍：

| 权限点 | 类型 | 谁用它 |
| :--- | :--- | :--- |
| `client:model:config` | C（入口） | 前端 `ENTRY_VIEWS` 加一个键 |
| `client:model:add` | F（按钮） | 前端 `<Permi>` 包按钮；后端路由挂 `require_permission` |

补进 4.2 那张契约表就是这两行（接在第 35 行之后，写法和 19–23 行一致）：

| # | 业务功能 | 权限点 | permission_type | path | component | 按钮文案 | parent_id | 对应后端 API |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 36 | 模型配置入口 | `client:model:config` | C | — | — | 模型配置 | 0 | — |
| 37 | 新增模型 | `client:model:add` | F | — | — | 新增模型 | 36 | `POST /api/settings/model/add` |

**改的顺序，五步：**

1.  **后端清单加两行**（`catalog.py` 的 `PERMISSIONS`）。这是唯一声明处，先动它。
2.  **后端路由引用 F 点**：新增 `POST /settings/model/add`。
    *   签名里加 `_: None = Depends(require_permission("client:model:add"))`（见 5.2）。
    *   C 点没有任何路由引用它——它没有 API，就是个纯前端入口。
3.  **前端入口表加一个键**：`ENTRY_VIEWS` 里加 `'client:model:config'`（见 6.4）。
4.  **前端按钮包 `<Permi>`**：`<Permi perms={['client:model:add']}>`（见 6.5）。
5.  **文档契约表补两行**（4.2）。它是清单的镜像，代码动了它就动。

**少做一步会被谁拦，三道闸门各管一段：**

| 漏了 | 谁拦 |
| :--- | :--- |
| 后端清单（第 1 步） | 路由一挂上，启动校验就抛错、不启动；前端一写这些 perms，CI 正向比对构建失败 |
| 前端 `ENTRY_VIEWS`（第 3 步） | CI 反向比对构建失败 |
| 前端 `<Permi>`（第 4 步） | 构建期没人拦。表现是按钮对谁都显示、点击被后端 403 打回——不是安全洞，但白挨一次报错 |

**然后发布，看它怎么生效：**

1.  **后端发版** → 启动时 `sync_catalog()` 把两行 upsert 进字典表，清单从 35 条变 37 条。
2.  **此刻还没有人能新用上**。清单多了只是「这道菜上了菜单」——
    *   `sys_role_permission` 里没有这两条，非 admin 角色的 `permissions` 一点没变。
    *   alice 下次开客户端，`/auth/me` 还是那三条，「模型配置」不显示。
    *   bob 是 admin，短路成全集，立刻看到新入口——他就是用来验功能的那个人。
3.  **管理员授权**：在客户端侧边栏的「角色管理」页（凭 `client:rbac:config` 进入，见 4.2 表第 23 行）里，把这两个点勾给 `user` 角色那一列、保存，落一行 `sys_role_permission`。
4.  **alice 生效**：她重开客户端（或等 5.1 那个缓存过期），`/auth/me` 多返回 `client:model:config`，入口和按钮一起出现。
5.  **后端独立复核**：前端漏判也不打紧，`POST /settings/model/add` 自己会再查一次（见 1.2）。

> 两条容易忘的：**用老客户端的用户看不到新入口**（`ENTRY_VIEWS` 在代码里，不重装就是旧的，见 4.4）；**授权是运行期的人工动作**，开发只负责上面那五步代码，谁也没法替他做。

---

## 五、后端实现方案（Python / FastAPI）

### 5.1 权限校验：一个 FastAPI 依赖，不是装饰器

权限校验写成 `Depends` 依赖，与既有的 `get_current_user`（`server/api/deps.py`）同一种形态。

不用装饰器，理由很实际：

*   项目里 20+ 个 handler 全是依赖注入式签名，装饰器拿不到 Request 和 DB session。
*   硬插进来就是两套鉴权写法并存。

```python
# server/api/deps.py
from uuid import UUID
from fastapi import Depends, HTTPException

def require_permission(*perms: str):
    """用法：_: None = Depends(require_permission("system:user:add"))"""
    async def _check(
        user_id: UUID = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        owned = await load_user_permissions(db, user_id)   # sys_user_role → sys_role_permission → sys_permission，去重成 set
        if not any(p in owned for p in perms):
            raise HTTPException(
                403,
                detail={"error": "PERMISSION_DENIED", "message": "权限不足"},
            )
    _check._perms = perms   # 见下：启动校验靠它把闭包里的实参捞回来
    return _check
```

*   **`_perms` 是给启动校验留的把手**：`verify_route_refs()` 遍历 `app.routes` 时拿到的是 FastAPI 的 `dependant`，里面只存着 `_check` 这个函数对象——闭包捕获的 `perms` 从外面看不见。挂到函数属性上，校验器才能 `getattr(call, '_perms', ())` 取回来（见 4.4）。少挂这一行，校验静默失效（扫到 0 条引用、永远不报漂移）。

*   **错误体跟项目既有约定走**
    *   形如 `detail={"error": <码>, "message": <人话>}`（见 `server/api/auth_routes.py`）。
    *   客户端按 `detail.error` 分流，不解析中文文案。
*   **`get_current_user` 只验签、不查库**，所以权限得自己查。
    *   这个查询每个请求都要跑，结果按 `user_id` 缓存，TTL 取 15 分钟以内（与「封号后旧 token 最长 15 分钟仍可用」同一量级，见 doc 18-8.5），不必为它单独设计失效。
    *   缓存是**进程内内存**（`readme.md` 里 uvicorn 没带 `--workers`，就一个进程），将来上多 worker / 多实例要换成 Redis。
    *   数据范围（见 5.3）和权限点**共用一个缓存项**：都是「按 `user_id` 查角色」，多存一份没有意义。缓存项里还捎带用户自己的 `dept_id`，按部门过滤要用。
    *   **授权变更要写穿**：管理面增删 `sys_role_permission` / `sys_user_role` 之后，主动清掉受影响用户的缓存项。
        *   不清的后果很阴：前端点了「重拉 `/auth/me`」、后端还在吐旧值，用户界面上的入口和实际权限对不上，且 15 分钟内自愈不了。见 6.6。
*   传多个权限点表示**任一满足**。

`load_user_permissions` 自己的语义三条：

*   **多角色取并集去重**——一个用户挂多个角色时权限是合集，不是交集。
*   **只算生效的**：`sys_role.status = 1` 且 `sys_permission.status = 1`，任一被禁用即不参与。
*   **全量角色短路返回全集**：`role_key` 落在 `catalog.ALL_PERMS_ROLE_KEYS`（`admin`、`dept_admin`）就返回全部权限点。
    *   不这么做，管理员哪天把自己的权限删了，就再也进不去管理界面（鸡生蛋）。
    *   选短路、而不是「给这些角色显式挂满权限点」的理由：后者以后每加一个权限点都要记得补一行，忘一次就静默少个入口。
    *   名单在代码里，加第三个全量角色改 `catalog.py` 一行；权限配置页上这几列只读恒勾选。
    *   **短路只针对权限点**，数据范围仍取角色行上的 `data_scope`（见 5.3）—— `admin` 与 `dept_admin` 的全部区别就在这一个字段上，所以 §5.3.1 第四条守卫是它们的承重墙。

`load_user_scope(db, user_id)` 与它同源——同一次查询顺手把用户自己的 `dept_id` 和角色行的 `data_scope` 取出来：

*   **多角色取最宽的那档**：`ALL` > `DEPT` > `SELF`（见 5.3 那张表）。
*   **不做 admin 短路**：数据范围只认角色行（见 2.2），admin 的 `ALL` 由种子显式写在角色上。
*   **没认出来的取值按 `SELF` 兜底**（fail-closed，见 5.3）。
*   要连 `dept_id` 一起拿的消费者用 `load_user_scope_ctx(db, user_id)`，返回 `(scope, dept_id)`；按部门算可见范围的子树用 `visible_dept_ids(db, dept_id)`。

### 5.2 接口使用示例

```python
# server/api/system_routes.py
from uuid import UUID
from fastapi import APIRouter, Depends

router_system = APIRouter(prefix="/system", tags=["system"])

@router_system.get("/user/list")
async def list_users(
    user_id: UUID = Depends(get_current_user),
    _: None = Depends(require_permission("system:user:list")),
    db: AsyncSession = Depends(get_db),
):
    scope, dept_id = await load_user_scope_ctx(db, user_id)   # 服务端按角色算（见 5.3）
    dept_ids = await visible_dept_ids(db, dept_id) if scope == "DEPT" else None
    return await user_repo.list_visible(user_id, scope, dept_ids)
```

> **服务端路由没有 `/api` 前缀**
> *   `/api` 是客户端的 base：持久化的 `Settings.apiBaseUrl` 初值是**空串**，每个调用点再 `settings.apiBaseUrl || '/api'` 兜底（`services/api.ts` 的 `DEFAULT_BASE_URL`）。由 electron-vite dev proxy 转发时剥掉，见 `agent-client/electron.vite.config.ts`。
> *   §4.2 契约表里的 `GET /api/system/user/list` 是**客户端视角的完整地址**，服务端实际是 `/system/user/list`。

### 5.3 数据权限过滤

三档，按宽到窄（`server/authz/service.py` 的 `SCOPE_ORDER`）：

| `data_scope` | 可见范围 | 内置角色 |
| :--- | :--- | :--- |
| `ALL` | 全部 | 超级管理员 |
| `DEPT` | 本部门 + 全部下级部门的人 | 管理员 |
| `SELF` | 仅本人 | 普通用户 |

*   **一个人挂多个角色时取最宽的一档**，如 [`user`, `dept_admin`] → `DEPT`。
*   **`DEPT` 的子树按 `sys_dept.parent_id` 现算**，不缓存部门集合。
*   **未知取值一律按 `SELF` 算**（fail-closed）。
    *   写错一个 scope 不能等于放开全量 —— 早先的实现是「`!= "SELF"` 就不加过滤条件」，那才是 fail-open。
*   **`scope` 一律从角色行算**，不从客户端来（见 3.2）——前端传个 `ALL` 就绕过了。
*   **缓存项带 `dept_id`** —— 按部门过滤得知道主体自己在哪个部门（见 5.1）。

过滤写在 Repo 方法里，照项目现有的 Repo 模式（`server/storage/postgres.py` 的 `UserRepo(session_factory)`，`async with self._sf() as db`）：

```python
# server/storage/postgres.py
class UserRepo:
    async def list_visible(self, actor_id, scope, dept_ids=None) -> list[OrmUser]:
        stmt = select(OrmUser)
        if scope == "ALL":
            pass
        elif scope == "DEPT":
            stmt = stmt.where(OrmUser.dept_id.in_(dept_ids or []))
        else:                       # SELF，以及任何没认出来的取值
            stmt = stmt.where(OrmUser.id == actor_id)
```

> 这个方法只是 §3.2 落点清单里的一行。真正的活是把「查用户这个对象」的**四类入口**都落齐：列表、详情、COUNT 分页总数、导出/统计聚合。本节只落了**列表**（`GET /system/user/list`），其余待建。

**接 DEPT 之后要清缓存的三处**（见 5.1）：

| 动作 | 清什么 | 理由 |
| :--- | :--- | :--- |
| 改某人所属部门 | 该用户 | 他换了子树 |
| 改部门父节点（挪树） | 全部 | 受影响的人反查不出来 |
| 删部门 | 全部 | 同上 |

**只有 `GET /system/user/list` 接了 DEPT。** 会话归属（`require_session_access`）与审计过滤（`GET /admin/audit`）仍是「非本人且非 `ALL` → 拒绝」，不动 —— 接 DEPT 等于让部门管理员能读下属的会话内容与审计日志。

#### 5.3.1 写侧的守卫

角色与部门是**全局字典**，没有部门归属 —— 这两处的写接口不能靠 `data_scope` 自动裁剪，得逐条守卫。建号同理：人是写进某个部门的，落哪个部门得判。判据都是「非 `ALL` 主体不许越出自己那棵子树」：

| 接口 | 守卫 | 拒时 |
| :--- | :--- | :--- |
| `PUT /system/user/{id}/roles` | 目标用户要在自己范围内 | 404（不给对方「这人存在」的信号） |
| `PUT /system/user/{id}/roles` | **新增**的角色 `data_scope` 不得比自己宽 | 400 `ROLE_SCOPE_EXCEEDED` |
| `PUT /system/user/{id}/dept` | 目标用户**与目的地部门**都在范围内 | 404 |
| `POST /system/user` | 落的部门要在自己子树内（`parent_id` 不参与，这里的范围就是那个部门本身） | 400 `DEPT_NOT_FOUND` |
| `PUT /system/role/{id}` | 目标角色与**新数据范围**都不得比自己宽 | 400 `ROLE_SCOPE_EXCEEDED` |
| `POST/PUT/DELETE /system/dept/*` | 目标部门与**新父节点**都在自己子树内（`parent_id = 0` 算范围外） | 400 `DEPT_NOT_FOUND` |

*   第五条是最要紧的一条：`dept_admin` 与 `admin` **共用同一套权限点**（见 5.1、5.5），只差一个 `data_scope`。没有它，管理员把自己那行改成 `ALL` 就是超管。
*   第六条堵的是「改树等于改自己的可见集」：`DEPT` 的定义就是本部门及下级，把范围外的部门挂进来，那批人就进他的可见集了 —— 不需要任何提权动作。
*   摘掉自己的**全部**全量角色也拦：那是把自己锁在门外。只降一档（超管 → 管理员）放行。

### 5.4 权限下发：扩 `/auth/me`，不新造接口

客户端 `authStore` 已经在调 `GET /auth/me` 取用户，权限搭这个响应回去最省事。但**不是零改造**，两处得一起改（`agent-client/src/renderer/src/stores/authStore.ts`）：

*   `fetchMe()` 现在只读 `body.user`，要一并读 `body.permissions`。
*   `fetchMe()` 只在 `bootstrap` 里被调一次，`login` **不调**——不改的话，首次登录当天 `permissions` 一直是 `null`，侧边栏一个入口都不显示，得重启才有。登录路径也要补一次拉取。

```python
# server/api/auth_routes.py
@router_auth.get("/me")
async def me(
    user_id: UUID = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
):
    user = await service.get_user(user_id)
    return {
        "user": serialize_user(user),
        "permissions": sorted(await load_user_permissions(db, user_id)),   # ["system:user:list", ...]
    }
```

*   **不下发菜单树，也不下发入口清单**
    *   前端自己持有 `perms → 组件` 映射（见 6.4），下发的 `permissions` 只用来查这张表。
    *   所谓「服务端下发入口、不发版就能加入口」的好处在这里等于零——agent-client 是桌面客户端，装的是用户本地那份代码，本来就要发版升级（见 4.4）。
*   **登录响应体保持现状**：`POST /auth/login` 返回 `{"access_token", "refresh_token", "expires_in", "user"}`。
    *   权限**不要塞进 token** —— 权限被改后要等 token 过期才生效，而扩在 `/auth/me` 上是每次开客户端就重新拉。
*   改动后 `18-登录认证模块` 的接口表需同步 `permissions` 字段。

### 5.5 落地顺序与种子机制

照这个顺序做，四步：

**1. 建表**

*   新增 `server/alembic/versions/020_rbac.py` 建五张表（`users` 已存在，见 2.2），**手动** `alembic upgrade head` 应用。
*   部门层另起一个 `021_dept.py`：建 `sys_dept`、给 `users` 加 `dept_id`。同样是手动 upgrade。
*   本项目建表只走 Alembic，`main.py` 不调 alembic，启动不会自动迁移。

**2. 种子**

*   写在 `server/db/seed.py`，沿用现有「挂 lifespan、每次启动都跑」的方式（`main.py` 里 `seed_hub_data` / `seed_expert_hub_data` 就是挂在 lifespan 上跑的）。
*   但要**分两段，写法不同**：
    *   **字典表**（`sys_permission` / `sys_permission_api`）→ **upsert 对账**：按 `perms` 增改、把代码里已经没有的下线。理由见 4.2。
    *   **业务表**（`sys_role` / `sys_role_permission` / `sys_user_role`）→ **insert-if-missing**：按业务键（`role_key`）逐行查，缺了才插，**绝不覆盖已有行**。
        *   理由：种子每次启动都会跑到，若 upsert，管理员改过的角色名、禁用过的角色、调整过的授权，会在下一次重启时被静默抹回种子值。

*   **部门层单独一段 `seed_dept`**（见 2.2），同一套两段规则：
    *   **默认部门** → insert-if-missing：按业务键 `dept_key = 'default'` 查，缺了才插 `parent_id=0` 的「默认部门」。已存在则一行不改 —— 管理员把它改名成「总部」是正当操作。
    *   **用户归属** → 每次启动把 `dept_id IS NULL` 的人补成默认部门。
        *   这就是「后台不建组织架构，大家也都有默认部门」的兜底语义。
        *   只覆盖**存量**：新号由建号接口直接写上管理员选的部门（见 4.2 第 4 行），不走这条回填。
    *   **顺序：要排在 `seed_rbac` 之后** —— 首个管理员账号是那一步建的，跑早了它第一次会漏掉回填。

> ⚠️ 别用现有种子那种「整表 `count == 0` 才插」的写法：表里只要先有了一行，`admin` 角色就可能永远建不出来。

**3. bootstrap：谁是第一个管理员**

种子直接建一个 `admin` 账号，挂上 `admin` 角色 —— 第一个管理员由种子给出，不靠任何注册路径。

*   种子建 `admin` / `dept_admin` / `user` 三个角色，加 4.2 那 35 行权限点。
    *   **三个角色都必须显式写 `data_scope`**：`admin` = `ALL`、`dept_admin` = `DEPT`、`user` = `SELF`。
        *   漏了就会吃 `sys_role` 的默认 `SELF`——admin 权限全通、数据却只剩自己（见 2.2 / 5.3）。
    *   显示名：`admin` 叫「超级管理员」、`dept_admin` 叫「管理员」、`user` 叫「普通用户」。
        *   库里 `admin` 那行历史叫「管理员」，种子按 `role_name` 对账改名；**只在名字不等于目标时才写库**（同 insert-if-missing 的道理，见 5.5.2）。
*   `user` 角色默认授三条纯前端入口：`client:skills:config`、`client:mcp:config`、`client:memory:config`；**不给** `client:expert:config`（与 4.5 的例子对齐）。
    *   只在这个角色**一条授权都没有**时给一次，之后超管随便调。
*   `admin` / `dept_admin` **授权行一行不写** —— 它们走 §5.1 的全量短路，天然拥有全部权限点。
    *   两者的区别只有角色行上的 `data_scope`：`ALL` 与 `DEPT`。
    *   因为它们必然拿到 `system:role:edit` 与 `system:dept:*`，那两处写接口各加了范围守卫（见 5.3.1）。
*   账号：`username` 取 `IWORK_BOOTSTRAP_ADMIN_USERNAME`（默认 `admin`），密码取 `IWORK_BOOTSTRAP_ADMIN_PASSWORD`。
    *   `hash_password()`（`server/auth/security.py:38`）哈希；用户名/密码校验与 `create_user` 同一套——正则在 `server/auth/service.py:43`，`.strip().lower()` 在 `normalize_username`。
    *   这两个变量补进认证段（`i-work/.env.example`，注意文件在 `i-work/` 下、不在仓库根）。
*   **幂等**：按 `username` 查，已存在就跳过，**不覆盖密码、不覆盖 `status`**——管理员自己改过的密码不能被下次启动抹回去（同 5.5.2 的 `insert-if-missing` 规则）。
*   **「拥有所有权限」靠挂角色，不靠授权行**
    *   只插一行 `sys_user_role` 指向 `admin` 角色，`sys_role_permission` 一行不写。
    *   它的 `role_key` 在 `ALL_PERMS_ROLE_KEYS` 里，由 `load_user_permissions` 短路成全集（见 5.1），以后每加一个权限点它自动就有。
    *   **数据范围是例外**：它没有短路（见 2.2），超管能看到全量靠的是上面那行 `data_scope='ALL'`，忘写就白干。
*   没配 `IWORK_BOOTSTRAP_ADMIN_PASSWORD` 就报错。

*   没有这一步，接口一挂上 `require_permission` 就是全员 403。

**4. 首批挂权限的接口**（按面分类，不逐个列；下表记的是**挂之前**的状态与做法，本轮已按它落地）：

| 面 | 位置 | 怎么处理 |
| :--- | :--- | :--- |
| 管理面 | `skill_routes.py` 的 `/skills/hub` 4 条、`mcp_routes.py` 的 `/mcp/hub` 4 条（各 1 读 3 写）；`routes.py` 的 `/admin/audit` 1 条。共 9 条，**当前全都无鉴权** | 挂新权限点，用 4.2 表 9–16、18 行那批 `system:*`；**两条 `GET /hub` 是例外**——客户端也要读它来渲染 Hub 列表，所以用 `require_permission("system:skill:list", "client:skills:config")`（MCP 同理），任一满足即可，否则普通用户开客户端就 403 |
| 角色配置面 | 新增 `system_routes.py` 的 5 条 `/system/role/*`、`/system/permission/*` | 用 4.2 表 24–28 行那批 `system:*`。**`PUT` 两条写完要清权限缓存**（见 5.1），否则管理员改了授权、对方 15 分钟内还看旧入口 |
| 部门面 | 新增 `dept_routes.py` 的 6 条：`/system/dept/*` 4 条、`/system/user/{user_id}/dept`、`/system/user/{user_id}/roles`，外加 `GET /system/user/list` | 用 4.2 表 29–33、35 行那批 `system:*`（`/user/list` 用第 2 行的 `system:user:list`）。`GET /user/list` 落 §5.3 的 `list_visible` 过滤 —— 只落了「列表」这一类。`/user/{user_id}/roles` 写完要清权限缓存（见 5.1），清的**是那一个用户**：这里知道改的是谁，用 `invalidate(user_id)` 就够，不必 `clear_all` |
| session 面 | `routes.py` 里 **18 个完全没挂鉴权的 handler**：`/{session_id}` 读改删、`/stream`、`/messages`、`/agents`、`/state`、`/effects`、`/queue`、`/queue/{msg_id}`、`/plan/{confirm,edit,answer}`、`/cancel`、`/tool-result/{request_id}`、`/messages/{id}/{regenerate,continue}`、`/replay`；外加 **1 个挂了 `get_current_user`、却没查归属的 `POST /messages`** | 先补归属校验：`session.user_id == 调用者`，即数据权限 `SELF` 档（见 5.3）。这批现在是「知道 `session_id` 就能读写别人的会话」——正是 3.2 落点清单的第一个真实例子 |
| 专家面 | `agent_routes.py` 的 4 条：`GET /experts`、`/experts/{expert_id}/download`、`/teams`、`/teams/{team_id}/download`（router 无 prefix，**不是** `/agents/*`） | 只读，先补 `get_current_user` |

**`/admin/audit` 的落点单独说**（§3.2 落点清单里最容易漏的一条）：它现在把 `user_id` 当**客户端传进来的可选查询参数**，等于「能调就能看谁的」。

*   它是个**跨用户、默认查全量**的查询 —— 正是 §3.2 判据里要收范围的那类（对比：查自己的会话本来就带 `where user_id = 我`，不用落）。
*   落法是**在 handler 里**：`scope != ALL` 时把 `user_id` 覆盖成调用者，`ALL`（只有 admin）才允许按任意 `user_id` 过滤。审计日志没有单独的数据对象，不进 Repo。

**5. 权限变更留痕**

*   复用现成的 `audit_log()`（`server/observability/audit.py`），写进 `audit_logs` 表（`server/db/models.py` 的 `OrmAuditLog`）。
*   `action` 取 `admin.role_permission_changed` / `admin.user_role_changed` / `admin.role_data_scope_changed`，部门面加 `admin.dept_created` / `admin.dept_updated` / `admin.dept_removed` / `admin.user_dept_changed`。
*   与既有的 `user.message_sent`、`user.skill_installed` 命名保持一致。
*   `action` 列是 `String(50)`：上面最长的 `admin.role_data_scope_changed` 是 29 字符，别再往上叠词。
*   `audit_log()` 只 `execute`、**不 commit**：handler 里自己的 `db` session 只承载这条审计，不显式 `await db.commit()` 会在 session 关闭时被回滚掉。

---

## 六、前端实现方案

### 6.1 通用做法

前端 RBAC 的通用骨架：**拿到权限 → 一个判定入口 → 四个落点**。

1.  **权限从哪来**：登录或应用启动时由后端下发 `perms` 数组，存全局状态。**不进 token**——塞进 token 就得等它过期才能生效。
2.  **一个判定入口**：全前端只走一个判定函数（本项目是 `usePermi()`，见 6.3），不许在组件里散写 `permissions.includes(...)`，否则判定口径会分叉。
3.  **四个落点**：
    *   **路由**：两派。
        *   服务端下发菜单树 + 前端 `addRoute` 动态注册（改权限不用发版，代价是后端要知道前端路由/组件路径）。
        *   前端静态路由 + `meta.perms` + 全局前置守卫（前端自治，代价是加页面要发版）。
        *   最简版是**只控按钮、不控路由**。
    *   **菜单**：一般由路由表派生，跟着路由一起过滤，不单独维护一份。
    *   **按钮**：指令 / 包裹组件 / hook，控制**渲染与否**，不是禁用置灰。
    *   **数据**：前端不负责，接口自己裁剪（见 3.2）。
4.  **三个易漏点**：
    *   只过滤了菜单、没做守卫兜底 → 手敲 URL、面包屑、通知里的跳转都能进去。
    *   权限是内存态，刷新即丢 → 必须区分**未就绪（还没拉到）**和**确实没有权限**。
    *   401 与 403 混着处理 → 401 是身份无效（去登录/刷新），403 是身份有效但这条不许（**不能踢回登录页**）。

> 铁律：前端隐藏不是安全边界，真正的拦截在后端 API（见 1.2）。

### 6.2 本项目：四个落点只有两个有对应物

本项目的前端就是 `agent-client`：**Electron 28 + React 18 + zustand + Tailwind**（`13.1-权限控制.md` 已把它定为"真实客户端"）。逐条对上面四个落点：

| 通用落点 | 本项目 | 依据 |
| :--- | :--- | :--- |
| **路由** | **无对应物** | 没有 router；视图切换是 `components/layout/AppLayout.tsx` 的 `useState<ConfigPage \| null>` + 条件渲染 |
| **菜单** | 有，但**只剩一层** | `Sidebar.tsx` 里平铺的入口项，没有二级目录。当前 7 个：6 个真入口 + 1 个无后端的「自动化」占位（见 6.4） |
| **按钮** | 有，**主战场** | 「新建任务」、各配置页里的增删改 |
| **数据** | 不适用 | 不在前端判，见 3.2 |

**「没有路由」不是缺陷**，是本项目的形态决定的：

*   这是桌面应用、单一主视图（聊天），几个配置页是盖在聊天区上的**浮层**。
*   桌面端没有地址栏、没有可分享的链接、没有前进后退、也没有刷新——「页面 = URL」这套 Web 语义不存在，路由两派之争在这里不成立。
*   反过来，也**不存在「手敲 URL 绕过」这类风险**。

所以本项目不需要「路由守卫兜底」，需要的是**一条等价规矩**：

> **凡是能打开配置页、或触发写操作的入口，都要过 `usePermi`。**

今天这条规矩几乎不费事——配置面板的唯一入口就是侧边栏那几个入口按钮（`setConfigPage` 只在 `AppLayout` 里被调用）。风险在**将来新增的入口**：

*   比如聊天空态里加一个「去配置技能」的引导按钮，很容易忘了判权限。
*   新加入口时，把它当 review 检查项。

### 6.3 权限存哪与判定入口

权限集合与 `user` 平级存在 `authStore`（启动或登录后由 `fetchMe()` 写入，见 5.4），**判定写成一个 hook**：

```ts
// stores/authStore.ts
interface AuthState {
  user: AuthUser | null            // AuthUser 就是登录那套字段，不含 permissions
  permissions: string[]            // 新增，来自 GET /auth/me（见 5.4）
  // ...
}

/** 权限判定唯一入口。`perms` 传数组表示**任一满足**，与后端 `require_permission` 同义。 */
export function usePermi(perms: string | string[]): boolean {
  const owned = useAuthStore((s) => s.permissions)
  const wanted = Array.isArray(perms) ? perms : [perms]
  return wanted.some((p) => owned.includes(p))
}
```

*   **为什么是 hook、不是普通函数**：`permissions` 是**启动后异步到达**的（`/auth/me`），而组件渲染时拿到的是当时那一份快照。用普通函数读 `getState()`，`/auth/me` 回来了**界面不会自己刷**——侧边栏会一直空到下次因别的原因重渲染。
    *   写成 hook、内部用 selector 订阅 `permissions`，权限一到就重渲染，调用点不必额外订阅。
    *   这也是 §6.1 那个「未就绪 vs 确实没有权限」易漏点在本项目的落法：两者都是 falsy，界面表现一致（都不渲染），所以**不需要单独的三态字段**——只要保证「到了就会刷」。
*   权限**不要现算**（比如每次渲染去解析 token）：token 里没有权限，且权限会变（见 5.4），必须用服务端下发的集合。
*   服务端**只下发权限点、不下发入口清单**
    *   `/auth/me` 给的是 `permissions`，前端自己持有 `perms → 入口` 的映射（见 6.4），拿 `permissions` 去查这张表。
    *   理由：agent-client 是桌面客户端，装的是用户本地那份代码，本来就要发版升级——「服务端下发入口、不发版就能加入口」这个好处在这里等于零。
*   **超管不用在前端特判**：`admin` 在后端短路成全集（见 5.1），`/auth/me` 拿到的就是全部权限点，`usePermi` 自然全通过。
    *   别写 `role === 'admin'` 这类分支——角色判断一旦散进前端就收不回来了。
*   **登录路径也要拉一次权限**：`fetchMe()` 原本只在 `bootstrap` 里调，`login` 不调——不补的话，首次登录当天侧边栏会是空的，重启才正常。改完顺带在 store 上暴露 `refreshPermissions()` 供 §6.6 用。
*   **`permissions` 初值是 `[]` 不是 `null`**：本轮不做「未就绪」的单独显示（不渲染任何入口本来也就是空侧边栏），少一个状态字段；将来真要在加载态显示骨架屏，再改回三态。

### 6.4 入口控制（替代「动态路由」）

侧边栏入口用一张 `perms → 页面标识` 的表声明（`components/config/pages.ts`），无权限的项**不渲染**：

```ts
// components/config/pages.ts —— 前端入口的唯一声明处
export type ConfigPage = 'skills' | 'mcp' | 'memory' | 'expert' | 'rbac' | 'dept'

export const CONFIG_TITLES: Record<ConfigPage, string> = { skills: 'Skills 配置', /* ... */ rbac: '角色管理', dept: '部门配置' }

export const ENTRY_VIEWS: ReadonlyArray<{ page: ConfigPage; perms: string; section: string }> = [
  { page: 'skills', perms: 'client:skills:config', section: '配置' },
  // ... 每个 client:* 权限点一个键
  { page: 'rbac',   perms: 'client:rbac:config',   section: '系统管理' },
  { page: 'dept',   perms: 'client:dept:config',   section: '系统管理' },
]
```

侧边栏按它渲染：先按 `section` 分组，组内逐条 `hasPermi(permissions, entry.perms)` 过滤，**一组里一条都不剩就整组连标题一起不渲染**（`Sidebar.tsx`）。

*   用 `hasPermi`（纯函数）而不是 `usePermi`：过滤要发生在渲染之前，在 `.filter()` 回调里调 hook 会被 `react-hooks/rules-of-hooks` 拦。
*   `section` 是**纯视觉分组**，不是权限点的 `M` 档目录（见本节末尾那条）。

*   **表里存的是「页面标识」，不是组件**：`page` 是稳定的短名，`perms` 是它要的权限点，两者**分开**。
    *   页面的渲染仍留在 `AppLayout`（`{configPage === 'skills' && <SkillsConfig />}`）。把组件塞进表里看着更省，但那样权限字符串变了就得连组件一起动；现在改 `perms` 只动一行常量。
    *   这张表是**唯一**的入口声明处：`ConfigPage` 类型、`CONFIG_TITLES`、`ENTRY_VIEWS` 都在同一个文件，`Sidebar` 与 `AppLayout` 各自 import——以前两边各写一份 `type ConfigPage`，加页面时只改一边也编译得过、运行时表现为「点了没反应」。
*   **旧客户端不认识新入口 → 该入口不显示，这是可接受的**
    *   「新后端 + 旧客户端」是常态，而渲染只遍历自己的 `ENTRY_VIEWS`，不认识的 perms 进不了遍历集合，既不会崩、也无从告警。
    *   真正要防的是**同一份代码里前端漏配**——漏配同样完全静默（权限授了、入口没出现，连 admin 也看不到），只能靠 CI 反向比对拦（见 4.4）。
*   **新增一个入口 = 改四处，其中后两处编译器会提醒**：
    1.  `catalog.py` 加一行（权限点）。
    2.  `pages.ts` 加一行 + `ConfigPage` 联合类型加一项。
    3.  `Sidebar.tsx` 的 `ENTRY_ICONS` 补图标。
    4.  `AppLayout.tsx` 补 `{configPage === 'x' && <XConfig />}` 那一支。
    *   第 2–3 步是 `Record<ConfigPage, ...>`，联合类型一改就编译不过，忘不了。**第 4 步是会静默漏的那个**：漏了的表现是入口在、点开是空白，没有报错——review 时专门看一眼这一处。
    *   后端那行不是冗余——权限点必须先进清单才能落库、才能经 `sys_role_permission.permission_id` 挂到角色上，否则管理员无从授权，该 perms 永远不会出现在任何人的 `permissions` 里（见 4.4）。
*   **「自动化」那个占位按钮**：它没有任何后端、`onClick` 是空实现，所以**不进 `ENTRY_VIEWS`**，仍在 `Sidebar.tsx` 里当静态项直接渲染，对所有人可见（代码里留了注释说明）。
    *   代价是它不受权限控制——可接受，因为它本来也点不出任何东西。
    *   别给它编一个没后端的 `client:xxx:config`：反向比对会要求它在 `ENTRY_VIEWS` 里有键，正向比对又查不到 API，白白制造噪音。
*   **agent-client 的「菜单」就是这一层入口项** —— 没有二级目录、没有路由表。
    *   §1.4 的**目录/菜单两档是给后台管理平台（带路由的 Web 后台）用的**；在 agent-client 里只落到「入口显不显示」这一档，别照着 §1.4 去找 `Layout` 和路由表。
    *   侧边栏的**分组标题**（`section`，如「配置」「系统管理」）只是这两个中文字符串，写死在 `ENTRY_VIEWS` 里——它不是 `M` 档目录、不进清单、不参与授权，一个组显不显示只由组内入口的权限决定。

### 6.5 按钮级控制

Vue 的 `v-hasPermi` 指令在 React 里没有等价物，写成包裹组件或 hook，**渲染前判断**（而不是像指令那样渲染后删 DOM）：

```tsx
// components/Permi.tsx
export function Permi({ perms, children }: { perms: string | string[]; children: ReactNode }) {
  return usePermi(perms) ? <>{children}</> : null
}
```

```tsx
<Permi perms={['system:user:add']}>
  <button onClick={onAdd}>新增用户</button>
</Permi>
```

*   按钮里写的 perms 是前端**唯一还在手写权限点**的地方，必须能在后端清单里找到。
    *   比对脚本拿这些字符串跟 `catalog.py` 的导出比，**用了清单里没有的 → 构建失败**（见 4.4；闸门本身还没搭）。
    *   拼错一个字符的表现是按钮永远不显示、也不报错，只能卡在构建期。

### 6.6 403 兜底：给 `authedFetch` 补一条分支

`services/authFetch.ts` 的 `authedFetch` 现在只处理 401：

*   `TOKEN_EXPIRED` → 刷新后重放。
*   `TOKEN_INVALID` / `REFRESH_TOKEN_INVALID` → 清登录态。

403 要单独补（位置很关键：**挡在 401 分支之前**，否则 403 会被当成登录态失效）：

```ts
if (res.status === 403) {
  // 登录有效、权限不够，不能当成登录态失效（403 ≠ 401）
  showToast('权限不足')
  return res
}
```

*   **403 不能清登录态**。401 是「身份没了」，403 是「身份没问题、这条不允许」——照 401 那样 `clearSession`，用户点错一个按钮就被踢回登录页。
*   这里只回一句固定的「权限不足」，不再去解 `detail.message`：后端的 403 文案本来就固定是 `权限不足`（见 5.1），解析一遍是白搭。**后端的错误码仍按 `detail.error` 约定给**（见 5.1），将来若要按码分流再解不迟。
*   前端隐藏和 403 兜底是**两层**：隐藏解决绝大多数情况，403 兜底接住「权限刚被回收」和「前端漏判」这两种。真正的边界始终在后端（§1.2）。
*   本地有权限、服务端却回 403 → 多半是本地 `permissions` 过期了（角色刚被改，前端还没重新拉 `/auth/me`）。这种情况**重拉一次 `/auth/me`**，不要清登录态。
    *   前提是拉得动：`fetchMe()` 现在是模块私有的，store 上要先暴露一个 `refreshPermissions()`（见 6.3）。
    *   后端那边也得同步清缓存，不然重拉还是旧值（见 5.1 的写穿）。



---

