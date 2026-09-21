# 9. 多 Agent 协作

## 目录

- [9.1 架构概览](#91-架构概览)
- [9.2 Agent 选择：task 工具](#92-agent-选择task-工具)
- [9.3 串行与并行](#93-串行与并行)
- [9.4 Agent 通信](#94-agent-通信)
- [9.5 Agent 配置](#95-agent-配置)
- [9.6 加载逻辑](#96-加载逻辑)
- [9.7 生命周期与调试](#97-生命周期与调试)
- [9.8 实现路径](#98-实现路径)
- [9.9 已确认问题](#99-已确认问题)
- [9.10 前后端接口定义](#910-前后端接口定义)
- [9.11 任务分解与协调（融合 Codex §4）](#911-任务分解与协调融合-codex-4)
- [9.12 并发一致性（融合 Codex §9）](#912-并发一致性融合-codex-9)

> 状态：**部分已实现**。§9.1–§9.10 的星型拓扑、`task` 工具、子 session、`<final_output>` 提取等已在 `server/engine/` 落地；§9.11 为融合 Codex §4 的增补设计（邮箱通信、异步派发、fork 继承），尚未实现；§9.12 为融合 Codex §9 的并发一致性盘点，绝大部分为现状记录（其待实现清单见 §9.12.9），其中 **§9.12.2 层2 新增「工作区写锁」的跨 agent / 跨 session 设计**，同样尚未实现。

### 9.1 架构概览

采用 **Star Topology（星型拓扑）**——主 agent（lead）是唯一的信息枢纽，子 agent 之间不直接通信。


                    ┌──────────────┐
                    │   主 Agent    │
                    │  (lead)      │
                    └──┬───┬───┬──┘
                       │   │   │
              ┌────────┘   │   └────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ 子 Agent  │ │ 子 Agent  │ │ 子 Agent  │
        │ researcher│ │ generator│ │ auditor  │
        └──────────┘ └──────────┘ └──────────┘
```

- 主 agent 通过 `task` 工具委派任务给子 agent
- 子 agent 在自己的隔离 session 中独立运行完整 agentic loop
- 子 agent 不可再调用 `task` 工具（禁止嵌套）
- 子 agent 之间不通信，主 agent 负责汇总和决策

### 9.2 Agent 选择：task 工具

`task` 是一个独立工具，和其他工具（read、write、bash、grep 等）一同注册在工具列表中发送给 LLM。

采用 **单一 `task` 工具 + `agent_name` 枚举** 的设计（而非每个子 agent 一个独立工具）：

```json
{
  "name": "task",
  "description": "委派任务给专家子 agent。当任务可拆分并行时，在同一轮中同时发出多个 task 调用。",
  "parameters": {
    "agent_name": {
      "type": "string",
      "enum": ["doc-researcher", "doc-generator", "doc-auditor"],
      "description": "要委派的子 agent 名称"
    },
    "prompt": {
      "type": "string",
      "description": "任务描述，应明确期望的输出格式和范围"
    }
  }
}
```

> **设计决策**：单一 `task` 工具而非每 agent 一工具，工具列表保持精简，方便动态增删子 agent。框架收到不存在的 `agent_name` 时返回明确错误（列出可用 agent），让 LLM 自行修正。

### 9.3 串行与并行

**当前阶段先按串行实现**，同一 turn 内多个 `task` 调用按顺序依次执行，等待上一个完成后再启动下一个。

后续阶段再引入并行：同一 turn 内 LLM 返回的多个 `task` 工具调用**框架层并行执行**（`asyncio.gather`）。

主 agent 的 system prompt 中注入指令：

> "当多个子任务之间没有依赖关系时，在同一轮对话中同时发出多个 task 工具调用以并行执行。有依赖关系时串行调用。"

> **注意**：不依赖 LLM 自然并行——指令 + 框架并行能力双管齐下。

### 9.4 Agent 通信

#### 9.4.1 主 → 子

通过 `task` 工具的 `prompt` 参数传递任务描述。系统创建子 session（`parent_id` 指向父 session），子 agent 在自己的隔离 session 中独立运行完整的 agentic loop。

#### 9.4.2 子 → 主

**提取机制**：子 agent 的 system prompt 末尾强制注入输出规范：

> "完成任务后，在最后一条消息中用 `<final_output>` 标签包裹最终成果：
> ```
> <final_output>
> (完整输出内容)
> </final_output>
> ```"

框架提取优先级：
1. 优先查找第一个 `<final_output>...</final_output>` 标签内的内容（`_extract_final_output`）
2. 若缺失，fallback 到全程收集的所有 `agent.text` delta 拼接返回（跨多轮，不含 `agent.thinking`）
3. 若子 agent 执行超时，返回 `子任务执行超时`；若子 agent 报错，返回 `[错误] <message>`

**返回格式**：

成功时：
```xml
<task_result agent="doc-researcher" status="success">
  (子 agent 的最终输出)
</task_result>
```

失败时：
```xml
<task_result agent="doc-researcher" status="error">
  <error>超时/超 token/异常信息</error>
  <partial_output>(如有部分输出)</partial_output>
</task_result>
```

父 agent 收到 error 后可以：
- 重试（相同 prompt 重新委派）
- 基于 partial_output 自己继续
- 调整约束后重新委派

#### 9.4.3 安全边界

子 agent 的硬性限制：
- 不可调用 `task` 工具（禁止嵌套）
- 不能修改 rules 或 memories
- 文件访问限制在父 agent 的 workspace 内

### 9.5 Agent 配置

#### 9.5.1 单个 Agent 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `name` | agent 标识名 | 必填 |
| `description` | 用途描述（给 LLM 选 agent 时参考） | 必填 |
| `system_prompt` | 完整 system prompt（markdown body） | 必填 |
| `max_turn` | 最大对话轮数 | 25 |
| `max_tokens` | 最多消耗 token 数 | 无限制 |
| `timeout_seconds` | 最长运行时间 | 300s |
| `mcp` | 可用的 MCP 服务器列表 | 继承父 agent 的 MCP 白名单 |
| `tools` | 可用内置工具列表 | 全部内置工具 |
| `skills` | 可用 skill 列表 | 继承 |
| `rules` | 可用 rule 列表 | 继承 |
| `model` | 使用的模型 | 继承父 agent |



#### 9.5.3 单个 Agent Markdown 文件格式

```markdown
---
name: doc-researcher
description: 信息检索专家，擅长收集、整理、分析各类技术文档和规范
max_turn: 15
max_tokens: 50000
timeout_seconds: 180
---

(agent 的 system prompt body，即其完整的行为指令和角色定义)
```

> MCP、tools、permission 等配置统一在 `plugin.json` 中管理，不在 Markdown 文件中出现。

#### 9.5.4 多 Agent（专家团）配置

plugin.json：

```json
{
  "name": "doc-team",
  "version": "1.0.0",
  "description": "专业文档生成团队",
  "author": { "name": "Expert Marketplace" },
  "expertType": "team",
  "leadAgent": "doc-team-lead",
  "agents": {
    "doc-team-lead": "agent_uuid_1",
    "doc-researcher": "agent_uuid_2",
    "doc-generator": "agent_uuid_3",
    "doc-auditor": "agent_uuid_4"
  },
  "members": [
    {
      "id": "doc-team-lead",
      "displayName": { "en": "Zhang Chengwen", "zh": "章成文" },
      "profession": { "en": "Editor-in-Chief", "zh": "总编辑" },
      "avatar": "avatars/doc-team-lead.png",
      "role": "lead"
    },
    {
      "id": "doc-researcher",
      "displayName": { "en": "Li Zhiyuan", "zh": "李知远" },
      "profession": { "en": "Research Analyst", "zh": "研究分析师" },
      "avatar": "avatars/doc-researcher.png",
      "role": "member"
    }
  ],
  "avatars": {
    "team": "avatars/team.png"
  },
  "displayName": { "en": "Document Generation Team", "zh": "专业文档生成团队" },
  "displayDescription": { "en": "...", "zh": "..." },
  "skills": ["./skills/browser-use"],
  "mcp": [],
  "permission": {}
}
```

> **设计说明**：`agents` 改为 map（id → agent UUID），通过 UUID 关联 `expert_hub` 表中的 agent 记录。`members` 只保留纯展示信息。

#### 9.5.5 存储结构

配置文件统一存储在数据库中，分为两张核心表：

| 表 | 用途 |
|------|------|
| `expert_hub` | 单个专家，存 agent markdown body + plugin 配置（JSONB） |
| `expert_team_hub` | 专家团，存团队配置（JSONB）+ 成员 agent 列表 |



> 后端通过 DB + 本地插件包混合存储：DB 存元数据和本地路径索引，实际配置内容（system_prompt、skills、avatars）以插件包形式存储在本地文件系统。前端通过 API 查询 hub 列表 + 下载插件包到本地。

#### 9.5.6 市场分发流程

专家/专家团以**插件包（zip）**形式分发。后端管理目录索引（DB），实际配置内容存储在本地文件系统；客户端通过下载接口获取插件包，解压到本地插件目录。

##### Zip 包结构

```
<expert-name>/
├── .iwork-plugin/          # 插件注册标识（必须存在）
├── agents/                 # 角色 prompt 文件
│   ├── doc-team-lead.md
│   └── ...
├── avatars/                # 头像图片
└── skills/                 # 技能文件
    ├── scripts/
    ├── SKILL.md
    └── references/
```

- `.iwork-plugin/` —— 插件注册的关键标识，目录存在即表示该目录是一个合法的 iWork 专家插件
- `agents/` —— 每个角色的 system prompt 文件（Markdown），文件名对应 agent id
- `avatars/` —— 头像图片资源
- `skills/` —— 技能定义，包含 `SKILL.md` 入口、`scripts/` 脚本和 `references/` 参考资料

##### 客户端存储路径

```
~/.iwork/plugins/marketplace/experts/
├── doc-researcher/
├── doc-generator/
├── doc-auditor/
├── doc-team/              # 专家团同样以目录形式存在
└── ...
```


##### DB 存储调整

`expert_hub` 表不再存储 `system_prompt` 全文，改为存储本地路径索引：

```
expert_hub
┌─────────────────────┐
│ id                  │
│ name                │
│ display_name        │
│ description         │
│ plugin_path         │  ← 新增：本地插件目录路径
│ version             │  ← 新增：插件版本号
│ config (JSONB)      │
│   - max_turn        │
│   - max_tokens      │
│   - timeout_seconds │
│   - permissions     │
│ created_at          │
│ updated_at          │
└─────────────────────┘
```

- `plugin_path` —— 服务端本地插件根路径，例如 `/data/iwork/plugins/experts/doc-researcher/`
- `version` —— 插件版本号，前端可据此判断是否需要重新下载
- `system_prompt` 字段移除，改为从 `{plugin_path}/agents/{agent_id}.md` 动态读取

### 9.6 加载逻辑

#### 9.6.1 触发方式

仅**显式调用**才使用专家或专家团。用户点击【使用】按钮时，前端依次执行以下步骤：

1. 前端展示专家 Hub / 专家团 Hub（`GET /experts`、`GET /teams` 获取元数据）
2. 用户选择某个专家或团队，点击【使用】按钮
3. **下载插件包** —— `GET /experts/{id}/download`，获取 zip 并解压到 `~/.iwork/plugins/marketplace/experts/`
   - 若本地已存在同版本插件包，跳过此步（通过 `version` 字段比对）
4. **创建会话** —— `POST /sessions`，传入 `{ agents: [{agent_id: "..."}] }`
5. 后端根据 `agent_id` 查 DB 获取 `plugin_path`，从本地插件文件读取 system prompt + skills 配置，完成封装
6. 后端创建子 session 开始执行推理

#### 9.6.2 单 Agent 加载

1. 前端通过 API 从 `expert_hub` 查询选中 agent 的元数据和 `plugin_path`
2. 子 agent 的 system prompt = 从 `{plugin_path}/agents/{agent_id}.md` 文件读取（完全替换，但叠加系统安全约束）
3. 子 agent 的 tools/MCP/skills/permissions 等配置从 `config` JSONB 字段中读取
4. 未配置的项继承父 agent 当前消息的对应配置
5. **系统安全规则始终保留**（如 deny-rm hook、workspace guard），不被子 agent 配置覆盖

#### 9.6.3 多 Agent（团队）加载

1. 主 agent 的配置加载到当前 session（system prompt、skills、MCP、rules 按主 agent 配置替换）
2. 所有子 agent 声明到 `task` 工具的 `agent_name` 枚举中，并将子 agent 的 name 和 description 以 `<available_agents>` XML 注入 system prompt，与 skills 的 `<available_skills>` 模式一致：

   ```
   <available_agents>
     <agent>
       <name>software-product-manager</name>
       <description>产品经理 - 需求分析与产品规划</description>
     </agent>
     <agent>
       <name>software-architect</name>
       <description>架构师 - 系统架构设计与技术选型</description>
     </agent>
     <agent>
       <name>software-engineer</name>
       <description>工程师 - 代码实现与单元测试</description>
     </agent>
     <agent>
       <name>software-qa-engineer</name>
       <description>测试工程师 - 质量保证与自动化测试</description>
     </agent>
   </available_agents>
   ```

   - `<name>` 取自 `members[].id`
   - `<description>` 取自 `members[].name` + `members[].profession`

3. 主 agent 调用 `task` 工具选择子 agent 时：
   - 创建新 session，`parent_id` 指向父 session
   - 子 session 按子 agent 自身配置加载
   - 子 session 不可使用 `task` 工具
4. 子 agent 完成后，提取结果返回父 agent 上下文

#### 9.6.4 子 Agent System Prompt 组装

子 agent 的完整 system prompt 组装顺序：

```
1. 系统安全约束（不可变，所有 agent 共享）
2. 子 agent 自身 system prompt（从 `{plugin_path}/agents/{agent_id}.md` 文件读取）
3. 当前日期
4. <available_skills> XML（按子 agent 配置过滤）
5. <rules> XML（按子 agent 配置，继承或自定义）
6. <memory-tools-guide>（L1 记忆工具使用指南，静态文本）
```

L1 的原子记忆**不按 agent 继承**：作用域是 `(user_id, agent_path)`，子 agent 有自己的
`agent_path` 和自己的会话历史，因此抽出的记忆天然与父隔离。相关的原子记忆拼在用户
消息前缀（`<relevant-memories>`），不占 system 段——system 每轮字节级不变才能命中
提示词缓存。

### 9.7 生命周期与调试

#### 9.7.1 子 Session 生命周期

- 子 session 完成后保留在数据库，标记 `status=archived`
- 用户可在前端展开查看子 session 的完整对话历史
- 父 session archive 时，级联 archive 所有子 session
- 不自动物理删除，除非用户手动清理

#### 9.7.2 超限处理

| 限制 | 触发时机 | 行为 |
|------|----------|------|
| `max_turn` | 达到最大轮数 | `status=error`，返回 `<error>达到最大轮数</error>` + 已有输出 |
| `max_tokens` | token 累计超限 | `status=error`，返回 `<error>超出 token 预算</error>` + 已有输出 |
| `timeout_seconds` | 运行时间超时 | `status=error`，返回 `<error>超时</error>` + 已有输出 |

#### 9.7.3 调试支持

- 每个子 session 完整保留对话历史
- 前端为每个子 agent 提供独立的问答面板，通过 agent 切换标签按钮在不同 agent 面板间切换，每个 agent 拥有独立的会话面板便于查看
- 日志中关联 `parent_id` 便于追踪调用链

### 9.8 实现路径

| 步骤 | 内容 |
|------|------|
| 1 | `Session` 模型新增 `parent_id: UUID | None` 字段 |
| 2 | 创建 `AgentConfig` 模型和配置加载器（解析 plugin.json + agent markdown） |
| 3 | 实现 `task` 工具定义，支持 `agent_name` 枚举 |
| 4 | `EngineManager` 新增子 session 创建逻辑（`spawn_child_session`） |
| 5 | 子 agent 运行隔离：禁用 `task` 工具、应用 permission 限制 |
| 6 | 结果提取：`<final_output>` 标签解析 + fallback 策略 |
| 7 | 超限控制：`max_turn`、`max_tokens`、`timeout_seconds` 三级监控 |
| 8 | 前端：多 agent 标签切换面板，每个 agent 独立会话窗口 |

### 9.9 已确认问题

1. **子 agent 可以调用 `skill` 工具动态加载 skill**，skill 范围限定在子 agent 配置的 skills 列表内。
2. **子 agent 支持选择更便宜的模型**，通过 `model` 字段独立配置。团队中 lead 用强模型（如 Opus），子 agent 用便宜模型（如 Haiku）降低成本。
3. **不需要支持子 agent 之间间接通信**——star topology 已足够。
4. **子 agent 结果替换 task 工具调用位置放入上下文**，而非追加到消息列表末尾。
5. **子 agent 出错时先返回错误信息**，不返回中间过程。

### 9.10 前后端接口定义

> 接口前缀统一为 `/api`（通过反向代理或 FastAPI prefix 挂载）。以下省略前缀，直接写路径。

#### 9.10.1 接口总览

**新增接口（4 个）：**

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/experts` | 专家列表（元数据，不含 system_prompt） |
| `GET` | `/experts/{id}/download` | 下载专家插件包（zip） |
| `GET` | `/teams/{id}/download` | 下载团队插件包（zip） |
| `GET` | `/teams` | 团队列表（含 members） |

**现有接口改造（4 个）：**

| 方法 | 路径 | 改造点 |
|------|------|--------|
| `POST` | `/sessions` | 请求体加 `agents` 字段 |
| `POST` | `/sessions/{id}/messages` | 流式响应 chunk 加 `agent_id`；新增事件类型 |
| `GET` | `/sessions/{id}/stream` | 同上，stream 重连回放也包含 `agent_id` |
| `DELETE` | `/sessions/{id}` | 加多 Agent 资源清理 |

---

#### 9.10.2 新增接口详细定义

##### GET /experts

获取所有可用专家列表（元数据，不含 system_prompt 全文）。专家数量少（几十个），不分页，全量返回。

system_prompt 全文通过 `GET /experts/{id}/download` 下载插件包后在本地读取 `agents/*.md` 获取。

**Response** `200 OK`:

```json
{
  "experts": [
    {
      "id": "doc-researcher",
      "displayName": "李知远",
      "profession": "研究分析师",
      "icon": "🔍",
      "color": "#3b82f6",
      "desc": "信息检索专家，擅长收集、整理、分析各类技术文档和规范。",
      "tags": ["信息检索", "技术调研", "竞品分析"],
      "version": "1.0.0",
      "config": {
        "max_turn": 15,
        "max_tokens": 50000,
        "timeout_seconds": 180
      }
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，对应 team members 中的 expert id |
| `displayName` | string | 展示名称 |
| `profession` | string | 职业/角色 |
| `icon` | string | 图标（emoji） |
| `color` | string | 主题色（hex） |
| `desc` | string | 一句话描述 |
| `tags` | string[] | 技能标签 |
| `version` | string | 插件版本号（semver），前端据此判断是否需要重新下载 |
| `config.max_turn` | int | 最大对话轮次 |
| `config.max_tokens` | int | Token 预算上限 |
| `config.timeout_seconds` | int | 超时时间（秒） |

---

##### GET /experts/{id}/download

下载专家插件包（zip）。后端从本地文件系统读取插件目录，打包为 zip 流式返回。

**Response** `200 OK`:

- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="{expert-name}-v{version}.zip"`

zip 包内容结构见 [9.5.6 Zip 包结构](#956-市场分发流程)。

**错误响应:**

| 状态码 | 场景 |
|--------|------|
| `404` | expert id 不存在 |
| `500` | 插件目录不存在或文件读取失败 |

---

##### GET /teams/{id}/download

下载团队插件包（zip）。与专家下载接口行为一致，后端从本地文件系统读取团队插件目录，打包为 zip 流式返回。

**Response** `200 OK`:

- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="{team-name}-v{version}.zip"`

zip 包内容结构见 [9.5.6 Zip 包结构](#956-市场分发流程)，团队包与专家包使用相同的目录布局。

**错误响应:**

| 状态码 | 场景 |
|--------|------|
| `404` | team id 不存在 |
| `500` | 插件目录不存在或文件读取失败 |

---

##### GET /teams

获取所有可用团队列表。每条包含成员详情，不分页。

**Response** `200 OK`:

```json
{
  "teams": [
    {
      "id": "doc-team",
      "displayName": "专业文档生成团队",
      "icon": "📋",
      "desc": "专业的文档生成和质量保障团队。从调研到生成再到审核，提供完整的文档生产流水线。",
      "leadDisplayName": "章成文",
      "leadProfession": "总编辑",
      "skills": ["browser-use"],
      "mcp": [],
      "members": [
        { "id": "doc-team-lead", "displayName": "章成文", "profession": "总编辑", "avatar": "章", "role": "lead" },
        { "id": "doc-researcher", "displayName": "李知远", "profession": "研究分析师", "avatar": "李", "role": "member" },
        { "id": "doc-generator", "displayName": "王思源", "profession": "文档生成师", "avatar": "王", "role": "member" },
        { "id": "doc-auditor", "displayName": "赵明诚", "profession": "文档审核员", "avatar": "赵", "role": "member" }
      ]
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 团队唯一标识 |
| `displayName` | string | 团队展示名称 |
| `icon` | string | 图标（emoji） |
| `desc` | string | 团队描述 |
| `leadDisplayName` | string | 领队展示名称 |
| `leadProfession` | string | 领队职业 |
| `skills` | string[] | 团队技能列表（skill id 数组） |
| `mcp` | string[] | 团队 MCP 工具列表（MCP id 数组） |
| `members` | array | 团队成员 |
| `members[].id` | string | 成员 id，对应 expertHub 中的专家 id |
| `members[].displayName` | string | 成员展示名称 |
| `members[].profession` | string | 成员职业 |
| `members[].avatar` | string | 成员头像文字（单字） |
| `members[].role` | string | `"lead"` 或 `"member"` |

#### 9.10.3 现有接口改造

##### POST /sessions（改造）


**Request Body（JSON 结构）：**

```json
{
  "id": "<UUID>",
  "scene_mode": "\"office\" | \"code\"",
  "workspace": "<string>",
  "model": "<string>",
  "mode": "\"ask\" | \"plan\" | \"build\"",
  "client_tools": ["<string>"],
  "agent_id": "<string>",
  "agent_type": "\"expert\" | \"team\"
}
```

**改造后的请求体示例**（多 Agent 会话——专家团）：



**Response** `201 Created`（JSON 结构）：

```json
{
  "id": "<UUID>",
  "title": "<string>",
  "mode": "<string>",
  "scene_mode": "<string>",
  "model": "<string>",
  "workspace": "<string>",
  "client_tools_count": "<int>",
  "agents_count": "<int>",
  "created_at": "<string (ISO 8601)>"
}
```

**错误响应（JSON 结构）：**

```json
// 400 — agent_id 不存在
{ "detail": "agent_id 'xxx' not found in expert_hub or team_hub" }

// 400 — 多个 lead
{ "detail": "only one lead agent is allowed, got N" }

// 400 — team 不能作为 member
{ "detail": "agent_type='team' must have role='lead'" }
```

---

##### POST /sessions/{id}/messages（改造）

**现状**: 入队 → 引擎执行 → NDJSON 流式返回 chunk。chunk 格式：

```json
{"seq":1,"type":"text","delta":"分析中..."}
{"seq":2,"type":"tool_call","tool":"search","command":"..."}
{"seq":3,"type":"message.complete"}
```

**改造** — `MessageCreate` 新增 `agent_id` 可选字段，入队后引擎按 `session.agents` + `agent_id` 分流：


**Request Body（JSON 结构）：**

```json
{
  "content": "<string>",
  "scene_mode": "<string>",
  "workspace": "<string>",
  "model": "<string>",
  "mode": "<string>",
  "agent_id": "<string | null>",
  "agent_type": "<string | null>",
  "skill_invocations": ["<string>"],
  "files": ["<string>"],
  "mcp_servers": ["<string>"]
}
```

**入队后分流逻辑：**

```
用户消息入队
  ├── agent_id 为空 → 单人聊天流程（忽略 agent_type）
  ├── agent_type = "expert" → 单专家流程:
  │       1. 从 DB 查 plugin_path
  │       2. 读取 {plugin_path}/agents/{agent_id}.md → system_prompt
  │       3. 读取 {plugin_path}/skills/ → 挂载 skills
  │       4. 直接执行该 agent
  └── agent_type = "team" → 专家团流程:
          1. 从 DB 查 team 配置，确定 lead agent
          2. Lead Agent 接收消息，拆解为子任务
          3. 分派给 member agents 并行/串行执行
          4. Lead 汇总结果
```

所有 chunk 加 `agent_id` 字段，前端据此路由到对应列。

**改造后的 NDJSON chunk 格式：**

```jsonl
{"seq":1,"agent_id":"doc-team-lead","type":"text","delta":"我来协调团队完成这个任务..."}
{"seq":2,"agent_id":"doc-team-lead","type":"session.publish","to":"doc-researcher","task_type":"research","prompt":"调研微服务架构最新最佳实践"}
{"seq":3,"agent_id":"doc-team-lead","type":"session.publish","to":"doc-generator","task_type":"generate","prompt":"根据调研结果生成技术白皮书初稿"}
{"seq":4,"agent_id":"doc-team-lead","to":"doc-researcher","type":"agent.status","status":"thinking"}
{"seq":5,"agent_id":"doc-researcher","type":"text","delta":"正在检索相关资料..."}
{"seq":6,"agent_id":"doc-team-lead","to":"doc-researcher","type":"agent.status","status":"running"}
{"seq":7,"agent_id":"doc-researcher","type":"tool_call","tool":"web-search","command":"microservices best practices 2025"}
{"seq":8,"agent_id":"doc-researcher","type":"tool_result","result":"找到 12 篇权威资料"}
{"seq":9,"agent_id":"doc-researcher","type":"text","delta":"调研完成。核心要点：1. 服务拆分粒度应以业务边界为准..."}
{"seq":10,"agent_id":"doc-team-lead","to":"doc-researcher","type":"agent.status","status":"done","output_preview":"调研完成。核心要点：1. 服务拆分粒度应以业务边界为准..."}
{"seq":11,"agent_id":"doc-team-lead","to":"doc-generator","type":"agent.status","status":"running"}
{"seq":12,"agent_id":"doc-generator","type":"text","delta":"白皮书初稿生成中..."}
{"seq":13,"agent_id":"doc-generator","type":"text","delta":"## 第一章 概述\n\n微服务架构作为一种成熟的分布式系统设计范式..."}
{"seq":14,"agent_id":"doc-team-lead","to":"doc-generator","type":"agent.status","status":"done","output_preview":"## 第一章 概述\n\n微服务架构..."}
{"seq":15,"agent_id":"doc-team-lead","type":"text","delta":"所有子任务已完成，正在汇总..."}
{"seq":16,"agent_id":"doc-team-lead","type":"message.complete"}
```


**新增事件类型（NDJSON chunk 中的 type 字段）：**

```json
// session.publish — 领队委派子任务
{"type": "session.publish", "agent_id": "<lead>", "to": "<member>", "task_type": "<string>", "prompt": "<string>"}

// agent.status — 子 agent 状态变化，agent_id 为 lead，to 指向子 agent，done 时携带 output_preview
{"type": "agent.status", "agent_id": "<lead>", "to": "<member>", "status": "\"thinking\" | \"running\" | \"done\"", "output_preview": "<string | null>"}
```

**Response** `200 OK`:

- `Content-Type: text/plain; charset=utf-8`（NDJSON 流，每行一个 JSON object）
- `Transfer-Encoding: chunked`

流结束标记：`{"type":"message.complete"}`（由 `agent_id` 对应的 lead agent 发出）。

**错误响应（JSON 结构）：**

```json
// 404
{ "detail": "session not found" }

// 400
{ "detail": "agent_id 'xxx' not in session agents list" }

// 409
{ "detail": "session has a message being processed" }

// 500
{ "detail": "plugin file not found: {plugin_path}/agents/{agent_id}.md" }
```

**兼容性**: `agent_id`、`agent_type` 单人聊天时不传或为 `null`，前端按现有逻辑处理。`MessageCreate` 新增字段均为可选，现有调用方不受影响。

---

##### GET /sessions/{id}/stream（改造）

断线重连时回放错过的流数据。回放 chunk 格式与 `POST /sessions/{id}/messages` 响应完全一致。

**Request（Query Parameters — JSON 结构）：**

```json
{
  "since_seq": "<int>"  
}
```

**Response** `200 OK`:

- `Content-Type: text/plain; charset=utf-8`（NDJSON 流，格式与 messages 响应一致）
- 每个 chunk 包含 `agent_id` 字段，前端按相同逻辑路由

```
GET /sessions/{id}/stream?since_seq=5
```

```jsonl
{"seq":6,"agent_id":"doc-researcher","type":"agent.status","status":"running"}
{"seq":7,"agent_id":"doc-researcher","type":"tool_call","tool":"web-search","command":"..."}
{"seq":8,"agent_id":"doc-researcher","type":"tool_result","result":"..."}
...
```

**错误响应（JSON 结构）：**

```json
// 404
{ "detail": "session not found" }

// 410
{ "detail": "stream buffer cleared, session ended too long ago" }
```

---

##### DELETE /sessions/{id}（改造）

归档会话，同时清理多 Agent 子会话资源。

**Path Parameters（JSON 结构）：**

```json
{
  "id": "<UUID>"
}
```

**Response** `200 OK`（JSON 结构）：

```json
{
  "status": "\"archived\"",
  "id": "<UUID>",
  "sub_sessions_archived": "<int>"
}
```

**改造逻辑** — 加一步多 Agent 资源清理：

```python
@router_sessions.delete("/{session_id}")
async def delete_session(session_id: UUID, engine_mgr=...):
    session = await engine_mgr.session_repo.get(session_id)
    if session is None:
        raise HTTPException(404, ...)

    # 新增：清理多 Agent 子会话资源（如有）
    sub_sessions = await engine_mgr.session_repo.get_sub_sessions(session_id)
    for sub in sub_sessions:
        await engine_mgr.destroy_engine(sub.id)
        await engine_mgr.session_repo.archive(sub.id)

    await engine_mgr.destroy_engine(session_id)
    await engine_mgr.session_repo.archive(session_id)
    return {"status": "archived", "id": str(session_id), "sub_sessions_archived": len(sub_sessions)}
```

**错误响应（JSON 结构）：**

```json
// 404
{ "detail": "session not found" }
```

---

#### 9.10.4 典型交互时序

```
Client                           Server
  │                                │
  │  GET /experts ────────────────→ 返回专家元数据列表
  │  GET /teams ─────────────────→ 返回团队列表（含 members）
  │                                │
  │  用户选择专家/团队               │
  │  GET /experts/{id}/download ──→ 打包插件目录为 zip
  │  ←──────────────────────────── 返回 zip（StreamingResponse）
  │  解压到 ~/.iwork/plugins/      │
  │  marketplace/experts/          │
  │                                │
  │  POST /sessions ─────────────→ 创建多 Agent 会话
  │    { agents: [...] }          │ 初始化成员 agent 引擎
  │  ←─────────────────────────── 201 { id, agents_count: 4 }
  │                                │
  │  POST /sessions/{id}/messages → 发送消息
  │    { content: "生成白皮书" }    │ 入队 → 领队拆解 → 委派成员
  │                                │
  │  ←── NDJSON stream ────────── │
  │    seq=1 agent_id=lead text    │ 领队思考
  │    seq=2 agent_id=lead delegation → doc-researcher
  │    seq=3 agent_id=lead delegation → doc-generator
  │    seq=4 agent_id=lead to=doc-researcher agent.status=thinking
  │    seq=5 agent_id=doc-researcher text "调研中..."
  │    seq=6 agent_id=lead to=doc-researcher agent.status=done
  │    seq=7 agent_id=doc-generator text "生成中..."
  │    seq=8 agent_id=lead to=doc-generator agent.status=done
  │    seq=9 agent_id=lead message.complete
  │                                │
  │  DELETE /sessions/{id} ──────→ 清理所有子会话 + 归档
  │  ←─────────────────────────── 200
```

---

#### 9.10.5 后续扩展预留

| 扩展方向 | 预留字段 | 说明 |
|---------|---------|------|
| 团队管理 CRUD | — | 初期通过 seed/admin 管理专家/团队，后续可加 `POST/PUT/DELETE /teams` |
| Agent 独立 skill/MCP | `AgentConfig.skills`, `AgentConfig.mcp` | 每个 agent 可以有不同的工具集 |
| 并行策略控制 | 请求体 `parallel: bool` | 串行/并行执行可选 |
| 子任务 DAG | `delegation.depends_on: [agent_id]` | 支持有依赖的子任务编排 |
| Agent 间通信 | `type=agent.message` | 成员间直发消息（当前禁止，预留） |

---

### 9.11 任务分解与协调（融合 Codex §4）

> 本章融合 OpenAI Codex（codex-rs）多 Agent 实现第 4 节「任务分解与协调」的思路，按 iWork 的产品形态做了裁剪。
>
> **与 §9.1–§9.10 的差异**：本章新增「邮箱通信」与「异步派发 + 自动挂起等待」两条机制，并修订 §9.3（串行 → 并行）、§9.4（通信通道）、§9.7.1（子 session 生命周期）、§9.9（已确认问题 4）。凡修订处均在正文内以小字标注。

#### 9.11.1 定位与取舍：固定两层星型

iWork 沿用 §9.1 的**星型拓扑**，并**明确拒绝** Codex 的分散式树形：

| 维度 | Codex（分散式树） | iWork（固定星型） |
|---|---|---|
| 拓扑 | 任意深度线程树，谁都能 spawn | 两层：lead → members，**成员不可再派生** |
| 调度 | 无集中调度器，各 agent 自发分解 | **lead 是唯一协调者**（硬约束） |
| 寻址 | `AgentPath` 树路径（`/root/explorer_1`） | **同构** `AgentPath`（`/root/{agent_id}`）——格式一致，但**当前只生成 2 段**（§9.11.3） |
| 约束 | 软约束（提示词引导树形，兄弟技术上仍可直连） | **硬约束**（成员 `_disable_task_tool=True`；兄弟直连被邮箱门拒绝） |

**地址空间不随拓扑锁死**：本轮即采用 `AgentPath` 寻址（§9.11.3），路径格式可容纳任意深度；当前只是**不生成**更深层级。将来若放开"成员可再派生"，**寻址层零改动**——只需放开派生门，不必二次改造地址。

#### 9.11.2 lead 的角色：唯一的分解与分派点

⚠️ **先纠正一个容易误用的说法**：Codex 反复强调 root"不是中央调度器"，那是它的**自我描述**——它靠"没有代码级调度组件 + 每层 agent 都能 spawn（分解分布式）+ 软约束"来避免中心化。**这套说法搬到 iWork 不成立**。

iWork 中成员被 `_disable_task_tool=True` 硬锁，**只有 lead 能拆解与分派**（§9.11.1 对照表）——所以 lead **就是逻辑上的中央调度点**，而且比 Codex 更集中：Codex 每层都能拆，iWork 只有 lead 能拆。

借用 Codex 的只是 root 的**职责描述**，不是它的"非中心化"主张：

- **发起**：把用户任务拆成子任务、决定派给谁、派什么活
- **汇总**：收各成员的产出，拼起来
- **定大方向**：根据结果决定下一步（继续派 / 追加 / 收尾）
- **不管过程**：成员内部怎么执行，lead 不微观管理

> **"中心"到底指什么**：不是**代码级调度组件**（iWork 与 Codex 都没有——分配由 lead 这个 LLM 靠 prompt 指导、用 `task` 工具完成，不是代码里的规则策略），而是**分解与分派的逻辑中心**。在这个意义上，iWork 的 lead 是中心，且是唯一中心。

**拆解策略**写进 lead 的 system prompt（显式指导，不依赖 LLM 自发）：

> "收到任务后，先判断能否拆成相互独立的子任务。能并行时，在同一轮中为每个子任务发出一个 `task` 调用（`mode=spawn`），由框架并行执行；有依赖关系时，串行调用，后一个基于前一个的结果。最后汇总所有成员结果。"

#### 9.11.3 寻址：AgentPath（路径预留，当前锁定两层）

**采用 `AgentPath` 路径寻址**——尽管当前只有两层。理由：唯一真正需要路径的场景，就是将来放开"成员可再派生"——那时同一个 `agent_id` 会出现在不同层级，扁平地址失去唯一性。**现在把地址做成路径，是为了将来放开拓扑时寻址层零改动**，不是因为今天需要。

**路径格式**：

| 层级 | 路径 | 说明 |
|---|---|---|
| 根 | `/root` | **团队会话自身**——iWork 的 lead 就跑在团队会话里（`_setup_team_lead()`，`query_loop.py:736`），不是独立 session |
| 成员 | `/root/{agent_id}` | 例：`/root/doc-researcher` |
| 同名冲突 | `{agent_id}_{n}` | 同父下同名加序号（对齐 Codex `/root/explorer_1` 风格）。**当前不会发生**，见下 |

- **当前只生成 2 段**；地址空间本身不设深度上限
- 当前不产生同名冲突：§9.11.6 已定「followup 复用同一 child session、不每次新建」，同一成员的多次派发走复用而非新建。`_{n}` 是留给将来星→树的兜底规则
- 单人聊天 / 单专家会话不派生任何子 session，`agent_path` 恒为 `/root`

**⚠️ 唯一性作用域（易错点）**：`agent_path` **不能全局唯一**——两个互不相关的顶层会话，路径都是 `/root`，全局 UNIQUE 会直接冲突。

> Codex 靠「每个根会话创建一个 `AgentControl` 注册表」天然限定作用域（`9-多agent流程(未实现-新).md:47`）。iWork 对应做法：**唯一性限定在同一根会话内**——`sessions` 表加 `agent_path` + `root_session_id`，唯一约束 `(root_session_id, agent_path)`。

**LLM 侧代价几乎为零**（最容易被高估的一点）：`task` 的 `enum` **保留不变**，只是枚举的**值**从成员 id 换成成员路径——建工具时按 `f"/root/{m['id']}"` 算好（`_build_task_tool_definition()`，`query_loop.py:860-863` 只改这一处）。LLM 仍然只是"从 `<available_agents>` 名单里挑一个"，**不需要自己拼路径、也不需要知道自己的深度**。这点必须写清，否则后人会误以为已支持任意寻址。

**与 `parent_id` 的关系**：路径是 `Session.parent_id`（`server/models/session.py:54`）父链的**显式化表示**——顺着父链本就能推出路径，`agent_path` 列只是把它物化，让"路径 → session"变成一次直接查库，不必逐级上行。父链下行已有现成能力（`session_repo.list_by_parent()`，`server/storage/postgres.py:86`），路径物化后这一层也就不必再走。

#### 9.11.4 工具面：单一 task 工具 + mode

沿用 §9.2「单一 `task` 工具 + 枚举」的设计，扩展两个参数。**枚举保留，但枚举值改为成员路径**（§9.11.3）：

> **修订 §9.2**：原 `agent_name` 参数 → 改名 `agent_path`，枚举值从成员 `id` 改为成员路径（`/root/{agent_id}`）。仅此一项变化——`task` 仍是**单一工具**、仍是**枚举式选取**，没变成"可寻址任意节点"。

```json
{
  "name": "task",
  "input_schema": {
    "type": "object",
    "properties": {
      "agent_path": { "type": "string", "enum": ["/root/doc-researcher", "..."], "description": "要委派的子 agent 路径，从枚举中选取" },
      "prompt": { "type": "string", "description": "任务描述" },
      "mode": { "type": "string", "enum": ["spawn", "followup"], "description": "spawn=首次派生；followup=复用已有子 agent 追加任务" },
      "fork_turns": { "type": "string", "description": "可选，覆盖该 agent 配置的上下文继承档位：none | all | <N>" }
    },
    "required": ["agent_path", "prompt", "mode"]
  }
}
```

- `mode="spawn"`：新建 child session（`parent_id` 指向父），并写入 `agent_path = 父路径 + "/" + agent_id`（例 `/root/doc-researcher`）——**由框架计算，LLM 不拼路径**（§9.11.3）
- `mode="followup"`：复用该成员已存在的 child session，追加消息并唤醒新回合
- **不引入** `spawn_agent` / `send_message` / `wait_agent` / `interrupt_agent` / `list_agents` 多工具——保持工具面精简

> **返回值语义变了**（详见 §9.11.8）：`task` **不返回最终结果**，而是立即返回**句柄**：
>
> ```json
> { "status": "dispatched", "task_id": "<uuid>", "agent_path": "/root/doc-researcher" }
> ```

#### 9.11.5 上下文继承：fork + 清洗

现状：子 agent 只拿到 `prompt` 字符串（`task_handler.py:67` 构造 `MessageCreate(content=prompt)`），完全没有父的对话背景。引入 **fork** 让子可选择性地继承父历史。

**fork_turns 三档**：

| 值 | 含义 |
|---|---|
| `none` | 全新上下文，只带 `prompt`（= 现状） |
| `all` | 继承父的完整历史（清洗后） |
| `N`（正整数） | 只继承父**最近 N 轮**（按历史行的 `turn` 标记，`context.py:124` `_stamp()`） |

**配置位置**：per-agent（写在 agent `.md` frontmatter），**全局 fallback 默认 `3`**。调研型 agent 可配 `all`，工具型 agent 可配 `none`。

**清洗规则**（继承前先过滤，不原样搬运）：

- **留**：system / user 消息 + 每轮的最终答案
- **丢**：工具调用细节（read/write/bash 的输入输出）
- **换**：父的 developer 指令 → **子自己的**（子有自己的 role 与 system prompt）

**时机**：**仅 `mode=spawn` 时 fork**；`mode=followup` 时子沿用自身已有历史，不重新 fork。

> 别和 `max_turn` 混淆：`fork_turns` = 子**继承父**多少轮；`max_turn` = 子**自己**最多跑多少轮（§9.5.1，默认 25）。

#### 9.11.6 生命周期与复用

- `spawn` → 新建 child session（`parent_id` 指向父）
- `followup` → 复用同一 child session，追加消息、唤醒新回合；**不再每次新建**
- 子 session 跑完**保留 active**（不自动归档）以便复用；**父归档时级联归档所有子**
- 前端：同一 agent 标签页跨多次 followup **累积**（chunk 已带 `agent_id`，路由不变）
- **待实现**：子 session 需显式记录 `agent_path`（顺带解决 §9.11.3 的唯一性缺口）——现状只有 `title="[子任务] {agent_name}"`（`task_handler.py:49`），靠标题匹配脆弱。物化后"路径 → 子 session"是一次直接查库

> **修订 §9.7.1**：原"子 session 完成后标记 `archived`"改为"保留 `active` 以便复用，父归档时级联归档"。

#### 9.11.7 通信：Session Mailbox（父子限定）

**模型**：每个 session 一个**私有邮箱**——**只有收件人从自己的邮箱取信**，发件人只往**对方**的邮箱投递。这就是"邮箱"的本质。

**信封** `MailMessage`：

```python
class MailMessage(BaseModel):
    id: UUID
    sender: str          # agent_path（或 "user"）
    recipient: str       # agent_path
    type: str            # "task" | "followup" | "result" | "status"
    payload: dict        # 正文 / 结果 / 状态内容
    cid: UUID | None     # 关联 id：把 result 对回它那条 task
    trigger_turn: bool   # 投递后要不要唤醒对方开新回合
```

| 类型 | 方向 | trigger_turn | 用途 |
|---|---|---|---|
| `task` | 父→子 | true | 首次派生 + 初始任务 |
| `followup` | 父→子 | true | 追加任务、唤醒新回合 |
| `result` | 子→父 | false | 子回合终态的最终答案 |
| `status` | 子→父 | false | 中途状态（预留；本轮不消费） |

**路由 + 门**（`MailRouter.send`）：

1. **过门**：允许当且仅当 recipient 是**直接子**或**直接父**——判定用**路径段数 + 前缀**，不用 session-id 比较：
   - **直接子** ⟺ `recipient.agent_path` 去掉最后一段 == `sender.agent_path`（恰好多一段且前缀匹配）
   - **直接父** ⟺ `sender.agent_path` 去掉最后一段 == `recipient.agent_path`
   - **兄弟（同父但互不为父子）拒绝**
2. **寻址**：`recipient.agent_path` → 定位 session（按 `(root_session_id, agent_path)` 直接查库，§9.11.3）
3. **投递**：写入收件人邮箱；`trigger_turn=true` 时置 `_wake_event` 唤醒其 `run()`

> 这条门把"父-子互发、不能子-子"从**软约束变硬约束**，写死在 router 一处。
>
> **为什么用路径段数判定、而不是 session-id 比较**：两者今天完全等价（只有两层），但路径版**自动支持多层**——将来放开"成员可再派生"，门无需重写。若用 id 比较，届时还得再改一遍门，那就白预留路径了（§9.11.3）。

**存储**：复用 DB 消息表——`messages` 增加 `sender_agent_id / recipient_agent_id / msg_type / cid` 字段；**邮箱 = 该 session 按 `recipient` 过滤、`status=PENDING` 的消息**。引擎摄入本就 DB 支撑（`enqueue()` 写库 + `_wake_event`，`query_loop.py:1153`；`run()` 的 `_dequeue_next()` 从库取，`query_loop.py:954`），复用后**持久化 + 重连回放免费**。

**语义 vs 展示分离**（解决现有纠缠）：

- **语义**：子 → 父 走邮箱（`result` 信封）
- **展示**：子的 chunk（text/thinking/status/tool）仍中继到父的 `_chunk_queue`（前端标签页），**不属于邮箱**

> 现状是父读子的 `_chunk_queue`（`task_handler.py:112`）**一鱼两吃**——既抽结果又中继前端。上邮箱后拆开，父**不再持有子引擎对象**做通信，只持有地址（`agent_id`）。

**明确不是**中立邮箱广播：有 `AgentPath` 寻址（§9.11.3），但**没有通用广播 / 注册表列举**——不能 `list_agents`、不能群发，收件人必须落在**直系**上；当前只有 lead ↔ member 一条通信轴。

#### 9.11.8 派发语义：异步（非阻塞）+ 自动挂起等待

这是本章与现状 §9.3（串行阻塞）最大的不同。

**1. task 立即返回句柄 → 父继续本轮**

`task` 返回 `{"status":"dispatched","task_id":X}`，父**同一条消息内继续下一个 LLM 循环**（`_run_message_loop` 的 `while` 继续，`query_loop.py:1397` / `:1426`）。父可以：

- 继续派更多子任务（**一轮并行 fan-out**）
- 做本地工作
- 直接进入收尾

**2. 收尾时挂起等待**

父本轮将正常结束（LLM 无 tool_use）时，框架检查 `in_flight_tasks`：

- **非空** → **不写 `message.complete`、不置 IDLE**，状态置 `WAITING_CHILDREN`，挂起等待
- **空** → 正常结束

**3. 结果回收 → 继续同一循环**

子终态时 `router.send(result, to=父, cid=X)` → 注入结果 → 继续**同一条**消息循环（turn 继续累加，不新开消息）。

**收集策略**：**批量**——等在途子任务**全部**回收（或超时）后一次性注入，减少 LLM 往返。

**时间线**：

```
t0  父消息 M1 开始
t1  M1.turn1: LLM 发出 task(researcher, mode=spawn) + task(generator, mode=spawn)
t2  └ 两个都立即返回 {"dispatched", task_id=X/Y}      ← 并行，不阻塞
t3  M1.turn2: LLM 继续（输出"已派发两条线…"）
t4  M1 要收尾 → in_flight={X,Y} 非空 → 挂起（WAITING_CHILDREN）
t5  …两子在后台并行跑…
t6  X、Y 都回来 → 批量注入 → M1.turn3 继续
t7  LLM 汇总 → in_flight 空 → M1 正常结束
```

**in_flight_tasks**：每父 session 维护 `{task_id → {agent_path, status, dispatched_at}}`；可注入"在途清单"到上下文让 lead 感知。

**超时 / 失败兜底**：子超时或报错 → 同样作为 `result`（`status=error`）注入并移出 in_flight，**避免父永久挂起**；父消息另有整体超时兜底。

**为何选它**：对 LLM 表现为"同步"（不出现 IDLE 空档），框架层面却是并行——等价于 §9.3 预留的"同轮多 task 并行 + 同轮收集"。

> **修订 §9.3**：原"先按串行实现，后续引入并行执行（`asyncio.gather`）"→ 本章直接采用"异步派发 + 自动挂起等待"的并行方案。

#### 9.11.9 结果提取（子 → 主）

- **子 prompt 契约**：要求"**最后一次回复必须包含本次任务的交付物；不要在交付之后再补充收尾内容**"——**位置合同，取代 §9.4.2 的 `<final_output>` 标签方案**（注入点 `query_loop.py:851` `_setup_expert_agent()`，live 代码唯一注入点）
- **提取**：取**子回合终态的最后一条 assistant 消息**（取代现状"整回合 `agent.text` 全量累加"，`task_handler.py:127-131`）——实现上 buffer 在每次 `client.tool_request` 时清空、`message.complete` 时取值；`_extract_final_output()`（`task_handler.py:142-148`）随之删除
- **空末条兜底**：子以工具调用收尾、无收尾文本时 → warn + 回退到本回合最后一段非空文本；确无文本则返回明确标记（如 `子任务无文本产出`），不返回空串
- **注入形式变了**：以 `role=user` 的 agent 消息片段注入父上下文，**非 tool_result 槽位**——因为槽位已被 dispatch 句柄占用（§9.11.8）；且**必须**带结构化前缀（如 `[子 agent /root/doc-researcher 的产出 · task_id=<cid>]`），否则与用户输入不可区分。父侧标识由信封承担（§9.11.7），删 `task_handler.py:92` 的 `<final_output>` 回包裹，`query_loop.py:2089` 预览直接取 payload

**取舍（位置合同丢掉了什么）**：零解析，无需处理标签未闭合 / 标签名写错；但**丢失机械可检测性**——末条总是存在，模型不守约时框架无从判断它是不是交付物（标签方案下"找不到标签"是可观测事件）。这是**无声降级**；如需可观测，可加启发式 warn（末条长度显著小于本回合累计输出 → 报警），**本轮不加**。

> 严格语义：末条承载的是「**模型选定的回传内容**」，**不是**完整性保证——交付物若由 `write_file` 等工具写出，末条里是**摘要而非全文**（此点在标签方案下同样成立）。

> **修订 §9.9 已确认问题 4**：原"子 agent 结果替换 `task` 工具调用位置放入上下文"→ 改为"以 agent 消息片段（`role=user`）注入"。

> **修订 §9.4.2**：§9.4.2 描述的"`<final_output>` 标签 + 全程 `agent.text` 拼接兜底"被本节**取代**；旧节正文不动（记录的历史实现）。

#### 9.11.10 门控

只有**显式模式**：用户选专家/专家团插件 → 建会话时填充 `session.agents`（§9.6.1）。Codex 的 `ExplicitRequestOnly`（默认）/ `Proactive`（自发拆任务）/ `Custom` 映射到 iWork 即"只有显式模式"，**`Proactive` / `Custom` 列为预留**。

#### 9.11.11 权限原则

采纳 Codex："**角色只降能力、不超父权限**"。子 agent 的 tools / MCP / skills / permissions 只能收敛于父，不能扩张（与 §9.6.2 第 5 条"系统安全规则始终保留"一致）。

#### 9.11.12 实现路径（待实现清单）

| # | 内容 | 落点 |
|---|---|---|
| 1 | `agent_path` + `root_session_id` 进 `Session`，唯一约束 `(root_session_id, agent_path)`（§9.11.3） | `server/models/session.py:42` + Alembic 迁移 |
| 2 | `MailMessage` 信封模型（`sender` / `recipient` 用 `agent_path`） | 新增（或并进 `server/models/message.py`） |
| 3 | `messages` 表加 `sender_agent_id / recipient_agent_id / msg_type / cid` | `server/db/models.py` + Alembic 迁移 |
| 4 | `MailRouter`（**路径解析** + **路径段数门** + 投递 + 唤醒） | 挂 `EngineManager`（`query_loop.py:203`） |
| 5 | `task` 定义：`agent_name` → `agent_path`（**枚举保留、值改路径**）、加 `mode` / `fork_turns`、返回改句柄 | `query_loop.py:855-888` / `:2054` |
| 6 | 父→子 history fork + 清洗 | `task_handler.py` 新增 |
| 7 | 子 session 写入 `agent_path`（替代标题匹配）+ 父→子映射 | `task_handler.py:45-58`、`server/models/session.py:42` |
| 8 | 子终态自动 `router.send(result, to=父, cid=X)` | `task_handler.py`（替代 `:112` 读子队列） |
| 9 | 父侧 `wait_for(cid)` / 邮箱等待表 | `QueryLoopEngine` 新增 |
| 10 | `in_flight_tasks` + 挂起 / 恢复 | `query_loop.py:1397` `_run_message_loop` 收尾插桩 |
| 11 | `_dequeue_next` 分流（用户消息 vs agent 信封） | `query_loop.py:954` |
| 12 | 级联归档 | 扩展 `EngineManager`（`cancel_tree` `query_loop.py:292`） |
| 13 | `fork_turns` 进 `AgentConfig` + `.md` frontmatter 解析 | `server/models/session.py:28` / `server/plugins/loader.py` |

> 第 1、5、7 条是本次「地址改上 `AgentPath`」相对原扁平设计**新增/变更**的三处；其余条目不受寻址格式影响。

#### 9.11.13 明确不做

- ~~不做 `AgentPath` / 树寻址~~ → **已改做**：地址格式采用 `AgentPath`（§9.11.3）。但仍**不做基于它的树遍历 / 任意节点寻址**——`task` 只认直系成员的枚举，没有"随便指一个深层节点"的能力
- **不做动态 spawn（任意深度）**——成员不可再派生。当前硬约束不变（`_disable_task_tool=True`）；**地址空间已按路径预留，将来放开拓扑时寻址层零改动**（§9.11.1、§9.11.3）
- 不引入 `spawn_agent` / `send_message` / `wait_agent` / `interrupt_agent` / `list_agents` 多工具
- 不做 `Proactive` 自发模式
- 不做子子通信（邮箱门已硬拦，预留放开点）
- 本轮不做中途 `send_message` 汇报（`status` 类型已预留）
- **展示层不进路径**：chunk 的 `agent_id` 保持扁平成员 id，前端路由契约（§9.10）不变——寻址层用路径、展示层用扁平，两者分离

---

### 9.12 并发一致性（融合 Codex §9）

> 本章融合 OpenAI Codex（codex-rs）多 Agent 实现第 9 节「并发一致性：多 agent 如何解决状态一致性与冲突」的思路，落到 iWork 真实的并发面上。
>
> **与 §9.11 的关系**：§9.11 定"该怎么协作"（谁拆任务、怎么派、结果怎么回）；本节定"协作并发跑起来时，状态怎么不打架"——是 §9.11 的地基。§9.11.8 的异步派发一旦落地，本节列出的缺口会从"潜在"变"必现"。
>
> **主轴是 iWork 自己的问题域**，不是 Codex 的逐节复述：每小节先写「现状（代码锚点）→ 靠什么保证 → 缺口在哪」，Codex 只作对照列。因为 iWork 的并发面**远小于** Codex——只有两层星型、成员不可再派生、服务端根本不写用户文件、单进程部署——所以 Codex 的大部分保证在这里是**靠架构天然豁免**的，真缺口只有少数几个。本节的产出重心是 §9.12.9 的待实现清单。

#### 9.12.1 定位：为什么 iWork 的并发面比 Codex 小

Codex 的并发一致性要处理"N 个线程同进程、共享容器文件系统、任意深度线程树"；iWork 只有"两层、单进程、不碰文件"。这不是实现水平的差异，而是**架构前提**的差异——三条豁免：

| 豁免 | 依据 | 消掉了 Codex 的什么 |
|---|---|---|
| **只有两层，成员不可再派生** | §9.11.1 硬约束：成员 `_disable_task_tool=True`（`query_loop.py:857`），`task` 的 `enum` 只含本团成员（`query_loop.py:860-863`）| 同级兄弟之间的路径竞争 |
| **子 agent 当前不同时跑** | `task` 内联阻塞：`_dispatch_tool` 直接 `await self._task_handler.execute(...)`（`query_loop.py:2071`）| Codex"执行并发上限"要管的问题在 iWork 暂不存在——并发度天然 ≤ 1 |
| **服务端没有文件写路径** | `write_file` / `edit_file` 是**客户端**工具，服务端 `server/tools/` 只做读 / 写分类与权限判定 | Codex 第 9 节篇幅最大的"文件层不做冲突检测"整块 |

结论先给：**Codex 那套"分层锁 + 原子计数 + LRU 驱逐"在 iWork 大部分没有用武之地**。但这不是"iWork 更简单所以不用管"，而是"它的安全性依赖几个前提，前提一旦松动就要现补"——前提清单就是 §9.12.4 与 §9.12.7 的内容。

#### 9.12.2 文件系统层：结论相同，但理由完全不同

Codex 的结论是"文件层**不做**冲突检测"——共享文件系统、无文件锁，靠 worker 角色的 **ownership 提示词**（每份文件归谁管、不许回滚他人修改）+ `apply_patch` 的 **old-lines 乐观校验**兜底。它的乐观校验差在原子性：读文件拿基线 → 校验 old lines 是否存在 → 算新内容 → 整文件覆盖写回，**校验与写回之间没有"提交时再校验"**，所以"两个 worker 同时基于旧版覆盖"的静默丢失（其文档里的"时序 3"）是必然可能的。

iWork 在这一层的结论一样（不做冲突检测），但**理由不同**：不是"共享了但不设防"，而是**服务端压根没有文件写路径**——`write_file` / `edit_file` 由客户端执行，服务端只在调度与权限层把它们判为"写"（`server/tools/scheduling.py`、`server/scheduling.yaml`、`server/tools/permission.py`）。因此 Codex 的"时序 3 静默覆盖"在 iWork **落在客户端与客户端工具实现上**，不在服务端的并发模型之内；服务端能做的是"判读 / 写"和"给不给权限"，不是"合并两份内容"。

iWork 自己的保护**分三层**：

**层1 · 单 agent 单 turn 内 —— 已落地。**
分段调度（`server/tools/scheduling.py` + `_run_segments`，`query_loop.py:2365-2392`）：本轮调用按模型发出顺序扫成连续段，**读段并行**（`asyncio.gather`，`:2403`）、**写段串行**、**段间严格保序**。
→ **子 agent 白拿这一层**：子 agent 跑的就是同一个 `QueryLoopEngine`，`_run_segments` 天然生效，**不需要多 agent 专项支持**。

**层2 · 跨 agent / 跨 session —— 未实现（本节新增设计）。**
方案是**工作区写锁**，五个要点：

1. **并发域 = 工作区。** 不是 **session**——团队会话的 lead 与其子 agent 同属一个工作区、会碰同一批文件，按 session 分桶会把父子拆到两个桶里（等于没锁）；也不是**进程**——客户端开的两个不同项目的对话会被无谓串起来（白等）。**工作区**正好等于"可能碰同一批文件"的最大集合，父子继承工作区（§9.4.3）后天然同桶，不同项目天然不同桶。
2. **只排写、不排读。** 写写冲突丢数据（不可逆），读写冲突只是读到脏数据（下一轮重读即可，可恢复）；层1 已保住同一 agent 内"写完自己读"的一致性，敞开的只有**跨 agent** 那一段。给读加锁的两条路都不通：全局读写锁把并行度赔光（且 asyncio 无现成读写锁）；按路径精确锁挡不住 `grep` / 无路径 `glob` / `bash cat` 这类探索性读——它们解析不出路径域（`SchedulingPolicy.path_of()` 只认 `write_file` 类工具的 `path` 参数、不解析命令，`server/tools/scheduling.py:101-109`）。代价是**读到半截**（与 `14-单回合工具调用并发.md` §3.7.4 一致，本节接受）。
3. **`task` 按调用排除锁（否则父子死锁）。** `task` 被判为"写"（`is_read_only()` 的 `read_tools` 名单里没有它，`scheduling.py:68-73`），且当前是**同步阻塞**——`_dispatch_tool` 的 task 分支（`query_loop.py:2055`）一路等子 agent 跑完（`task_handler.py:95-139`）。若 `task` 也持锁：父持锁等子 → 子要写 → 拿不到同一把锁（父子必然同工作区）→ **死锁**。故锁必须**按调用获取**，不能包整个写段循环。
4. **持有范围**：从下发 `client.tool_request`（`query_loop.py:2158`）到结果收口（`_execute_tool` → `_finalize_tool`）。用 `async with` 保证超时 / 断连 / 异常都会释放，不会永久占锁。
5. **代价**：同一工作区内所有写排队。真正被拖的是 `bash` 写（`cargo build` / `pytest`）——它会独占整个工作区数分钟；`write_file` / `edit_file` 是客户端本地毫秒级操作，串行几乎无感（且单 agent 写段本来就串行）。→ **多 agent 的写并行度上限由 `bash` 写决定，不由 `write_file` 决定。**

> **与 `14-单回合工具调用并发.md` §3 的关系（未定，本轮不做）**：ch14 §3 设计了更细的档位——路径域 + X / S 模式 + 意向锁 IX / IS（+ 四条纪律 + 5 条挡不住的边界）。本节的"工作区写锁"是更粗的档位。二者是"退化特例"（同一把锁、只是把写一律视为根）还是两套机制，**尚未定论**；本轮只落粗档。本节承接 ch14 §3 的两条结论：① 文件层永远不做 merge；② 语义冲突要靠**文件所有权**（提示词），锁只能防撞车。

> **本节明确不做**：文件层 merge / 版本号；跨进程（锁是进程本地，依赖 §9.12.7 的单进程前提）；读的并发上限（见 §9.12.9 第 4 条）。

#### 9.12.3 内存共享状态：对照 Codex 的 5 个共享对象，iWork 落了哪些、缺了哪些

Codex 为 5 个共享对象各配一种原语；下表逐条对照 iWork 的落点与缺口——**两个换机制、两个没做、一个只做了一半**：

| Codex 对象 | Codex 原语 | iWork 现状 | 落点 | 缺口 |
|---|---|---|---|---|
| 线程表 | `RwLock` | ✅ 换成无锁 dict | `query_loop.py:226` | — |
| spawn 总数 | `AtomicUsize`+CAS | ❌ **没做** | — | 无"最多起 N 个子 agent"的闸 |
| 执行中数 | `AtomicUsize`+RAII | ❌ **没做** | — | 无并发执行上限（§9.12.9 第 4 条）|
| `active_turn` | `tokio::Mutex` check-and-set | ✅ 换成"一 session 一引擎"的结构 | §9.12.4 | — |
| `agent_status` | `watch` 通道 | ⚠️ 只有事件推送，不是 `watch` | `query_loop.py:2096-2102` / `task_handler.py:103-108` / `models/events.py:231` | 见下第 3 条 |

**两条 ✅ 是"换机制"，不是"省锁"**：线程表退化成无锁 dict（同一事件循环访问，`RwLock` 无必要）；`active_turn` 用"一 session 一引擎、一条 `run()` 串行"的结构替代（§9.12.4）。保证等价，手段不同。

**两条 ❌ 是真缺口**：spawn 上限与执行中上限**本来就不是为防锁竞争设计的**，是**限流 / 容量**原语——iWork 没有对应物。全仓 `Semaphore` 0 命中、无 `max_agents` / spawn 计数（`config.py:46` 的 `max_queue_size=10` 是**入队**上限，不是并发执行上限）。

**`agent_status` 只算半个**：推送有（`query_loop.py:2096-2102` 的 done / error、`task_handler.py:103-108` 的 running）、模型有（`models/events.py:231` 的 `AgentStatus`，已并入 `StreamChunk`）、总线有（`EventBus`，`main.py:44-50` 订阅 stream + audit）；但缺 `watch` 的**状态持有**语义——`EventBus.emit`（`observability/event_bus.py:26-35`）是 fire-and-forget 广播，**无当前态可查、无回放、新订阅者拿不到现态**。两处未完工痕迹：`AgentStatus` 模型**从未被实例化**（代码只往 `_push_chunk` 塞裸 dict）；`status` 的 `"thinking"`（`models/events.py:237`）**全仓无置位**，是死值——子 agent 实际只有 running / done / error，前端看到的是"突然 running 跳 done"，中间的 thinking 段没有。

**这句把结论收窄了**：下一段说"共享面被压到近乎零、锁无用武之地"**成立**，但它只覆盖"**锁**"这一类；Codex 那 5 个原语里另有 2 个是**限流**，iWork 缺它们不是架构收益。**"省掉了锁"和"缺了限流"是两码事。**

**为什么可以不用锁**：iWork 把 Codex 的一棵线程树压成了"一个 session 一个引擎、一条 `run()` 协程"（§9.12.4），于是 Codex 那些"跨线程共享"的对象在 iWork 要么退化成**每 session 私有**（`_chunk_queue`、`_seq`、`_cancel_event`），要么**只在同一事件循环里被访问**（`_engines`、`_wake_event`）。**共享面被压到近乎零，锁就没有用武之地。** 这是架构收益，不是技术债。

> **收窄一句（§9.12.2 层2 落地后）**：上面的盘点说的是**层1**——它靠分段调度替代锁（"并发执行、按序收口"），确实不需要锁原语。但 **层2 的「工作区写锁」是 iWork 第一个跨 agent / 跨 session 的锁**，只存在于跨 agent 那一层。用的是 `asyncio.Lock`——单线程单事件循环，不是 `threading.Lock`（全仓库 `server/` 下无任何线程锁）；**引擎内已有一把 `_lock`（`query_loop.py:340`，护消息仓库的读改写，`enqueue` `:1159` / `_dequeue_next` `:1239` 两处持锁，见 §9.12.4），但它是引擎私有、不跨 session，不属上面盘点的那一类**。并对齐 §9.12.7 的单进程前提。

#### 9.12.4 单 turn 单写者：靠「一 session 一引擎」，不是靠 `active_turn` 锁

Codex 最硬的一条保证是：`active_turn` 用异步互斥锁做 check-and-set，**同一线程绝不并发跑两个回合**（用户输入与邮箱邮件同时到达时只有一方通过检查）。iWork 提供同一条保证，但靠的是**结构**而非锁：

- 每个 session 恰好一个 `QueryLoopEngine`，且只启动**一条** `run()` 协程（`EngineManager.get_or_create` 里 `asyncio.create_task(engine.run())`，`query_loop.py:286`）；
- `run()` 是单消费者循环：出队 → `_execute_message` → 回到出队（`query_loop.py:937-961`），一条消息处理完才取下一条件。**"一 session 内不会有两个回合并发"是循环结构直接给的。**

⚠️ **一个容易误读的点**：`self._lock = asyncio.Lock()`（`query_loop.py:340`）**不是 turn 锁**——它只护 `enqueue()`（`:1159`）与 `_dequeue_next()`（`:1239`）对**消息仓库**的读-改-写（查重 → 计数 → 插入；出队 → 重排序号），防的是两个请求同时改队列，跟"turn 能不能并发"无关。

**忙时是"拒绝"不是"排队"**（与 Codex 同向）：引擎 `state == "PROCESSING"` 时，`reprocess()` 直接返回 `{"status": "busy"}`（`query_loop.py:1274-1275`），HTTP 层映射为 **409**（`api/routes.py:766-768`）；队列满同样是拒绝（§9.12.5）。

**缺口**：`state` 目前只有两个取值——`IDLE`（`query_loop.py:337`）与 `PROCESSING`（`:972`），队空时回 `IDLE`（`:956`）。模块 docstring 里画的 `WAITING_SYNC`（`:7`）**从未被赋值**，而 §9.11.8 需要的 `WAITING_CHILDREN`（父回合收尾但仍有在途子任务，不能置 IDLE、不能写 `message.complete`）**根本不存在**。也就是说，§9.11.8 的"自动挂起等待"落地时，**第一步就是先补状态机**——清单第 2 条。

#### 9.12.5 并发上限：只有「队列层」，Codex 那三层闸门一层都没有

Codex 用三层闸门管并发（总数 / 执行中 / 常驻内存），且超限一律**拒绝或驱逐，不排队**。iWork 现状对位：

| Codex 三层 | iWork 现状 | 为什么 |
|---|---|---|
| ① spawn 总数（原子计数 + CAS + RAII，超限拒派生）| **无计数器，但天然有界** | `task` 的 `enum` 只含本团成员（`query_loop.py:860-863`，已排除 lead）；§9.11.6 又定"followup 复用同一 child session、不每次新建" → 派生线程数 ≤ 成员数。**枚举行委派天生有界，不需要原子计数** |
| ② 执行中上限（活跃子 agent 数，超限子回合不启动）| **无** | 子 agent 当前天然 ≤ 1：`task` 不在 `scheduling.yaml` 的 `read_tools` 里 → 被判**写段** → 段内串行（`14-单回合工具调用并发.md` §1.2⑥、`query_loop.py:2387-2391`）。但 §9.11.8 换异步派发后**这一层必须补**，否则一轮 fan-out 出去的 N 个子 agent 无任何并发上限 |
| ③ 常驻 LRU 驱逐 | **无** | `_engines` 只在归档时被 `pop`（`api/routes.py:348`），**没有任何驱逐或容量上限** → 长跑进程里引擎对象与它持有的会话状态永不释放 |

唯一存在的闸门是**队列层**：`max_queue_size = 10`（`server/config.py:46`），按 `count_pending` 统计（`query_loop.py:1167-1169`），超限抛 `QueueFullError` → **429**（`api/routes.py:410`）。语义上与 Codex"超限拒绝而非排队"同向，但**管的不是一回事**——它限的是"攒了多少条待处理消息"，与"同时有多少个 agent 在跑"无关。

还有一个 in-process 的隐含缺口：**读段 fan-out 无上限**。`_run_read_segment` 对整段读调用做 `asyncio.gather`（`query_loop.py:2403-2405`），全库**没有任何 `asyncio.Semaphore`**（已 grep 确认），并发度直接等于模型一轮发出的读调用个数。

#### 9.12.6 持久化一致性：DB 是权威，出队是全库唯一的原子原语

Codex 的持久化模型是：每线程一份 **rollout 追加日志**（权威正本）→ 后台写队列（容量 256）→ **flush 屏障**（把异步写变成同步落盘）→ 每线程写锁 + 跨进程锁文件；DB 里的表只是**可重建的投影**。

iWork 没有 rollout 这一层：**DB 本身就是权威**——`messages` 记消息与其状态，`conversation_history` 记对话历史。相应的"串行化写入"由两处机制承担：

1. **出队的原子 SQL**——全库**唯一**的原子原语：`SELECT ... FOR UPDATE SKIP LOCKED` → `UPDATE status='processing'`（`server/storage/postgres.py:134-163`，由 `query_loop.py:1237-1240` 持 `_lock` 调用）。它保证同一条待处理消息只会被一个消费者取到。
2. **入库幂等 + 消息状态机**——`client_message_id` 去重（`query_loop.py:1160-1165`）承担"重复投递不重复执行"；`pending → processing → completed / error / cancelled`（`query_loop.py:1185` / `:977` / `finally :1048-1057`）承担 Codex"记了账 ≠ 落了盘"的位置。这两条的完整语义在 `17-幂等性与副作用控制.md`（§17.3 摄入幂等、§17.5 中断恢复三入口），本节不复述。

> 其余 repo 方法都是 autocommit（每次 `async with self._sf()` 一个隐式事务），**没有显式事务边界、没有第二处 `with_for_update`、没有 advisory lock、没有 `ON CONFLICT` upsert**（已 grep 确认）。也就是说，跨表一致性目前**没有数据库级保障**，靠的是"单写者"（§9.12.4）在上游把并发收敛掉。

**缺口（对应 Codex 的 flush 屏障）**：Codex 在 `fork` 派生子线程**之前先 flush 父历史**，保证子继承到的是**已提交一致**的历史而不是排队中的尾巴。iWork 目前没有 fork（§9.11.5 未实现），所以不构成缺陷；但 §9.11.5 落地时**必须先把父历史落库再建子 session**，否则子 agent 一出生就继承到一段未落盘的尾巴——这条要写进 §9.11.5 的实现前置条件（清单第 3 条）。

#### 9.12.7 多进程下：一个 session 中不同消息可能进入不同进程，从而有多个 engine

这是本节**最容易被忽略**的一条：iWork 的并发正确性建立在"**单进程部署**"这个前提上，而代码里只有一半做了跨进程安全，另一半没有——**这一半会掩盖另一半**。

| 机制 | 位置 | 跨进程安全？ |
|---|---|---|
| 消息出队 | `postgres.py:134-163`（`FOR UPDATE SKIP LOCKED`）| ✅ 安全——DB 保证同一条消息不双取 |
| `_wake_event` 唤醒 | `query_loop.py:338` | ❌ **进程本地**——只唤醒本进程的引擎 |
| `_engines` 注册表 | `query_loop.py:226` | ❌ **进程本地**——各进程各有一份 |
| `_seq` | `query_loop.py:443-445` | ❌ **进程本地**——各进程序号会撞 |

若将来把 uvicorn 起成多个 worker（当前 `readme.md:5,7` 都是单进程启动，无 `--workers`），同一 session 的消息可能被**另一个进程**出队，而唤醒落在**本进程**的引擎上——两个进程里各有一个"该 session 的引擎"，各自都以为自己是独占的。**"单 session 单引擎"这条 §9.12.4 的地基会失效，而出队的原子性恰好让这件事看起来没问题。**

**为什么会冒出第二个引擎**——`get_or_create` 只问"**本进程**的字典里有没有"（`query_loop.py:264-287`）：

```python
async def get_or_create(self, session: Session) -> "QueryLoopEngine":
    if session.id not in self._engines:        # ← 本进程的字典
        engine = QueryLoopEngine(session=session, ...)
        engine._task_handler = TaskToolHandler(self, self._plugin_loader)
        self._engines[session.id] = engine
        asyncio.create_task(engine.run())      # ← 只有新建才起循环
    return self._engines[session.id]
```

这个 `if` 判的不是"该不该给这个 session 再建一个引擎"，而是"**我这儿**有没有它的引擎"——它没有别的信息可查：没有共享注册表、没有 DB 行、没有锁。于是：

- **每个碰到过该 session 的进程，都会长出一个引擎 + 一条常驻的 `run()` 循环**（`create_task` 在 `if` 内，每进程每 session 恰好一条）；
- 而且 `_engines` **全库只有一个 `pop` 点，且只 pop 子会话引擎**（`api/routes.py:348`，父自身的引擎连归档时都不 pop，见 §9.12.9 第 1 条），无驱逐、无上限 → 只增不减；
- N 条循环同时 poll 同一个 session 的队列——`FOR UPDATE SKIP LOCKED` 保证同一条消息不被双取，但它们会**轮流**取走该 session 的不同消息，于是同一个会话被多个进程交替处理。

本节把它记为**假设与后果**，不列为缺陷——当前部署确实单进程。要真支持多副本，需要的不是"给 dict 加锁"，而是把"谁是该 session 的引擎"从进程内状态提升为**带租约的共享状态**（DB 行锁 / 心跳 / 单会话粘性路由三选一）。**本轮不做。**

**延伸：多副本下「工作区写锁」怎么判（本轮不做）**

§9.12.2 层2 的「工作区写锁」是 `asyncio.Lock`——**进程本地**。多副本下两个进程各有一把、互相看不见，等于没锁。要真支持多副本，**先得满足前提：session→进程亲和**（同一个 session 的请求永远路由到同一个进程，即上面说的「带租约的共享状态」或粘性路由）。**亲和一旦成立**，跨进程的交互就只剩一种——**不同 session（不同进程）共用同一个工作区、同时写**，而那正是层2 要挡的形态。所以：**粘性亲和 + 跨进程工作区写锁，两件事合起来才等于「多副本下的层2」**；只做前者，写冲突照样发生。

还有一个容易忽略的点：**锁不能是工作区里的一个锁文件**。`workspace` 是**客户端路径**（`server/db/models.py:87-90` 的 Text 列，客户端自填的绝对路径），**服务端从来不碰那台机器的文件系统**——连目录都打不开，更别说在里面建锁文件。所以锁的 key 只能是**工作区字符串本身**（hash 后）；锁的"资源"是逻辑上的，不是文件系统上的。

三条候选：

| 路线 | 怎么抢 | 优点 | 代价 |
|---|---|---|---|
| **DB 租约行**（推荐）| `workspace_locks` 表，条件 upsert + 心跳续租 | 不占连接；与本节上面「带租约的共享状态」**是同一套机制**——一张表可同时服务 engine 亲和与写锁 | 要心跳 + 陈旧回收 |
| Postgres advisory lock | `pg_try_advisory_lock(hashtext(ws))` | 代码最少；进程崩 → 连接断 → 锁**自动释放**，不留陈旧锁 | 锁是**连接级**的，必须独占一条连接持到客户端写完（`bash` 写可跑几分钟）→ **连接池饥饿**（`pool_size=10`，`server/db/engine.py:6`）|
| 服务端本地文件锁 | 按 `sha1(workspace)` 在**服务端本地**目录 flock | 无新表；进程崩自动释放 | **只能同机**跨进程，多台后端无效；Windows / Unix 两套 API |

**推荐 DB 租约行**，形状：

```
workspace_locks(workspace_key PK, owner_session, owner_pid, expires_at)
抢锁: UPDATE ... SET owner=me, expires=now()+TTL
       WHERE key=? AND (owner IS NULL OR expires<now())   -- rowcount==1 即拿到
持有: 每 TTL/3 续租一次
回收: 进程崩溃 → 到期自然可抢
```

**为什么不选 advisory lock**：它最省代码，但**锁的粒度是连接**——而层2 的持有范围是从下发 `client.tool_request` 到结果收口（§9.12.2 层2 第 4 点），`bash` 写能跑几分钟，等于几分钟占死一条连接；`pool_size=10` 下几个不同工作区同时写就抽干连接池。它唯一的优势是"进程死即解锁"，但租约的 TTL 也能给到同样的兜底，只多一个 TTL 的延迟。

**四个必须写清的边界**：

1. **心跳是必需的，不是优化**。TTL 若短于一次 `bash` 写的时长，租约会中途过期 → 第二个写者进来 → **恰好发生这把锁本该防的写写冲突**。所以续租间隔必须 << TTL，且**续租失败必须中止写**（不能"没续上还继续写"）。
2. **等待是重试，不是阻塞**。租约模型下"等锁"没有可阻塞的 DB 原语可用（要阻塞就得占着连接，又回到池饥饿）。正确写法是 `try-acquire` 失败 → 退避重试（~200ms），直到拿到或租约空出——这也正合层2「只排写」的语义。
3. **时间一律用 DB 的 `now()`**，不用各进程自己的钟——多机时钟偏移会让 `expires_at` 的判定错乱。
4. **key 的归一化只能做到字符串级**。`workspace` 是客户端自填的自由文本、**无唯一性约束**（`server/api/routes.py:214`）：`D:\proj` 与 `d:\proj\` 明明是同一个目录却会算成两把锁；更麻烦的是**空值会被归成一把锁**——子 session 的 `workspace` 默认是 `""`（`server/engine/task_handler.py:45-57`），于是多个空工作区的 session 会被误判成"共用同一批文件"而互相排队。服务端没法 `realpath` 一台它看不见的机器上的路径，所以这一层只能靠**客户端在传 workspace 之前先归一化**。

**与 §9.12.2 层2 / §9.12.9 的关系**：落点和层2 同一个（写段循环按调用取锁），只是锁对象从 `asyncio.Lock` 换成租约。清单第 7 条（多副本时的会话独占）与这段**是同一张租约表的两个用途**——一个是"谁是该 session 的引擎"，一个是"谁在写这个工作区"。**本轮不做**，理由与 §9.12.10 一致：当前部署单进程，多副本无收益。

#### 9.12.8 明确接受的竞态窗口

Codex 明确列了 5 条它接受的窗口。逐条对到 iWork：

| Codex 接受的窗口 | iWork | 说明 |
|---|---|---|
| `watch` 订阅者看到 `agent_status` 短暂陈旧 | **不存在** | iWork 没有状态广播这层；agent 状态是 chunk 事件，不是可查询的共享状态 |
| 派生关系图 spawn 边最终一致（最后写者赢）| **同样接受** | `_engines` 无锁 + `parent_id` 写库非原子；并发下同样是"最后写者赢" |
| 原子计数与活跃 agent 映射非原子一起更新 | **同样接受** | `get_or_create` 里"查 dict → 建 engine → 写 dict"与 DB 无关，天然非原子 |
| 邮箱"查空才允许驱逐"与驱逐之间的小窗口 | **不存在** | iWork 没有 LRU 驱逐（§9.12.5③）——窗口不存在，代价是内存无界 |
| 纯内存邮箱，进程崩溃即丢 | **不存在（iWork 更强）** | §9.11.7 定的邮箱走 DB 消息表，持久化与重连回放由现有机制免费提供 |

iWork **特有**、要单列的三条：

1. **展示顺序 ≠ 收口顺序**。读段里 `_dispatch_tool` 是**按模型顺序串行下发**的（每步含权限判定、Hook、工具账本记账这些读-改-写动作，见 `query_loop.py:2398-2401`），只有**回投等待**才 `asyncio.gather` 并行（`:2403-2405`）。因此 `_push_chunk` 推给前端的 chunk 顺序 = **工具完成顺序**，而历史收口（`_finalize_tool`）严格按**模型发出顺序**串行（`:2382-2389`）——因为历史拼接是 `MAX(sequence)+1` 分配序号 + `tool_calls` 数组合并的读-改-写，并发收口会丢块、产生孤儿 `tool` 行（`:2373-2374` 的 docstring 写明了这条）。**展示层按完成顺序、语义层按发出顺序**，这正是 §9.11.9「语义 vs 展示分离」在单 agent 内的翻版。
2. **`get_or_create` 形式上是 check-then-create，但临界区内没有 `await`**（`query_loop.py:264-287`：查 dict → 构造引擎 → 装配依赖 → 写回 dict → `create_task`，全是同步调用）。单事件循环下这**实际是原子的**——不存在两个协程同时建同一 session 的引擎。但它**脆**：将来只要在临界区里加任何一个 `await`（比如 §9.11.3 要落 `agent_path`、顺手去 DB 读一次 `agents`），立刻会破功并产生**同 session 双引擎双 `run()`**。故记为**脆弱点**而非缺陷（清单第 8 条）。
3. **"读到半截"被接受**（§9.12.2 层2）。工作区写锁只排写、不排读，所以一个 agent 写文件时，另一个 agent 读同一文件可能读到写了一半的内容。这与 `14-单回合工具调用并发.md` §3.7.4 一致——挡它们要靠"按路径的读写互斥"，代价大于收益，本轮不做。

#### 9.12.9 实现路径（待实现清单）

| # | 竞态 / 缺口 | 落点 | 为什么重要 |
|---|---|---|---|
| 1 | **归档父会话不取消在跑的子**：`DELETE /sessions/{id}` 只把子引擎从 `_engines` 里 `pop`（`api/routes.py:348`）+ 归档，**既不调 `cancel_tree` 也不 `task.cancel()`** | `api/routes.py:336-356` vs `query_loop.py:292-299` | 两个后果：① 子引擎的 `run()` 协程没被取消，仍继续跑并往**已归档**的 session 写历史、推事件；② 登记被 `pop` 掉后，后续任何 `get_or_create` 会**再建一个同 id 引擎** → 同 session 双引擎、双 `run()`（§9.12.4 的地基被自己破坏）。对照：`cancel_tree` 目前只被 `POST /cancel` 调用（`api/routes.py:722`）|
| 2 | `WAITING_CHILDREN` 状态 + 挂起 / 恢复 | `query_loop.py:337` / `:972` / `:956`（docstring `:7` 的 `WAITING_SYNC` 从未赋值）| §9.11.8"收尾时挂起等待"的落地前提——没有这个状态就表达不了"本轮要结束但不该写 `message.complete`" |
| 3 | fork 前先 flush 父历史（快照一致）| `task_handler.py:44-57`（建子 session 处）| §9.11.5 的实现前置条件：先落库父历史再建子，否则子继承到未落盘的尾巴（对应 Codex 的 flush 屏障）|
| 4 | 读段 fan-out 无上限（无 `asyncio.Semaphore`，全库无命中）| `query_loop.py:2398-2405` | 一轮 N 个读工具全量并发下发，客户端 / MCP / 沙箱压力无闸；也是 §9.12.5②"执行中上限"缺失的具体表现 |
| 5 | `_engines` 无驱逐、无容量上限 | `query_loop.py:226`、`api/routes.py:348`（唯一 pop 点）| 长跑进程内存无界增长（对应 Codex V2Residency 的镜像缺口）；驱逐前需确认该 session 队列与在途子任务均空 |
| 6 | `agent_path` + `root_session_id` 落库与唯一约束 `(root_session_id, agent_path)` | `server/models/session.py:42-58` + 新增 Alembic `013_*`（现最新 `012_add_tool_invocation_attempt.py`）| §9.11.3 寻址层的正确性依赖它；与本节并发写入时序耦合（先建列再写值）|
| 7 | 多副本（>1 worker）时的会话独占 | `query_loop.py:338` / `:226` / `:443-445` | 记录不实现：当前单进程前提一旦松动，"单 session 单引擎"失效（§9.12.7）|
| 8 | `get_or_create` 临界区"禁止 `await`" | `query_loop.py:264-287` | 脆弱点（非缺陷）：加一行 `await` 即产生双引擎，需在代码处以注释钉住 |
| 9 | **跨 agent / 跨 session 的工作区写锁**：`EngineManager` 加 `dict[workspace, asyncio.Lock]` + 取锁入口；写段循环**按调用**取锁、`task` 跳过（§9.12.2 层2）| `query_loop.py:206-226`（建锁）、`:264-287`（注入）、`:2386-2391`（取锁）| 这是 §9.11.8「异步派发」的**前置安全网**——不先加锁就放开一轮 fan-out 并行派发，等于放开多个子 agent 抢同一个文件 |

**交叉引用（不在本节实现）**：跨 agent 写互斥的**粗档**已由本节 §9.12.2 层2 own（工作区写锁，已设计未实现）；更细的**路径域**档位 → `14-单回合工具调用并发.md` §3；摄入幂等与中断恢复三入口 → `17-幂等性与副作用控制.md` §17.3 / §17.5；N 子并行派发后的 token / 成本仲裁 → `11-成本控制.md`。

#### 9.12.10 明确不做

- **不做文件层冲突检测 / 版本号 / merge**——`14-单回合工具调用并发.md` §3 owns（路径域 + X/S + 意向锁），且文件读写发生在客户端，不在服务端职责内
- **不做跨进程分布式锁 / advisory lock**——只记录假设与后果（§9.12.7），当前单进程部署下无收益；工作区写锁本身也是进程本地（§9.12.7 已记三条候选与推荐：DB 租约行）
- **不给读加锁**（全局读写锁 / 按路径精确锁）——理由见 §9.12.2 层2 第 2 点；接受"读到半截"
- **不重写邮箱持久化与中断恢复语义**——`17-幂等性与副作用控制.md` owns；本节只引用为"iWork 比 Codex 强的一点"
- **不做成本 / 预算的并发仲裁**——`11-成本控制.md` owns（§9.11.8 并行派发落地后父预算可能被 N 个子同时透支）
- **不引入全局大锁**——与 Codex 的分层哲学一致：先压共享面（§9.12.3），再谈锁；且"工作区写锁"只排写、不排读，不等于全局互斥
- **不复述 `FOR UPDATE SKIP LOCKED` 的机制本身**——`1-Query Loop 引擎.md` owns，本节只引为"全库唯一原子原语"

---

<a id="10-上下文管理"></a>

