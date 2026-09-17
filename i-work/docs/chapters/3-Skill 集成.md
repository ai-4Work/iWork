# 3. Skill 集成

## 目录

- [3.1 架构概览](#31-架构概览)
- [3.2 Skill 格式与结构](#32-skill-格式与结构)
- [3.3 Skill 生命周期](#33-skill-生命周期)
- [3.4 Skill 配置](#34-skill-配置)
- [3.5 Skill 发现与注册](#35-skill-发现与注册)
- [3.6 Skill 执行流程（Tool-based 按需加载）](#36-skill-执行流程tool-based-按需加载)
- [3.7 错误处理](#37-错误处理)
- [3.8 Skill 管理（Hub）](#38-skill-管理hub)
- [3.9 Skill 查询接口定义](#39-skill-查询接口定义)
- [3.10 与 Query Loop 引擎的集成点总结](#310-与-query-loop-引擎的集成点总结)

### 3.1 架构概览

Skill 是 iWork 改变 AI 行为模式的核心机制。与 MCP 工具（为 LLM 增加外部工具调用能力）不同，Skill 采用 **tool-based 按需加载**模式：所有已安装的 Skill 以 `<available_skills>` XML 块注入 system prompt，LLM 根据任务需要主动调用 `skill` 工具加载特定 Skill 的核心指令（SKILL.md），如需脚本或示例则通过现有工具（read_file/bash）在后续流程中动态读取。

```
┌── Electron Client ──┐     ┌── FastAPI Server ───────────────────────────────┐
│                      │     │                                                  │
│  Skill Hub UI        │     │  ┌── SkillRegistry (内存级) ─────────────────┐  │
│  (配置面板，           │     │  │                                           │  │
│   浏览/安装/启停)      │     │  │  skill-hub.json ──→ {skill_id: SkillDef}   │  │
│                      │◄───►│  │  skill_state.json → installed_ids          │  │
│  用户消息 →           │ API │  │                                           │  │
│  POST /messages      │─────►│  └──────────────┬────────────────────────────┘  │
│                      │      │                │                                │
│                      │      │                ▼                                │
│                      │      │  ┌── ContextManager.build() ─────────────────┐  │
│                      │      │  │  system_prompt += <available_skills> XML   │  │
│                      │      │  │  tools += skill 工具定义                   │  │
│                      │      │  │  → llm.stream(system, tools)              │  │
│                      │      │  └───────────────────────────────────────────┘  │
│                      │      │                │                                │
│                      │      │                ▼                                │
│                      │      │  ┌── QueryLoopEngine ────────────────────────┐  │
│                      │      │  │  LLM 调用 skill(name="code-review")        │  │
│                      │      │  │  → client.tool_request → 前端读本地文件     │  │
│                      │      │  │  → 工具结果注入 LLM 上下文              │  │
│                      │      │  │  → LLM 按指令行事，脚本/示例按需读取       │  │
│                      │      │  └───────────────────────────────────────────┘  │
└──────────────────────┘     └──────────────────────────────────────────────────┘
```

**两层架构：**

| 层 | 组件 | 职责 |
|---|---|---|
| **配置层** | `skill-hub.json` + `skill_state.json` | 存储 Skill 元数据（Hub 目录）和用户级安装状态。每个 Skill 对应一个文件夹，内含 SKILL.md 核心指令文件 |
| **注入层** | `ContextManager.build()` + `SkillRegistry` | 构建 `<available_skills>` XML 注入 system prompt；注册 `skill` 工具供 LLM 调用（客户端执行）；通过 `skill_invocations` 支持预加载 |

**Skill vs MCP 核心差异：**

| 维度 | MCP | Skill |
|------|-----|-------|
| 本质 | 外部进程运行时 | tool-based 按需加载核心指令 |
| 通信方式 | JSON-RPC 2.0 over stdio/HTTP | LLM 调用 `skill` 工具 → client.tool_request → 客户端读本地 SKILL.md |
| 生命周期 | CONNECTING → INITIALIZED → READY → ERROR | 安装 ↔ 卸载 |
| 运行时状态 | 进程句柄、传输连接 | 本地文件夹 + SKILL.md 文件（`~/.iwork/skills/`） |
| 执行方式 | 客户端 MCP 进程 → `tools/call` → 结果 | `skill(name)` → 客户端读本地文件 → 返回 tool_result |
| 故障模式 | 进程崩溃、超时、协议错误 | SKILL.md 文件缺失、skill name 不存在 |
| 流数据块 | `client.tool_request` / `POST /tool-result` | `client.tool_request` / `POST /tool-result`（同客户端工具） |
| 安装行为 | 写入 state + 返回配置（客户端 spawn 进程） | 写入 state + 返回 zip（客户端解压到本地） |

### 3.2 Skill 格式与结构

#### 3.2.1 文件夹结构

每个 Skill 是一个独立文件夹，位于 `server/skills/definitions/` 下。文件夹名与 `skill-hub.json` 中的 `folder_path` 对应：

```
server/skills/definitions/
├── code-review/             ← folder_path: "code-review"
│   ├── SKILL.md             ★ 核心指令文件（skill 工具调用时返回的内容）
│   ├── scripts/             ← 可选：辅助脚本
│   └── examples/            ← 可选：示例文件
├── doc-generator/
│   ├── SKILL.md
│   └── templates/           ← 可选：文档模板
└── ...
```

`SKILL.md` 是 Skill 的核心——它是 LLM 调用 `skill` 工具后唯一返回的内容。文件夹内的其他资源（脚本、示例、模板等）不会自动加载，而是由 LLM 在后续流程中通过 `read_file`、`bash` 等现有工具按需读取。

#### 3.2.2 Hub 元数据字段定义

`skill-hub.json` 中每个 Skill 条目仅包含**元数据**，不含核心指令文本。核心指令存放在对应文件夹的 `SKILL.md` 中。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `skill_id` | string | 是 | 唯一标识。Hub: `sk` + 序号，Custom: `cs` + 序号，Builtin: `bi` + 序号 |
| `skill_name` | string | 是 | 显示名称，同时也是 `skill` 工具调用的 `name` 参数值 |
| `description` | string | 是 | 功能简述，用于 `<available_skills>` XML 和 Hub 卡片展示 |
| `folder_path` | string | 是 | Skill 文件夹路径（相对于 `server/skills/definitions/`） |
| `version` | string | 否 | 语义化版本号（Hub 发布用） |
| `category` | string | 否 | 分类标签，如"开发""文档""效率" |
| `icon` | string | 否 | Emoji 图标，用于 UI 展示 |
| `author` | string | 否 | 作者名（Hub 发布用） |
| `tags` | string[] | 否 | 搜索/发现标签 |
| `source` | enum | 否 | `hub` / `custom` / `builtin` |

#### 3.2.3 完整示例

**`skill-hub.json` 条目（仅元数据）：**

```json
{
  "skill_id": "sk1",
  "skill_name": "code-review",
  "description": "以资深代码审查员视角分析代码，关注安全性、性能和可维护性",
  "folder_path": "code-review",
  "version": "1.2.0",
  "category": "开发",
  "icon": "🔍",
  "author": "iWork Team",
  "tags": ["code", "review", "security"],
  "source": "hub"
}
```

**`server/skills/definitions/code-review/SKILL.md`（核心指令）：**

```markdown
## Skill: 代码审查

你正在扮演一位资深代码审查员。在分析代码时，请遵循以下原则：

1. **安全性优先**：首先检查 OWASP Top 10 漏洞（SQL注入、XSS、CSRF 等）
2. **性能分析**：识别 N+1 查询、不必要的内存分配、阻塞操作
3. **可维护性**：检查命名规范、函数复杂度（超过 15 行建议拆分）、重复代码
4. **输出格式**：使用表格总结发现的问题，按严重程度排序（🔴 严重 / 🟡 中等 / 🟢 建议）
```

> **核心理解：** `SKILL.md` 就是 Skill 的全部。`skill-hub.json` 中的 `description`、`icon` 等字段只是 UI 元数据和 `<available_skills>` 展示文本。真正改变 LLM 行为的是 `skill` 工具调用后返回的 `SKILL.md` 内容。

#### 3.2.4 内置 Skill

系统预装、不可卸载的内置 Skill（对应 `source: "builtin"`）：

| skill_id | 名称 | 用途 | 来源 |
|----------|------|------|------|
| `bi1` | 日常办公 | 默认 `office` 模式的 system prompt 片段 | `context.py` 中的 `SYSTEM_PROMPTS["office"]` |
| `bi2` | 代码开发 | 默认 `code` 模式的 system prompt 片段 | `context.py` 中的 `SYSTEM_PROMPTS["code"]` |

内置 Skill 硬编码在服务端，不参与安装/卸载流程。现有的 `SYSTEM_PROMPTS` 和 `MODE_PROMPTS` 本质上已经是内置 Skill 的形式。

#### 3.2.5 ID 前缀规则

| 前缀 | 来源 | 示例 | 安装态 | 卸载 |
|------|------|------|--------|------|
| `sk` | Hub Skill | `sk1`, `sk42` | 可安装 | 可卸载 |
| `cs` | Custom Skill（用户自建） | `cs1`, `cs2` | 创建即安装 | 删除即卸载 |
| `bi` | Builtin Skill（系统内置） | `bi1`, `bi2` | 始终安装 | 不可卸载 |

### 3.3 Skill 生命周期

Skill 没有进程、没有连接、没有运行时状态。它的生命周期就是一个安装/卸载的标记流转：

```
                    ┌──────────────────────────────────────┐
                    │          SKILL LIFECYCLE              │
                    │                                       │
    ┌──────────┐    │                                       │
    │UNINSTALL │    │                                       │
    │    ED    │───►│  INSTALLED                             │
    │          │    │  (出现在 <available_skills>             │
    └────┬─────┘    │   中，LLM 可通过 skill                   │
         │          │   工具加载)                             │
         │ 卸载     │                                       │
         │          │                                       │
         └──────────┼───────────────────────────────────────┘
```

| 状态 | 说明 | 用户可见行为 |
|------|------|-------------|
| **UNINSTALLED** | Skill 不在用户的 `skill_state.json` 中 | 不出现在 `<available_skills>` 中 |
| **INSTALLED** | `skill_id` 已写入 `installed_ids` | 出现在 `<available_skills>` 中，LLM 可通过 `skill` 工具加载 |

> **与旧方案的关键区别：** 不再有 ENABLED/DISABLED 的概念。Skill 安装即可用，所有已安装 Skill 均出现在 `<available_skills>` 列表中。模型自行决定何时调用哪个 Skill，无需用户预设。

### 3.4 Skill 配置

#### 3.4.1 配置文件格式

**`skill-hub.json`**（服务端 Hub 目录）：

由管理员维护，用户只读。存储所有可安装的社区/团队 Skill 的元数据（不含 SKILL.md 内容）：

```json
{
  "skills": [
    {
      "skill_id": "sk1",
      "skill_name": "code-review",
      "description": "以资深代码审查员视角分析代码，关注安全性、性能和可维护性",
      "folder_path": "code-review",
      "version": "1.2.0",
      "category": "开发",
      "icon": "🔍",
      "author": "iWork Team",
      "tags": ["code", "review", "security"]
    },
    {
      "skill_id": "sk2",
      "skill_name": "doc-generator",
      "description": "自动生成 README、API 文档和代码注释",
      "folder_path": "doc-generator",
      "version": "1.0.0",
      "category": "文档",
      "icon": "📝",
      "author": "iWork Team",
      "tags": ["docs", "readme", "api"]
    }
  ]
}
```

**`skill_state.json`**（用户级安装状态）：

每用户独立存储，仅记录安装了哪些 Skill ID 和自定义 Skill：

```json
{
  "installed_ids": ["sk1", "sk3", "cs1", "bi1", "bi2"],
  "custom_skills": [
    {
      "skill_id": "cs1",
      "skill_name": "my-code-style",
      "description": "遵循团队 ESLint 配置的代码风格",
      "folder_path": "my-code-style",
      "category": "自定义",
      "icon": "✨",
      "source": "custom"
    }
  ]
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `installed_ids` | string[] | 用户已安装的所有 Skill ID（含 Hub、Custom、Builtin） |
| `custom_skills` | object[] | 用户自建的 Skill 元数据定义 |

> **设计要点：** 不再有 `disabled_ids`。所有已安装的 Skill 均出现在 `<available_skills>` 中，由 LLM 根据任务需要主动选择。用户如不需要某个 Skill，直接卸载即可。

#### 3.4.2 配置层级变化

旧方案中 skill 通过会话级 `default_skill_ids` 和消息级 `skill_invocations` 控制注入哪些 skill 的 prompt。新方案中这些字段被**移除**：

| 字段 | 旧方案 | 新方案 |
|------|-------|-------|
| `Session.default_skill_ids` | 会话级默认自动注入的 skill | **移除**——不再自动注入 prompt |
| `MessageCreate.skill_invocations` | 消息级手动指定 skill | **移除**——由 LLM 通过 `skill` 工具按需选择 |
| `MessageCreate.skill_invocations` | — | **新增**——客户端显式选择 skill 时传入（列表，每项含 `skill_id` + `skill_name`），服务端预加载 SKILL.md 到对话首条 user 消息 |
| `skill_state.disabled_ids` | 已安装但临时停用的 skill | **移除**——安装即可用，不需要时卸载 |

Skill 的选择完全交给 LLM：系统提供 `<available_skills>` 列表 + `skill` 工具，模型根据当前任务上下文判断是否需要以及需要哪个 Skill。

#### 3.4.3 凭据管理

Skill 不需要凭据管理。Skill 只包含纯文本 prompt，不涉及 API key、token、密码等敏感信息。这是 Skill 比 MCP 简单的又一个根本原因。

### 3.5 Skill 发现与注册

#### 3.5.1 启动加载流程

与 MCP 需要 `tools/list` 网络调用不同，Skill 的"发现"就是启动时读文件：

```
  ┌─ SkillRegistry.__init__() ──────────────────────────────────┐
  │                                                               │
  │  1. 加载 skill-hub.json → hub_skills: dict[id, SkillDef]      │
  │  2. 加载 skill_state.json → 用户安装状态                       │
  │  3. 合并 custom_skills 到 lookup                              │
  │  4. 添加 builtin skills (硬编码) → 注入 lookup                 │
  │  5. 校验每个已安装 Skill 的 SKILL.md 存在且非空                │
  │                                                               │
  │  结果: registry._skills = {                                   │
  │    "sk1": SkillDefinition(..., folder_path="code-review"),    │
  │    "sk2": SkillDefinition(..., folder_path="doc-generator"),  │
  │    "cs1": SkillDefinition(..., folder_path="my-style"),       │
  │    "bi1": SkillDefinition(...),  # 内置                       │
  │    "bi2": SkillDefinition(...),  # 内置                       │
  │  }                                                            │
  │  registry._installed = {"sk1", "cs1", "bi1", "bi2"}          │
  │                                                               │
  └───────────────────────────────────────────────────────────────┘
```

**SkillRegistry 核心实现：**

```python
class SkillDefinition(BaseModel):
    """Skill 的元数据定义。核心指令在文件夹的 SKILL.md 中。"""
    skill_id: str
    skill_name: str
    description: str
    folder_path: str                     # Skill 文件夹路径（相对于 definitions_dir）
    version: str = "1.0.0"
    category: str = ""
    icon: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    source: Literal["hub", "custom", "builtin"] = "hub"


class SkillRegistry:
    """管理所有已知 Skill（Hub + Custom + Builtin）的内存注册表。"""

    def __init__(self, hub_path: Path, state_path: Path, definitions_dir: Path):
        self._skills: dict[str, SkillDefinition] = {}
        self._installed: set[str] = set()
        self._definitions_dir = definitions_dir
        self._load(hub_path, state_path)

    def _load(self, hub_path: Path, state_path: Path):
        hub = _load_json(hub_path) if hub_path.exists() else {"skills": []}
        state = _load_json(state_path) if state_path.exists() else {}

        # Hub skills
        for s in hub.get("skills", []):
            self._skills[s["skill_id"]] = SkillDefinition(**s, source="hub")

        # Custom skills from user state
        for s in state.get("custom_skills", []):
            self._skills[s["skill_id"]] = SkillDefinition(**s, source="custom")

        # Builtin skills (hardcoded)
        for s in BUILTIN_SKILLS:
            self._skills[s.skill_id] = s

        # User state: only installed_ids (no more disabled_ids)
        self._installed = set(state.get("installed_ids", []))
        # Builtins are always installed
        self._installed.update(s.skill_id for s in BUILTIN_SKILLS)

    # ── 新方案核心方法 ──

    def build_available_skills_xml(self) -> str:
        """构建 <available_skills> XML 块，注入 system prompt。"""
        installed = [self._skills[sid] for sid in self._installed
                     if sid in self._skills]
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

    def lookup_by_name(self, skill_name: str) -> SkillDefinition | None:
        """按 skill_name 查找已安装的 Skill 定义。"""
        for skill in self._skills.values():
            if skill.skill_name == skill_name and skill.skill_id in self._installed:
                return skill
        return None

    def get_skill_md(self, skill: SkillDefinition) -> str:
        """读取 skill 文件夹中的 SKILL.md 内容（按需二级加载的第一级）。"""
        md_path = self._definitions_dir / skill.folder_path / "SKILL.md"
        if not md_path.exists():
            raise FileNotFoundError(f"SKILL.md 不存在: {md_path}")
        return md_path.read_text(encoding="utf-8")

    def get_available(self) -> list[SkillDefinition]:
        """返回所有已安装的 Skill（用于错误提示中列出可用 skill）。"""
        return [self._skills[sid] for sid in self._installed
                if sid in self._skills]

    # ── 安装/卸载（不变） ──

    def install(self, skill_id: str):
        """安装 Skill（纯状态更新）。"""
        if skill_id not in self._skills or self._skills[skill_id].source == "builtin":
            raise ValueError(f"无法安装 Skill: {skill_id}")
        self._installed.add(skill_id)

    def uninstall(self, skill_id: str):
        """卸载 Skill（纯状态更新）。"""
        if self._skills[skill_id].source == "builtin":
            raise ValueError(f"内置 Skill 不可卸载: {skill_id}")
        self._installed.discard(skill_id)


# ── 内置 skill 工具定义（常量） ──

SKILL_TOOL_DEFINITION = {
    "name": "skill",
    "description": (
        "Load a specialized skill when the task matches one listed in "
        "<available_skills>. The skill name must match one from the "
        "available skills list."
    ),
    "parameters": {
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
```

#### 3.5.2 Skill ID 命名规则

```
skill-hub.json 条目      →  skill_id: "sk1", "sk2", "sk3", ...
custom_skills 条目       →  skill_id: "cs1", "cs2", "cs3", ... (自动分配)
内置 Skill               →  skill_id: "bi1", "bi2", ...
```

Hub 和 Custom 的 ID 空间独立自增，互不冲突。

#### 3.5.3 合并到 LLM 上下文

对应 `server/engine/context.py` ——构建 LLM 上下文时，在 system prompt 末尾注入 `<available_skills>` XML 块，并将 `skill` 工具添加到可用工具列表中。

**`<available_skills>` XML 格式：**

```xml
<available_skills>
  <skill>
    <name>code-review</name>
    <description>以资深代码审查员视角分析代码，关注安全性、性能和可维护性</description>
  </skill>
  <skill>
    <name>doc-generator</name>
    <description>自动生成 README、API 文档和代码注释</description>
  </skill>
</available_skills>
```

仅包含 `name` 和 `description`，不暴露文件系统路径。后端根据 `name` 通过 `SkillRegistry` 内部查找到对应的 `folder_path`，读取 `SKILL.md`。

**`skill` 工具定义：**

```json
{
  "name": "skill",
  "description": "Load a specialized skill when the task matches one listed in <available_skills>. The skill name must match one from the available skills list.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "The skill name to load, must match one from <available_skills>"
      }
    },
    "required": ["name"]
  }
}
```

**`skill` 工具为内置 SERVER 工具**，不在 MCP 中注册、不由客户端执行，而是在 `ToolDispatcher.classify()` 返回 `ToolLocation.SERVER` 后由引擎直接处理。

**ContextManager 改造：**

```python
# context.py - ContextManager.build()

async def build(
    self, session_id, turn, mode, scene_mode,
    client_tools=None,
    mcp_tools=None,
    server_tools=None,            # 新增：内置 SERVER 工具（含 skill）
    available_skills_xml="",      # 新增：<available_skills> XML 块
):
    history = self._contexts.get(session_id, [])
    system = SYSTEM_PROMPTS.get(scene_mode, SYSTEM_PROMPTS["office"])
    system += MODE_PROMPTS.get(mode, "")

    # ── 注入 <available_skills> XML ── 替换原 prompt 拼接
    if available_skills_xml:
        system += "\n\n" + available_skills_xml

    tools = None
    if mode == "plan" and turn == 0:
        tools = [PLAN_QUESTION_TOOL_DEF]
    elif mode != "ask":
        tools = []
        if client_tools:
            tools.extend(client_tools)
        if mcp_tools:
            tools.extend(mcp_tools)
        if server_tools:
            tools.extend(server_tools)    # skill 工具在此注入

    return Context(messages=history, system_prompt=system, available_tools=tools)
```

**system prompt 提示：** 在 `SYSTEM_PROMPTS` 中已加入 skill 选择提示：「如有匹配任务的 Skill 可用，优先调用 skill 工具加载对应能力。」

**冲突处理：** `<available_skills>` XML 追加在 scene/system prompt 之后、其他工具定义之前。Skill 之间不存在命名冲突——`skill_name` 在 `skill-hub.json` 中唯一，`SkillRegistry` 加载时做去重校验。

### 3.6 Skill 执行流程（Tool-based 按需加载）

#### 3.6.1 按需二级加载机制

Skill 的"执行"不是 LLM 调用前的字符串拼接，而是 LLM **主动调用** `skill` 工具后的**按需二级加载**：

```
Model 判断任务需要 skill 辅助
  │
  ├── 调用 skill(name="code-review")
  │
  ├── 后端 SkillRegistry.lookup_by_name("code-review")
  │     └── 查找 folder_path → "code-review"
  │
  ├── 第一级：读取 SKILL.md
  │     └── 读取 server/skills/definitions/code-review/SKILL.md
  │     └── 返回 tool_result { success: true, output: "<SKILL.md 内容>" }
  │
  ├── LLM 收到 skill 核心指令，按指令调整行为模式
  │
  └── 第二级（按需）：脚本/示例动态读取
        └── LLM 调用 read_file("server/skills/definitions/code-review/scripts/...")
        └── LLM 调用 bash("python server/skills/definitions/code-review/scripts/...")
        └── 不会自动加载，由 LLM 根据任务需求决定是否读取
```

**与旧方案（prompt injection）的关键区别：**

| 维度 | 旧方案（prompt injection） | 新方案（tool-based） |
|------|--------------------------|---------------------|
| **加载时机** | LLM 调用前全量拼接 | LLM 按需主动调用 `skill` 工具 |
| **Token 消耗** | 所有激活 skill 的 prompt 全部占据 context window | 每次被调用的 skill 才加载，其他 skill 仅占 name + description（~50 tokens/skill） |
| **Skill 发现** | 无感知——LLM 被动接受注入 | 模型从 `<available_skills>` 列表中选择 |
| **资源加载** | 不支持（只能注入纯文本 prompt） | 支持——脚本/示例通过现有工具按需读取 |
| **存储结构** | JSON 中的 `prompt` 字段 | 文件夹 + `SKILL.md` + 可选资源 |

#### 3.6.2 流数据块

skill 工具的执行结果通过 `POST /tool-result` 回传，注入 LLM 上下文：

```json
{
  // 结果通过 POST /tool-result 回传，服务端注入 context
  "seq": 42,
  "tool_call_id": "toolu_xxx",
  "tool_name": "skill",
  "turn": 1,
  "message_id": "m1",
  "result": {
    "success": true,
    "output": "## Skill: 代码审查\n\n你正在扮演一位资深代码审查员。在分析代码时，请遵循以下原则：\n1. **安全性优先**...",
    "duration_ms": 2
  }
}
```

#### 3.6.3 QueryLoopEngine 集成

`skill` 工具现在是 **CLIENT 工具**（见 `ToolDispatcher.CLIENT_TOOLS`），不再由服务端执行。LLM 调用 `skill` 工具时，引擎通过 `client.tool_request` 委派给客户端，客户端读取本地 `~/.iwork/skills/{name}/SKILL.md` 并返回结果。

```python
# query_loop.py - _execute_tool_chunk() 中的 skill 处理

async def _execute_tool_chunk(self, msg: Message, chunk: LLMChunk, turn: int):
    location = self.tool_dispatcher.classify(chunk.tool_name)

    if location == ToolLocation.CLIENT:
        # skill 工具在此分支中处理——classify("skill") 返回 CLIENT
        # → 生成 client.tool_request 事件，前端执行后通过 tool_result 端点回传结果
        request_id = str(uuid4())
        await self._push_chunk({
            "type": "client.tool_request",
            "request_id": request_id,
            "tool_name": chunk.tool_name,
            "tool_input": chunk.tool_input,
        })
        result = await self._wait_client_result(request_id)
        # ...
    else:
        # MCP dispatch（服务端工具）
        result = await self.tool_dispatcher.dispatch(self.session.id, chunk)
        # ...
```

**客户端处理 `skill` 工具的逻辑：**

```
客户端收到 client.tool_request(tool_name="skill", tool_input={name: "code-review"})
  → 读取 ~/.iwork/skills/code-review/SKILL.md
  → POST /sessions/{id}/tool-result/{request_id} 回传内容
  → 服务端注入 tool_result 到上下文
```

不再需要服务端的 `_execute_skill_tool()` 方法——该方法已移除。Skill 的核心指令读取完全在客户端完成。

#### 3.6.4 SkillRegistry 新增方法

```python
# skill_registry.py - 新增方法

class SkillRegistry:
    # ... existing fields ...

    def build_available_skills_xml(self) -> str:
        """构建 <available_skills> XML 块，注入 system prompt。"""
        installed = [self._skills[sid] for sid in self._installed
                     if sid in self._skills]
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

    def lookup_by_name(self, skill_name: str) -> SkillDefinition | None:
        """按 skill_name 查找 Skill 定义。"""
        for skill in self._skills.values():
            if skill.skill_name == skill_name and skill.skill_id in self._installed:
                return skill
        return None

    def get_skill_md(self, skill: SkillDefinition) -> str:
        """读取 skill 文件夹中的 SKILL.md 内容。"""
        md_path = (self._definitions_dir / skill.folder_path / "SKILL.md")
        if not md_path.exists():
            raise FileNotFoundError(f"SKILL.md 不存在: {md_path}")
        return md_path.read_text(encoding="utf-8")

    def get_available(self) -> list[SkillDefinition]:
        """返回所有已安装的 Skill（用于 <available_skills> 和错误提示）。"""
        return [self._skills[sid] for sid in self._installed
                if sid in self._skills]


# 内置 skill 工具定义（常量）
SKILL_TOOL_DEFINITION = {
    "name": "skill",
    "description": (
        "Load a specialized skill when the task matches one listed in "
        "<available_skills>. The skill name must match one from the "
        "available skills list."
    ),
    "parameters": {
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
```

#### 3.6.5 客户端显式调用 Skill（预加载）

当用户输入 `/skill名` 时，**客户端先读本地 `~/.iwork/skills/{name}/SKILL.md`**，然后将内容作为 `skill_invocations` 列表通过 `POST /messages` 传给服务端。服务端在 turn 0 的 LLM 调用前，**直接将每个 skill 的 SKILL.md 内容作为 user 消息注入对话历史**，省去 LLM 调用 `skill` 工具的一轮。

```
客户端流程:
  1. 用户输入 "/code-review 请审查代码"
  2. 客户端解析出 skill_name="code-review"
  3. 读取本地文件 ~/.iwork/skills/code-review/SKILL.md
  4. 构建请求:
     POST /messages {
       skill_invocations: [
         { skill_id: "sk1", skill_name: "code-review", skill_md: "## Skill: 代码审查\n..." }
       ],
       content: "请审查代码"
     }

服务端流程 (_run_message_loop):
  1. for inv in skill_invocations:
       append_text("user", inv.skill_md)        ← 每个 SKILL.md 内容作为独立 user 消息
  2. append_text("user", "请审查代码")           ← 用户实际查询
  3. context.build() → llm.stream()
```

**注入后的对话结构：**

```
[system]  你是 iWork，AI 编程助手...如有匹配任务的 Skill 可用，优先调用 skill 工具...
[system]  模式：构建。逐步执行任务...
          （Turn 0 有 skill_invocations 时不注入 <available_skills> 和 skill 工具）
[user]    ## Skill: 代码审查
          你正在扮演一位资深代码审查员。在分析代码时，请遵循以下原则：
          1. **安全性优先**：首先检查 OWASP Top 10 漏洞...
          ...
[user]    请帮我审查这段代码
```

**关键点：**
- **客户端负责读取 SKILL.md**：客户端从本地 `~/.iwork/skills/{name}/SKILL.md` 读取内容，通过 `skill_md` 字段传给服务端
- LLM 直接从 user 消息收到 skill 指令，无需调用 `skill` 工具（`skill` 工具已移除，不再在服务端执行）
- Turn 0 有 `skill_invocations` 时不注入 `<available_skills>` 和 `skill` 工具；Turn 1+ 自动恢复
- 不传 `skill_invocations` 时行为不变——LLM 自己从 `<available_skills>` 中选择
- 如果客户端读取 SKILL.md 失败（文件不存在等），skill_invocations 中对应项的 `skill_md` 为空字符串，服务端跳过该项

```python
# query_loop.py - _run_message_loop() turn 0 前

if msg.skill_invocations:
    for inv in msg.skill_invocations:
        if inv.skill_md:
            await self.context_mgr.append_text(self.session.id, "user", inv.skill_md)
            logger.info("skill.preloaded  skill=%s  id=%s", inv.skill_name, inv.skill_id)
        else:
            logger.warning("skill.preload_empty  skill=%s  id=%s — 客户端未提供 SKILL.md 内容",
                           inv.skill_name, inv.skill_id)

await self.context_mgr.append_text(self.session.id, "user", msg.content)
```

**用户消息中的 workspace + files 上下文：**

同 3.6.5 旧方案，服务端在实际用户消息前附加上下文前缀：

```python
user_content = msg.content
if msg.workspace or msg.files:
    ctx_parts = []
    if msg.workspace:
        ctx_parts.append(f"工作目录: {msg.workspace}")
    if msg.files:
        ctx_parts.append("@文件: " + ", ".join(msg.files))
    user_content = "[上下文] " + "; ".join(ctx_parts) + "\n\n" + user_content

await self.context_mgr.append_text(self.session.id, "user", user_content)
```

LLM 收到的用户消息格式：

```
[上下文] 工作目录: G:\AI-coding\iWork; @文件: src/main.py, src/utils.py

请审查代码
```

#### 3.6.6 system prompt 组装顺序

```
完整的 system prompt 组装顺序:
  1. SYSTEM_PROMPTS[scene_mode]     ← "你是 iWork，AI 编程助手...如有匹配任务的 Skill 可用，优先调用 skill 工具..."
  2. MODE_PROMPTS[mode]             ← "模式：build。当前处于构建阶段..."
  3. <available_skills> XML         ← "\n<available_skills>\n  <skill>\n    <name>code-review</name>..."
```

> 注：客户端显式传入 `skill_invocations` 时，SKILL.md 作为 user 消息注入（见 3.6.5），不在 system prompt 中。

与旧方案不同，Skill 的核心指令（SKILL.md）不再占据 system prompt 空间，只有 `<available_skills>` 中的 `name` + `description`（约 50 tokens/skill）常驻上下文。模型判断需要某个 skill 时才通过 `skill` 工具加载完整指令。

#### 3.6.7 与 MCP 工具执行的对比

```
MCP 工具执行链路（客户端 MCP）:
  LLM → tool_use("github_search_issues")
      → ToolDispatcher.classify() → CLIENT（客户端上报的 MCP 工具）
      → client.tool_request → 前端 MCP 进程执行
      → POST tool-result → 注入上下文
  前端渲染 tool_call → tool_result 状态变化

Skill 工具执行链路（客户端 Skill）:
  LLM → tool_use("skill", {name: "code-review"})
      → ToolDispatcher.classify() → CLIENT（skill 在白名单中）
      → client.tool_request → 前端读取 ~/.iwork/skills/code-review/SKILL.md
      → POST tool-result → 注入上下文
  前端渲染标准 tool_call("skill") → 执行 → 状态更新

Skill 预加载（更快路径，跳过 LLM tool_use 轮次）:
  用户输入 "/code-review 请审查代码"
      → 客户端读 SKILL.md → 放入 skill_invocations[].skill_md
      → POST /messages → 服务端直接注入 user 消息
      → LLM 第一轮就看到 skill 指令，无需调用 skill 工具
```

#### 3.6.8 相邻工具调用的执行与上下文组织

当 LLM 在一次 turn 中连续发起多个工具调用时（例如先 `read_file` 再 `bash`），执行是**严格串行**的，但上下文组织有特殊处理。

##### 流式处理时序

```
LLM stream 产出:
  chunk: text("我先读取文件...")       → 累加入 llm_text
  chunk: thinking("需要看代码...")      → 累加入 llm_reasoning
  chunk: tool_use("read_file", id=call_1)  → 触发工具 #1 执行
  chunk: tool_use("bash", id=call_2)       → 触发工具 #2 执行
  chunk: end_turn                          → turn 终止

引擎处理顺序（串行）:
  1. 遇到 tool_use #1 →
      a. flush: append_text("assistant", llm_text, reasoning=llm_reasoning)
         → 上下文写入: {role: "assistant", content: "我先读取...", reasoning_content: "需要看代码..."}
      b. 清空 llm_text、llm_reasoning
      c. 执行 read_file → 获得 result_1
      d. append_tool_result(call_1, result_1)
         → 找到上一步的 assistant 消息，合并 tool_calls: [{id: call_1, ...}]
         → 追加 tool 消息: {role: "tool", tool_call_id: call_1, content: result_1}

  2. 遇到 tool_use #2 →
      a. llm_text 和 llm_reasoning 已为空 → 不调 append_text
      b. 执行 bash → 获得 result_2
      c. append_tool_result(call_2, result_2)
         → 再次找到同一条 assistant 消息（它仍是最后的 assistant），追加第二个 tool_call
         → 追加 tool 消息: {role: "tool", tool_call_id: call_2, content: result_2}
```

##### 最终上下文消息结构

两条 tool_call **合并到同一条 assistant 消息**中，`reasoning_content` 也在同一消息内：

```json
[
  {"role": "user", "content": "请检查 main.py 的语法错误并运行测试"},
  {
    "role": "assistant",
    "content": "我先读取文件内容，然后运行测试。",
    "reasoning_content": "需要先看代码才能分析语法错误，然后再跑测试验证。",
    "tool_calls": [
      {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": "{\"path\": \"main.py\"}"}
      },
      {
        "id": "call_2",
        "type": "function",
        "function": {"name": "bash", "arguments": "{\"command\": \"python -m pytest\"}"}
      }
    ]
  },
  {"role": "tool", "tool_call_id": "call_1", "content": "{\"success\": true, \"output\": \"def main():...\"}"},
  {"role": "tool", "tool_call_id": "call_2", "content": "{\"success\": true, \"output\": \"2 passed\"}"}
]
```

##### 关键设计点

| 机制 | 说明 |
|------|------|
| **合并原因** | DeepSeek 要求 `reasoning_content` 与 `tool_calls` 在同一消息内，否则 API 报错 |
| **合并方式** | `append_tool_result()` 反向遍历上下文找到最后一条 `role=assistant` 消息，调用 `setdefault("tool_calls", []).append(block)` |
| **text 只刷一次** | 首次 tool_use 出现时把积累的文本+推理刷入上下文，后续 tool_use 不再重复写入 |
| **执行串行** | 工具逐个执行，结果实时注入上下文——不会等所有工具都执行完再批量写 |
| **tool call id** | 每个 tool call 有唯一 `id`，tool result 通过 `tool_call_id` 对应，LLM 据此关联调用和结果 |
| **role: tool** | tool 结果消息使用 `role: "tool"`（OpenAI 兼容格式），每条 tool call 对应一条 tool 结果 |

##### append_tool_result 核心逻辑

```python
async def append_tool_result(self, session_id, tool_call_id, tool_name, tool_input, result):
    ctx = self._contexts[session_id]
    block = {
        "id": tool_call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(tool_input, ensure_ascii=False),
        },
    }
    # 反向查找最后的 assistant 消息，合并 tool_calls
    for m in reversed(ctx):
        if m.get("role") == "assistant":
            m.setdefault("tool_calls", []).append(block)
            break
    else:
        # 没有 assistant 消息时（异常情况），新建一条
        ctx.append({"role": "assistant", "content": None, "tool_calls": [block]})
    # 追加 tool 结果
    ctx.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(result, ensure_ascii=False),
    })
```

### 3.7 错误处理

Skill 的错误分为两类：**启动加载错误**（SkillRegistry 初始化时）和**工具调用错误**（LLM 调用 `skill` 工具时）。不存在重连（无进程/连接）、不存在重试耗尽（不涉及网络）。

#### 3.7.1 错误分类

**启动加载错误**（SkillRegistry 初始化时）：

| # | 场景 | 触发条件 | 处理方式 | LLM 影响 |
|---|------|---------|---------|---------|
| S1 | **Hub JSON 格式错误** | `skill-hub.json` 条目非法 | 记录错误日志，跳过该条目，继续加载其余 Skill | 该 Skill 不在 `<available_skills>` 中 |
| S2 | **SKILL.md 不存在** | Skill 文件夹中缺少 SKILL.md | 加载时记录警告，标记为不可用 | 该 Skill 不在 `<available_skills>` 中 |
| S3 | **SKILL.md 为空或过短**（< 10 字符） | Skill 定义不完整 | 加载时记录警告，标记为不可用 | 该 Skill 不在 `<available_skills>` 中 |
| S4 | **skill_state.json 损坏** | 文件被外部破坏 | 记录错误，回退到空状态（所有 Hub/Custom Skill 视为未安装） | 仅内置 Skill 有效 |
| S5 | **重复 skill_name** | 同名 skill_name 冲突 | 加载时记录错误，后加载的覆盖先加载的 | 后者生效 |
| S6 | **skill-hub.json 文件缺失** | 服务端未配置 Hub | Hub 目录为空，仅 Custom + Builtin 可用 | Hub 标签页为空 |

**工具调用错误**（LLM 调用 `skill` 工具时——客户端执行）：

| # | 场景 | 触发条件 | 处理方式 | LLM 影响 |
|---|------|---------|---------|---------|
| S7 | **skill name 不在已安装列表中** | LLM 传入的 `name` 与客户端本地任何已安装 skill 的 `skill_name` 不匹配 | 客户端返回 `{success: false, error: ...}`，`error` 中列出所有可用 skill 名称 | LLM 看到错误后选择可用 skill 或放弃 |
| S8 | **SKILL.md 本地缺失** | skill 已安装但 `~/.iwork/skills/{name}/SKILL.md` 被外部删除 | 客户端返回 `{success: false, error: "核心文件缺失"}` | LLM 被告知该 skill 不可用 |
| S9 | **SKILL.md 读取失败** | 文件权限不足、编码错误等（客户端本地） | 客户端返回 `{success: false, error: 详情}` | LLM 自行决定替代方案 |

#### 3.7.2 处理策略

**启动加载错误**均为 Level 2（降级）：有问题的 Skill 被静默跳过，不在 `<available_skills>` 中展示。引擎不终止，不推送 `message.error` 或 `system.status`。

**工具调用错误**通过 `POST /tool-result`（`{success: false}`）返回给 LLM，LLM 根据错误信息自行调整策略（选择其他 skill 或继续无 skill 操作）。不需要：
- 重试（SKILL.md 缺失重试无意义）
- `system.status` 通知（标准 tool_result 足够）
- 终止（skill 缺失不是致命错误）

#### 3.7.3 安装态错误

| 场景 | HTTP 状态码 | 说明 |
|------|-----------|------|
| 安装不存在的 Hub Skill | 404 | Hub 中无此 skill_id |
| 重复安装 | 409 | 已安装 |
| 卸载内置 Skill | 403 | 内置 Skill 不可卸载 |
| 卸载未安装的 Skill | 404 | 未安装 |
| Custom Skill validation 失败 | 422 | SKILL.md 为空或 name 非法等 |

### 3.8 Skill 管理（Hub）

#### 3.8.1 Hub 数据来源

Hub 数据以 JSON 配置文件形式存储于服务端 `server/skill-hub.json`，管理员可直接编辑此文件增删条目。客户端通过 API 获取可安装的 Skill 列表。

每条 Hub 条目包含 Skill 元数据和 `folder_path`，客户端用于展示和安装：

```json
{
  "skill_id": "sk1",
  "skill_name": "code-review",
  "description": "以资深代码审查员视角分析代码，关注安全性、性能和可维护性",
  "folder_path": "code-review",
  "icon": "🔍",
  "category": "开发",
  "version": "1.2.0",
  "author": "iWork Team",
  "tags": ["code", "review", "security"]
}
```

> 注意：Hub 列表 API 响应中**不包含 `folder_path` 和 SKILL.md 内容**，仅安装后才可通过 `skill` 工具获取核心指令。

#### 3.8.2 安装 / 卸载流程

安装/卸载状态持久化在 `server/storage/skill_state_{user_id}.json`（服务端记录 installed_ids）。Skill 文件由客户端管理（位于 `~/.iwork/skills/`）。

```
安装:
  用户点击 [安装]
  → POST /skills/install  Body: {skill_id: "sk1"}
  → 校验 skill_id 在 Hub 中存在，且未安装
  → 写入 skill_state_{user_id}.json 的 installed_ids
  → SkillRegistry.install(skill_id)  更新内存注册表
  → 将 skill 文件夹打包为 zip，返回 application/zip 流
  → 客户端收到 zip 后解压到 ~/.iwork/skills/{folder_name}/
  → 重复安装返回 409，不存在的 skill_id 返回 404

卸载:
  用户点击 [已安装]
  → DELETE /skills/uninstall/{skill_id}
  → 从 skill_state.json 的 installed_ids 中移除
  → SkillRegistry.uninstall(skill_id)  更新内存注册表
  → 返回 200: {success: true, uninstalled: skill_id}
  → 客户端删除本地 ~/.iwork/skills/{folder_name}/ 目录
  → 未安装返回 404，内置返回 403
```

> **与旧方案的核心区别：** 旧方案中 Skill 安装是纯服务端 JSON 写入。新方案中：
> - 安装时服务端返回 zip 文件，客户端负责解压到本地 skill 目录
> - 卸载时服务端返回确认，客户端负责删除本地文件
> - `skill` 工具执行从服务端读取 SKILL.md 变为客户端读取本地文件

#### 3.8.3 自定义 Skill

用户可通过两种方式创建自定义 Skill：

1. **上传 Skill 文件夹**：包含 SKILL.md 的压缩包，服务端验证后自动分配 `skill_id = cs{N+1}`，解压到 `server/skills/definitions/`，写入 `custom_skills` 并自动安装
2. **从 UI 表单创建**：弹窗填写 name、description、SKILL.md 内容，提交后服务端创建文件夹并持久化

删除自定义 Skill 时同时从 `custom_skills`、`installed_ids` 中移除，并删除对应文件夹。

#### 3.8.4 与主对话流程的联通

Skill 通过 `<available_skills>` XML + `skill` 工具（客户端执行）与主对话流程打通：

```
ContextManager.build()
  → 注入 <available_skills> XML（所有已安装的 Skill）
  → 注入 skill 工具定义（由客户端注册为 CLIENT 工具）
  → LLM 自行判断并调用 skill(name="xxx")
  → ToolDispatcher.classify("skill") → CLIENT
  → client.tool_request → 客户端读取本地 SKILL.md → POST tool-result
  → tool_result 注入上下文
```

`MessageCreate.skill_invocations` 字段仅用于客户端预加载场景（用户输入 `/skill名`），正常对话中 LLM 通过 `skill` 工具按需加载。

### 3.9 Skill 查询接口定义

所有 Skill 管理接口挂载在 `/skills` 前缀下，由 `server/api/skill_routes.py` 实现。

| 方法 + 路径 | 说明 | 持久化 |
|------------|------|--------|
| `GET /skills/hub` | 浏览 Hub 中所有可安装的 Skill | 读取 `skill-hub.json` |
| `GET /skills/installed` | 查看已安装的 Skill | 读取 `skill_state.json` + Hub |
| `POST /skills/install` | 安装 Hub 中的 Skill | 写入 installed_ids |
| `DELETE /skills/uninstall/{skill_id}` | 卸载已安装的 Skill | 移除 installed_ids |
| `GET /skills/custom` | 查看自定义 Skill 列表 | 读取 custom_skills |
| `POST /skills/custom` | 创建自定义 Skill（自动分配 ID + 自动安装） | 追加 custom_skills + installed_ids |
| `PUT /skills/custom/{skill_id}` | 更新自定义 Skill（部分字段） | 修改 custom_skills 条目 |
| `DELETE /skills/custom/{skill_id}` | 删除自定义 Skill（同步卸载） | 移除 custom_skills + installed_ids |

```typescript
// ═══════════════════════════════════════════
// GET /skills/hub — 浏览 Hub 所有可安装的 Skill
// ═══════════════════════════════════════════

Response 200:
{
  skills: {
    skill_id: string;         // "sk1"
    skill_name: string;       // "code-review"
    description: string;      // "以资深代码审查员视角分析代码..."
    version: string;          // "1.2.0"
    category: string;         // "开发"
    icon: string;             // "🔍"
    author: string;           // "iWork Team"
    tags: string[];           // ["code", "review", "security"]
    // folder_path 和 SKILL.md 内容不返回——Hub 列表不暴露存储路径和核心指令
  }[];
}


// ═══════════════════════════════════════════
// GET /skills/installed — 查看已安装的 Skill
// ═══════════════════════════════════════════

Response 200:
{
  installed: {
    skill_id: string;
    skill_name: string;
    description: string;
    icon: string;
    category: string;
    source: "hub" | "custom" | "builtin";
  }[];
}


// ═══════════════════════════════════════════
// POST /skills/install — 安装 Hub 中的 Skill
// ═══════════════════════════════════════════

Request Body:
{ skill_id: string; }         // 必须存在于 Hub 中

Response 200 (application/zip):
// 返回 skill 文件夹的 zip 包（包含 SKILL.md 及所有资源文件）
// Content-Type: application/zip
// Content-Disposition: attachment; filename="{folder_name}.zip"

// 409 — 已安装
{ detail: "该 skill 已安装"; }

// 404 — Hub 中不存在
{ detail: "skill_id 不在 hub 中"; }


// ═══════════════════════════════════════════
// DELETE /skills/uninstall/{skill_id} — 卸载 Skill
// ═══════════════════════════════════════════

Response 200:
{ success: true; uninstalled: string; }   // uninstalled = skill_id

// 403 — 内置 Skill 不可卸载
{ detail: "内置技能不可卸载: bi1"; }

// 404 — 未安装
{ detail: "未安装该技能: xxx"; }


// ═══════════════════════════════════════════
// GET /skills/custom — 查看自定义 Skill 列表
// ═══════════════════════════════════════════

Response 200:
{
  custom: {
    skill_id: string;          // 自动生成 "cs1", "cs2", ...
    skill_name: string;
    description: string;
    icon: string;              // 默认 "✨"
    category: string;          // 默认 "自定义"
    folder_path: string;
    created_at: string;
    updated_at: string;
  }[];
}


// ═══════════════════════════════════════════
// POST /skills/custom — 创建自定义 Skill
// ═══════════════════════════════════════════

Request Body:
{
  skill_name: string;          // 必填，最大 100 字符
  description?: string;        // 默认 ""
  icon?: string;               // 默认 "✨"
  skill_md: string;            // 必填，SKILL.md 核心指令内容（最小 10 字符）
  category?: string;           // 默认 "自定义"
}
// skill_id 和 folder_path 由服务端自动生成，创建后自动安装

Response 201:
{
  skill_id: "cs2";
  skill_name: string;
  description: string;
  icon: string;
  category: string;
  folder_path: string;
  created_at: string;
}

// 422 — 验证失败
{ detail: "skill_md 不能少于 10 个字符"; }


// ═══════════════════════════════════════════
// PUT /skills/custom/{skill_id} — 更新自定义 Skill
// ═══════════════════════════════════════════

Request Body:
{
  skill_name?: string;
  description?: string;
  icon?: string;
  skill_md?: string;           // 更新 SKILL.md 内容
  category?: string;
}
// 部分更新：仅传入的字段生效

Response 200:
{ updated: true; skill_id: string; }

// 404 — 自定义 Skill 不存在
{ detail: "自定义技能不存在: xxx"; }


// ═══════════════════════════════════════════
// DELETE /skills/custom/{skill_id} — 删除自定义 Skill
// ═══════════════════════════════════════════

Response 200:
{ deleted: true; skill_id: string; }

// 404 — 不存在
{ detail: "自定义技能不存在: xxx"; }
```

### 3.10 与 Query Loop 引擎的集成点总结

Skill 在第 1 章 Query Loop 引擎架构中的注入位置（新方案——客户端执行）：

```
QueryLoopEngine
│
├── EngineManager.get_or_create(session_id, user_id)
│   └── skill_registry = SkillRegistry(hub_path, state_path, definitions_dir)   ← 读取文件夹结构
│       (纯内存加载，同步完成，无异步，无进程管理)
│       (仅管理 Hub 索引 + 安装状态，不负责 skill 执行)
│
├── _run_message_loop()
│   │
│   ├── if msg.skill_invocations:                                ← 客户端预加载的 SKILL.md 内容
│   │     → for inv in skill_invocations:
│   │         if inv.skill_md:                                   ← 客户端已读取本地 SKILL.md
│   │           append_text("user", inv.skill_md)                ← 注入每个 SKILL.md 为独立 user 消息
│   │
│   ├── context_mgr.build()
│   │   ├── system += available_skills_xml                       ← 注入 <available_skills>
│   │   └── tools.extend(all_tools)                              ← skill 工具在 client_tools 中
│   │
│   ├── llm.stream(system=augmented_prompt, tools=[client, mcp])
│   │
│   └── _execute_tool_chunk()
│       ├── ToolDispatcher.classify("skill") → CLIENT             ← skill 在白名单中
│       ├── if CLIENT:                                            ← skill + 其他客户端工具
│       │     → client.tool_request → 前端执行 → tool_result 回传
│       └── elif SERVER:                                          ← 服务端 MCP dispatch
│
├── _push_chunk() → client.tool_request ← 现有逻辑不变
│   (Skill 工具通过 client.tool_request 委派，结果通过 POST /tool-result 回传)
│
└── context_mgr.append_tool_result()                              ← 现有逻辑不变
```

**需要变更的组件：**

| 组件 | 变更内容 | 复杂度 |
|------|---------|--------|
| `SkillRegistry.__init__()` | 接受 `definitions_dir` 参数，新增 `build_available_skills_xml()`、`lookup_by_name()`、`get_skill_md()` 方法 | 高 |
| `ContextManager.build()` | 注入 `<available_skills>` XML；`skill` 工具由客户端提供（在 client_tools 中） | 中 |
| `ToolDispatcher.classify()` | `"skill"` 归类为 CLIENT（在白名单 `CLIENT_TOOLS` 中） | 低 |
| `main.py` lifespan | SkillRegistry 初始化时传入 `definitions_dir` | 低 |
| `server/models/message.py` | `SkillInvocation` 模型新增 `skill_md: str` 字段（客户端预读取的内容） | 低 |
| `server/skill-hub.json` | 去 `prompt`，加 `folder_path` | 低 |
| `server/storage/skill_state.json` | 去 `disabled_ids` | 低 |
| `QueryLoopEngine._run_message_loop()` | skill 预加载逻辑（遍历 `msg.skill_invocations` → `append_text("user", inv.skill_md)`）+ workspace/files 上下文注入 | 低 |

**需要移除的旧组件：**

| 组件 | 移除内容 |
|------|---------|
| `QueryLoopEngine._execute_skill_tool()` | skill 工具不再在服务端执行（客户端读取本地 SKILL.md） |
| `SkillRegistry.get_active_prompts()` | 不再需要 prompt 拼接 |
| `SkillRegistry._disabled` | 不再需要 disabled_ids |
| `QueryLoopEngine._resolve_skill_ids()` | 不再需要会话/消息级 skill 解析 |
| `POST /skills/enable` & `POST /skills/disable` | 不再需要启用/禁用 API |
| `_execute_tool_chunk()` 中 `if tool_name == "skill"` 分支 | skill 走通用 CLIENT 路径，不需要特殊处理 |

**服务启动时机：** Skill 注册表初始化在 `main.py` lifespan 中同步执行（读 JSON 文件）。

**流数据块：** Skill 工具的执行产生 `client.tool_request`，结果通过 `POST /tool-result` 回传。客户端渲染 tool_call → client.tool_request → 执行 → 状态更新。

---

---

<a id="4-数据库与种子数据运维"></a>

