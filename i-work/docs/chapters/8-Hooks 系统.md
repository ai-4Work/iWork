# 8. Hooks 系统

## 目录

- [8.1 架构概览](#81-架构概览)
- [8.2 拦截点](#82-拦截点)
- [8.3 Hook 上下文与返回值](#83-hook-上下文与返回值)
- [8.4 责任链执行模型](#84-责任链执行模型)
- [8.5 配置](#85-配置)
- [8.6 引擎集成](#86-引擎集成)
- [8.7 安全模型](#87-安全模型)
- [8.8 实现路径](#88-实现路径)
- [8.9 常用 Hook 示例](#89-常用-hook-示例)
- [Structlog 日志标签](#structlog-日志标签)

Hooks 是用户自定义的**责任链**，在引擎生命周期关键节点插桩执行。与 EventBus 不同：Hook 有序、可修改数据、engine 等待结果。通知型 hook 只是永远返回 `CONTINUE` 的普通 hook，不特殊对待。

### 8.1 架构概览

```
Engine 生命周期                       Hook Chain
─────────────────────                ──────────────────────────
tool.before ──────────► hook1 ──► hook2 ──► hook3 ──► 拿结果
                         │         │         │
                         ▼         ▼         ▼
                     MODIFY    CONTINUE    STOP
                    (改参数)   (放行)    (拒绝执行)

tool.execute ──► (执行工具)

tool.after ───────────► hook4 ──► hook5 ──► 拿结果
                         │         │
                         ▼         ▼
                     MODIFY    CONTINUE
                    (改结果)   (放行)
```

**与 EventBus 的对比：**

| | EventBus | Hook Chain |
|------|------|------|
| **模式** | 发布-订阅 | 责任链 |
| **方向** | 单向广播，无返回值 | 串行调用，engine 等待结果 |
| **顺序** | 无序，subscriber 独立 | 严格按 `order` 升序执行 |
| **数据修改** | 不可修改 | 前一个 MODIFY → 后一个看到修改后的数据 |
| **终止** | 无法阻止 | 任一 STOP → 整条链终止 |
| **超时/异常** | 静默吞掉 | 记录 warning，视为 CONTINUE |
| **消费者** | StreamSubscriber、AuditSubscriber（内置） | 用户自定义 hook 脚本 |

### 8.2 拦截点

6 个生命周期拦截点，覆盖引擎的关键路径：

| 拦截点 | 触发时机 | `input` 内容 | 典型用途 |
|------|------|------|------|
| `message.before` | 用户消息入队后、开始处理前 | `{content, mode, model, workspace}` | 审查用户输入、注入系统提示 |
| `message.after` | 消息处理完成（含 MESSAGE_COMPLETE emit 前） | `{status, total_turns, tokens, cost, response_text}` | 成本记账、结果通知、AI 回复审查 |
| `llm.before` | 每个 turn 调用 LLM 前 | `{provider, model, messages, tools, thinking_budget}` | 审查/修改 prompt、切换模型 |
| `llm.after` | 每个 turn LLM 返回后 | `{stop_reason, tokens, content, tool_calls}` | Token 统计、响应后处理 |
| `tool.before` | 每个工具执行前 | `{tool_name, args, location}` | 安全审查、阻止危险命令、参数改写 |
| `tool.after` | 每个工具执行后 | `{tool_name, success, result, duration_ms}` | 结果审计、通知、缓存更新 |

### 8.3 Hook 上下文与返回值

所有拦截点的入参使用**统一的外层信封**，`input` 字段内容**因拦截点而异**。

**外层信封（所有拦截点通用）：**

```json
{
  "hook_point": "<拦截点>",
  "session_id": "uuid",
  "user_id": "uuid",
  "message_id": "uuid",
  "turn": 2,
  "input": { /* 随拦截点不同，见下方 */ }
}
```

**各拦截点 `input` 字段详解：**

`message.before`
```json
{
  "input": {
    "content": "用户原始消息文本",
    "mode": "chat",
    "model": "claude-opus-4-7",
    "workspace": "/path/to/project"
  }
}
```

`message.after`
```json
{
  "input": {
    "status": "completed",
    "total_turns": 5,
    "tokens": { "input": 1234, "output": 567 },
    "cost": { "total": 0.042 },
    "response_text": "完整的 AI 回复文本"
  }
}
```

`llm.before`
```json
{
  "input": {
    "provider": "anthropic",
    "model": "claude-opus-4-7",
    "messages": [
      { "role": "user", "content": "..." }
    ],
    "tools": [
      { "name": "bash", "description": "..." }
    ],
    "thinking_budget": 16000
  }
}
```

`llm.after`
```json
{
  "input": {
    "stop_reason": "end_turn",
    "tokens": { "input": 1234, "output": 567 },
    "content": [
      { "type": "text", "text": "..." }
    ],
    "tool_calls": [
      { "name": "bash", "args": { "command": "ls" } }
    ]
  }
}
```

`tool.before`
```json
{
  "input": {
    "tool_name": "bash",
    "args": { "command": "rm -rf /" },
    "location": "server"
  }
}
```

`tool.after`
```json
{
  "input": {
    "tool_name": "bash",
    "success": true,
    "result": "{\"stdout\": \"...\", \"stderr\": \"\"}",
    "duration_ms": 1234
  }
}
```

**返回值（stdout JSON）：**

```typescript
// CONTINUE —— 放行，不修改
{ "action": "CONTINUE" }

// MODIFY —— 放行，但修改 input/output
{ "action": "MODIFY", "input": { ... } }

// STOP —— 阻止，返回原因
{ "action": "STOP", "reason": "禁止执行 rm -rf /" }
```

**退出码：** 非零视为 `STOP`，stderr 作为 `reason`。

### 8.4 责任链执行模型

```python
# server/hooks/chain.py
import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

class HookAction(StrEnum):
    CONTINUE = "CONTINUE"
    MODIFY = "MODIFY"
    STOP = "STOP"

@dataclass
class HookResult:
    action: HookAction
    modified_input: dict[str, Any] | None = None
    reason: str | None = None


class HookManager:
    """按拦截点缓存已排序的 hook 列表，引擎调用 run() 时按序执行。"""

    def __init__(self, hooks_config: list[dict]):
        self._hooks: dict[str, list[HookConfig]] = {}
        for h in sorted(hooks_config, key=lambda x: x["order"]):
            self._hooks.setdefault(h["on"], []).append(HookConfig(**h))

    async def run(self, hook_point: str, input_data: dict, ctx: HookContext) -> HookResult:
        hooks = self._hooks.get(hook_point, [])
        for hook in hooks:
            try:
                result = await asyncio.wait_for(
                    self._execute(hook, ctx, input_data),
                    timeout=hook.timeout_ms / 1000,
                )
            except asyncio.TimeoutError:
                logger.warning("hook_timeout", hook=hook.name, hook_point=hook_point)
                continue  # 超时视为 CONTINUE
            except Exception:
                logger.exception("hook_error", hook=hook.name)
                continue  # 异常视为 CONTINUE

            if result.action == HookAction.STOP:
                logger.info("hook_stopped", hook=hook.name, reason=result.reason)
                return result
            if result.action == HookAction.MODIFY and result.modified_input is not None:
                input_data = result.modified_input  # 传递给下一个 hook

        return HookResult(action=HookAction.CONTINUE, modified_input=input_data)

    async def _execute(self, hook, ctx, input_data) -> HookResult:
        hook_input = {
            "hook_point": hook.on, "session_id": str(ctx.session_id),
            "user_id": str(ctx.user_id), "message_id": str(ctx.message_id),
            "turn": ctx.turn, "input": input_data,
        }
        proc = await asyncio.create_subprocess_exec(
            hook.script, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(json.dumps(hook_input).encode())

        if proc.returncode != 0:
            return HookResult(action=HookAction.STOP,
                              reason=stderr.decode()[:200])

        return HookResult(**json.loads(stdout))
```

**关键行为：**

| 场景 | 行为 |
|------|------|
| Hook 返回 `CONTINUE` | 传递原始 input 给下一个 hook |
| Hook 返回 `MODIFY` | 修改后的 input 传递给下一个 hook |
| Hook 返回 `STOP` | 整条链终止，engine 跳过后续动作 |
| Hook 超时 | 记录 warning，视为 `CONTINUE`，不阻塞 |
| Hook 异常/崩溃 | 记录 error，视为 `CONTINUE` |

### 8.5 配置

`hooks.json` 或 config 中的 hooks 段：

```json
{
  "hooks": [
    {
      "name": "security-check",
      "description": "阻止危险的 shell 命令",
      "on": "tool.before",
      "script": "./hooks/deny-rm.sh",
      "order": 10,
      "timeout_ms": 5000,
      "enabled": true
    },
    {
      "name": "cost-tracker",
      "description": "每次 LLM 调用后记录成本",
      "on": "llm.after",
      "script": "python ./hooks/cost-tracker.py",
      "order": 20,
      "timeout_ms": 10000,
      "enabled": true
    },
    {
      "name": "teams-notify",
      "description": "消息完成后通知 Teams",
      "on": "message.after",
      "script": "./hooks/teams-webhook.sh",
      "order": 30,
      "timeout_ms": 15000,
      "enabled": false
    }
  ]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `hook_id` | string | 唯一标识 |
| `name` | string | 显示名称，日志用 |
| `description` | string | 可读说明 |
| `on` | string | 拦截点：`message.before/after`、`llm.before/after`、`tool.before/after` |
| `script` | string | 可执行脚本路径，相对于 workspace 根目录 |
| `order` | int | 执行顺序，升序。同拦截点内数值小的先执行 |
| `timeout_ms` | int | 超时毫秒数，超时视为 CONTINUE |
| `enabled` | bool | 是否启用，可动态关闭而不删除配置 |

### 8.6 引擎集成

```python
# server/engine/query_loop.py
from hooks.chain import HookManager

class QueryLoopEngine:
    def __init__(self, ..., hooks_config: list[dict]):
        self.hooks = HookManager(hooks_config)

    async def _execute_tool(self, tool_call, ctx: HookContext):
        # ── before hooks ──
        before_input = {
            "tool_name": tool_call.name,
            "args": tool_call.args,
            "location": tool_call.location,
        }
        result = await self.hooks.run("tool.before", before_input, ctx)
        if result.action == HookAction.STOP:
            raise ToolBlockedError(tool_call.name, result.reason)
        if result.modified_input:
            tool_call.args = result.modified_input.get("args", tool_call.args)

        # ── 实际执行 ──
        exec_result = await self._do_execute(tool_call)

        # ── after hooks ──
        after_input = {
            "tool_name": tool_call.name,
            "success": exec_result.ok,
            "result": exec_result.output,
            "duration_ms": exec_result.duration_ms,
        }
        await self.hooks.run("tool.after", after_input, ctx)
        # after hook 的 STOP 不影响已完成的执行，仅记录日志

        return exec_result

    async def _call_llm(self, messages, ctx: HookContext):
        # ── before hooks ──
        llm_input = {
            "provider": settings.llm_provider, "model": ctx.model,
            "messages": messages, "tools": ctx.tools,
            "thinking_budget": ctx.thinking_budget,
        }
        result = await self.hooks.run("llm.before", llm_input, ctx)
        if result.action == HookAction.STOP:
            raise LLMBlockedError(result.reason)
        if result.modified_input:
            messages = result.modified_input.get("messages", messages)

        # ── 实际调用 ──
        response = await self._provider.chat(messages, ...)

        # ── after hooks ──
        await self.hooks.run("llm.after", {
            "stop_reason": response.stop_reason,
            "tokens": response.usage,
            "content": response.content,
            "tool_calls": response.tool_calls,
        }, ctx)

        return response
```

### 8.7 安全模型

| 措施 | 说明 |
|------|------|
| **脚本白名单目录** | 配置项 `hooks.script_dir`（默认 `./hooks/`），`script` 路径必须在此目录下。禁止 `../` 越狱 |
| **超时强制 kill** | `asyncio.wait_for` 超时后强制终止子进程，不占用引擎资源 |
| **不传敏感环境变量** | Hook 子进程不继承 `API_KEY`、`DB_PASSWORD` 等敏感变量 |
| **仅传必要上下文** | stdin JSON 不含 `api_key`、数据库凭据、其他用户数据 |
| **工作目录隔离** | Hook 进程 cwd 设在 `./hooks/`，不暴露项目根目录文件 |
| **失败不阻塞** | 超时/异常/崩溃均视为 CONTINUE，hook 绝不能成为引擎的故障点 |

### 8.8 实现路径

```
server/
├── hooks/
│   ├── __init__.py
│   ├── chain.py              # HookManager + HookConfig + HookResult
│   └── config.py             # 加载 hooks.json
│
├── engine/
│   └── query_loop.py         # 在 tool/llm/message 关键节点调用 self.hooks.run()
│
├── config.py                 # 新增 hooks_enabled, hooks_script_dir 配置项
└── hooks.json                # 用户 hook 配置文件（示例）
```

| 步骤 | 内容 |
|------|------|
| 1 | 创建 `hooks/chain.py`：`HookManager`、`HookConfig`、`HookResult` |
| 2 | 创建 `hooks/config.py`：加载 `hooks.json`，校验配置 |
| 3 | 在 `query_loop.py` 的 `_execute_tool`、`_call_llm`、`_run_message_loop` 插入 `await self.hooks.run(...)` 调用 |
| 4 | `HOOK_DENIED` 异常类型 + 错误诊断卡片 `hook_denied`（联动 7.6.3 错误码清单） |
| 5 | 示例 hook 脚本：`hooks/deny-rm.sh`、`hooks/cost-tracker.py` |

### 8.9 常用 Hook 示例

#### 8.9.1 `tool.before` — 拦截危险命令

```bash
#!/bin/bash
# hooks/deny-rm.sh
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.input.tool_name')
CMD=$(echo "$INPUT" | jq -r '.input.args.command // ""')

BLOCKED=("rm -rf /" "mkfs." "dd if=" "> /dev/sda" ":(){ :|:& };:")

if [ "$TOOL" = "bash" ]; then
  for pattern in "${BLOCKED[@]}"; do
    if [[ "$CMD" == *"$pattern"* ]]; then
      echo "{\"action\":\"STOP\",\"reason\":\"禁止执行危险命令: $CMD\"}"
      exit 0
    fi
  done
fi
echo '{"action":"CONTINUE"}'
```

#### 8.9.2 `tool.before` —  限制工作空间外的文件访问

```bash
#!/bin/bash
# hooks/workspace-guard.sh
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.input.tool_name')
FILE=$(echo "$INPUT" | jq -r '.input.args.file_path // ""')
WS="/safe/workspace"

if [ "$TOOL" = "read_file" ] || [ "$TOOL" = "edit_file" ]; then
  if [[ "$FILE" != "$WS"* ]]; then
    echo "{\"action\":\"STOP\",\"reason\":\"禁止访问工作空间外的文件: $FILE\"}"
    exit 0
  fi
fi
echo '{"action":"CONTINUE"}'
```

#### 8.9.3 `tool.after` — 审计文件修改

```python
#!/usr/bin/env python3
# hooks/file-audit.py
import json, sys, os
from datetime import datetime

data = json.load(sys.stdin)
input_data = data["input"]

if input_data["tool_name"] in ("write_file", "edit_file") and input_data["success"]:
    with open("/var/log/iwork/file-audit.log", "a") as f:
        f.write(json.dumps({
            "session_id": data["session_id"],
            "timestamp": datetime.now().isoformat(),
            "tool": input_data["tool_name"],
            "duration_ms": input_data["duration_ms"],
        }) + "\n")

print(json.dumps({"action": "CONTINUE"}))
```

#### 8.9.4 `llm.before` — 注入项目上下文

```bash
#!/bin/bash
# hooks/context-inject.sh
INPUT=$(cat)
MODE=$(echo "$INPUT" | jq -r '.input.mode')

# 仅在 coding 模式下注入项目规范
if [ "$MODE" = "coding" ]; then
  PROJECT_RULES=$(cat /workspace/.claude/rules.md 2>/dev/null || echo "")
  if [ -n "$PROJECT_RULES" ]; then
    MODIFIED=$(echo "$INPUT" | jq --arg rules "$PROJECT_RULES" \
      '.input.messages[-1].content += "\n\n项目规范:\n" + $rules')
    echo "{\"action\":\"MODIFY\",\"input\":$MODIFIED}"
    exit 0
  fi
fi
echo '{"action":"CONTINUE"}'
```

#### 8.9.5 `llm.after` — Token 用量记录

```python
#!/usr/bin/env python3
# hooks/cost-tracker.py
import json, sys

data = json.load(sys.stdin)
tokens = data["input"]["tokens"]
total = tokens.get("input", 0) + tokens.get("output", 0)

# 写入本地统计文件
with open("/var/log/iwork/token-usage.log", "a") as f:
    f.write(json.dumps({
        "session_id": data["session_id"],
        "turn": data["turn"],
        "stop_reason": data["input"]["stop_reason"],
        "tokens": tokens,
        "total": total,
    }) + "\n")

print(json.dumps({"action": "CONTINUE"}))
```

#### 8.9.6 `message.before` — 敏感信息脱敏

```bash
#!/bin/bash
# hooks/pii-detect.sh
INPUT=$(cat)
CONTENT=$(echo "$INPUT" | jq -r '.input.content')

# 检测密钥/AK/SK 模式
if echo "$CONTENT" | grep -qE '(sk-[A-Za-z0-9]{32,}|AK[A-Z]{2}[0-9]{16}|[A-Za-z0-9+/]{40,})' 2>/dev/null; then
  echo '{"action":"STOP","reason":"输入中包含疑似 API 密钥或敏感凭证，已阻止"}'
  exit 0
fi
echo '{"action":"CONTINUE"}'
```

#### 8.9.7 `message.after` — Webhook 通知

```bash
#!/bin/bash
# hooks/teams-notify.sh
INPUT=$(cat)
STATUS=$(echo "$INPUT" | jq -r '.input.status')
COST=$(echo "$INPUT" | jq -r '.input.cost.total')
TOKENS=$(echo "$INPUT" | jq -r '.input.tokens')

if [ "$STATUS" = "completed" ]; then
  curl -s -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"消息处理完成 | 费用: \$${COST} | Tokens: ${TOKENS}\"}" \
    > /dev/null 2>&1
fi
echo '{"action":"CONTINUE"}'
```

#### 8.9.8 `llm.before` — 强制 Think 模式

```python
#!/usr/bin/env python3
# hooks/enforce-thinking.py
import json, sys

data = json.load(sys.stdin)
messages = data["input"]["messages"]

# 检查最后一条 user 消息是否要求深度思考
last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
if last_user and "think" in last_user.get("content", "").lower():
    modified = dict(data["input"])
    modified["thinking_budget"] = 32000  # 提升 thinking budget
    print(json.dumps({"action": "MODIFY", "input": modified}))
else:
    print(json.dumps({"action": "CONTINUE"}))



### Structlog 日志标签

`server/engine/query_loop.py` 中使用的 structlog event 标签：

| 标签 | 级别 | 用途 |
|------|------|------|
| `message_started` | info | 消息开始处理 |
| `message_completed` | info | 消息处理完成 |
| `message_error` | exception | 消息处理异常 |
| `message_blocked_by_hook` | warning | 消息被 Hook 拦截 |
| `message_timeout` | warning | 消息执行超时 |
| `context_built` | info | 上下文构建完成 |
| `assistant_response` | info | LLM 文本/thinking 回复 |
| `tool_call` | info | 工具调用开始 |
| `tool_executed` | info | 工具执行完成（含 result 摘要） |
| `tool_parse_error` | warning | 工具参数 JSON 解析失败 |
| `tool_permission_denied` | warning | 工具权限被拒 |
| `tool_blocked_by_hook` | warning | 工具被 Hook 拦截 |
| `tool_mcp_timeout` | warning | MCP 工具执行超时 |
| `hook_tool_before_error` | exception | Hook 执行异常 |
| `llm_turn` | info | LLM turn 汇总（tokens、耗时） |
| `content_filter` | warning | 内容被安全策略拦截 |
| `plan_text_question_detected` | warning | Plan 模式违规直接提问 |
| `loop_detected` | warning | 连续 3 次相同工具调用检测 |
| `hooks_loaded` | info | Hook 配置加载完成（模块级 logger） |

```

<a id="9-多-agent-协作"></a>

