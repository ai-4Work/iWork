# 2. MCP 工具集成

## 目录

- [2.1 架构概览](#21-架构概览)
- [2.2 MCP 协议基础](#22-mcp-协议基础)
- [2.3 MCP 服务生命周期](#23-mcp-服务生命周期)
- [2.4 MCP 服务配置](#24-mcp-服务配置)
- [2.5 工具发现与注册](#25-工具发现与注册)
- [2.6 MCP 工具执行流程](#26-mcp-工具执行流程)
- [2.7 错误处理](#27-错误处理)
- [2.8 MCP 服务管理（Hub）](#28-mcp-服务管理hub)
- [2.9 MCP 查询接口定义](#29-mcp-查询接口定义)
- [2.10 与 Query Loop 引擎的集成点总结](#210-与-query-loop-引擎的集成点总结)

### 2.1 架构概览

MCP (Model Context Protocol) 是 iWork 扩展 AI 能力的核心机制。通过 MCP 协议，iWork 服务端连接外部工具提供者（GitHub、Slack、PostgreSQL 等），将外部工具无缝注册到 LLM 上下文，使 AI 能在对话中调用它们。

与客户端工具（Client Tools）不同，MCP 工具在**服务端执行**，对前端完全透明——前端只需渲染 `tool_call` / `tool_result` 的状态变化，无需参与执行链。

```
┌── Electron Client ──┐     ┌── FastAPI Server ───────────────────────────────┐
│                      │     │                                                  │
│  MCP Hub UI          │     │  ┌── MCPServerManager ───────────────────────┐  │
│  (配置面板，           │     │  │                                           │  │
│   浏览/安装/启停)      │     │  │  ┌────────┐  ┌────────┐  ┌──────────┐  │  │
│                      │◄───►│  │  │GitHub  │  │ Postgre│  │  Slack   │  │  │
│  用户消息 →           │ API │  │  │  MCP   │  │  MCP   │  │   MCP    │  │  │
│  POST /messages      │─────►│  │  │(stdio) │  │(stdio) │  │(HTTP)    │  │  │
│                      │      │  │  └────────┘  └────────┘  └──────────┘  │  │
│                      │      │  └─────────────────────────────────────────┘  │
│                      │      │           │                           ▲         │
│                      │      │           │ tools/list +              │         │
│                      │      │           │ tools/call                │         │
│                      │      │           ▼                           │         │
│                      │      │  ┌── MCPToolRegistry ─────────────────────┐   │
│                      │      │  │  合并所有 MCP 工具 → LLM 可用工具列表    │   │
│                      │      │  └────────────────────────────────────────┘   │
│                      │      │           │                                    │
│                      │      │           ▼                                    │
│                      │      │  ┌── QueryLoopEngine ──────────────────────┐  │
│                      │      │  │  context.build() → 合并全部工具          │  │
│                      │      │  │  → llm.stream(tools=[全部工具])          │  │
│                      │      │  │  → tool_dispatcher.dispatch()           │  │
│                      │      │  └─────────────────────────────────────────┘  │
└──────────────────────┘     └──────────────────────────────────────────────────┘
```

**三层架构：**

| 层 | 组件 | 职责 |
|---|---|---|
| **配置层** | `mcp_servers.yaml` + `Settings.mcp_*` | 存储服务连接定义、凭据、启用状态，用户级隔离 |
| **运行时层** | `MCPServerManager` | 管理进程/连接生命周期：启动 → 初始化 → 心跳 → 重连 → 关闭 |
| **注册层** | `MCPToolRegistry` | 从所有已连接 MCP 服务收集工具定义，合并为 LLM 可用工具列表 |

### 2.2 MCP 协议基础

iWork 的 MCP 实现基于 **JSON-RPC 2.0** 协议，支持三种传输方式。

```
MCP 通信模型：

iWork Server                          MCP Server (外部进程/服务)
       │                                        │
       │──── initialize ───────────────────────→│  握手阶段
       │←─── {protocolVersion, capabilities} ──│
       │──── initialized ──────────────────────→│
       │                                        │
       │──── tools/list ───────────────────────→│  发现阶段
       │←─── [{name, description, inputSchema}] │
       │                                        │
       │──── tools/call ───────────────────────→│  执行阶段
       │←─── {content: [...], isError: false} ──│
       │                                        │
       │──── shutdown ─────────────────────────→│  关闭阶段
```

**三种传输方式对比：**

| 传输方式 | 传输层 | 适用场景 | 配置字段 |
|---------|--------|---------|---------|
| **stdio** | 子进程 stdin/stdout | 本地 MCP 服务（npx/uvx 启动） | `command` + `args` + `env` |
| **SSE** | HTTP Server-Sent Events | 远程 MCP 服务（兼容） | `url` + `headers` |
| **Streamable HTTP** | HTTP POST + NDJSON | 远程 MCP 服务（推荐，与项目架构一致） | `url` + `headers` |

> **iWork 选择：** 优先支持 stdio（npm 生态兼容最好）和 Streamable HTTP（与项目已有 NDJSON 架构一致）。SSE 作为兼容选项保留。

**传输选择决策：**

```
mcp_servers.yaml 中的 transport 决定连接方式：

  transport: stdio
    → 服务端 spawn 子进程，通过 stdin/stdout 交换 JSON-RPC
    → 适用: npx/pipx/uvx 启动的本地 MCP 服务
    → 配置: command, args, env

  transport: streamable-http
    → 服务端通过 HTTP POST 向远端发送 JSON-RPC 请求
    → 适用: 远程 MCP 服务（公司内部 API MCP）
    → 配置: url, headers
```

**三种传输的 connect / request / disconnect 操作对比：**

```
┌─ StdioTransport ─────────────────────────────────────────────────────┐
│                                                                      │
│  connect()                                                           │
│    asyncio.create_subprocess_exec(cmd, args, stdin=PIPE, stdout=PIPE)│
│    → fork 子进程，拿到 stdin StreamWriter + stdout StreamReader       │
│                                                                      │
│  request(id=N)                                                       │
│    self._request_id += 1                                             │
│    stdin.write('{"jsonrpc":"2.0","id":N,"method":"...","params":{}}  │
│               '\n')                                                  │
│    await stdin.drain()                                               │
│    line = await stdout.readline()     ← 阻塞等待一行 JSON             │
│    return json.loads(line)["result"]                                  │
│                                                                      │
│  disconnect()                                                        │
│    transport.request("shutdown", {})  ← 优雅关闭                      │
│    stdin.close()                                                     │
│    await process.wait(timeout=5)      ← 超时则 kill()                │
│                                                                      │
│  特点: 一问一答，锁保护，进程崩溃直接体现为 ConnectionError             │
└──────────────────────────────────────────────────────────────────────┘

┌─ HttpTransport (streamable-http) ────────────────────────────────────┐
│                                                                      │
│  connect()                                                           │
│    self._client = httpx.AsyncClient(timeout=30, verify=...)          │
│    设置 Accept: application/json, text/event-stream                  │
│    → 无实际网络请求，仅创建 HTTP 客户端                                │
│                                                                      │
│  request(id=N)                                                       │
│    self._request_id += 1                                             │
│    resp = await client.post(url, json={"jsonrpc":"2.0","id":N,...},  │
│                              headers=...)                            │
│    resp.raise_for_status()                                           │
│    data = resp.json()                 ← 直接解析 JSON body            │
│    if "error" in data: raise MCPError                                │
│    return data["result"]                                              │
│                                                                      │
│  disconnect()                                                        │
│    await self._client.aclose()        ← 关闭 HTTP 连接池              │
│                                                                      │
│  特点: 每次 request 是一次完整 HTTP POST，无长连接状态，响应路径唯一    │
└──────────────────────────────────────────────────────────────────────┘

┌─ SseTransport (sse) ─────────────────────────────────────────────────┐
│                                                                      │
│  connect()                                                           │
│    ① GET /mcp (Accept: text/event-stream, stream=True)               │
│    ② 启动后台 asyncio.Task: _read_sse() 持续消费 SSE 流              │
│    ③ await _endpoint_ready.wait()   ← 等待 endpoint 事件到达         │
│       SSE 流中解析:                                                   │
│         event: endpoint                                              │
│         data: /mcp/session/abc123                                    │
│       → self._endpoint_url = urljoin(base, "/mcp/session/abc123")    │
│    ④ 超时或流错误 → disconnect → raise ConnectionError               │
│                                                                      │
│  request(id=N)                                                       │
│    ① POST endpoint_url {"jsonrpc":"2.0","id":N,...}                  │
│    ② 检查 Content-Type:                                              │
│       ├─ application/json → resp.json() 直接返回                      │
│       ├─ text/event-stream → 从 POST body 提取 SSE data              │
│       └─ 202 Accepted / 空 body → 创建 Future 放入 _pending[id]      │
│           等待后台 _read_sse() 从 GET SSE 流中匹配 id 后 resolve      │
│           超时 → raise ConnectionError                                │
│                                                                      │
│  disconnect()                                                        │
│    ① 取消所有 _pending Futures（set_exception）                       │
│    ② _read_task.cancel()              ← 取消后台 SSE 读取任务         │
│    ③ await _sse_response.aclose()     ← 关闭 GET SSE 响应流          │
│    ④ await _client.aclose()           ← 关闭 httpx 客户端             │
│                                                                      │
│  特点: 双路异步响应，                                          │
│        _read_sse() 是唯一 aiter_lines() 消费者（避免重复消费）        │
└──────────────────────────────────────────────────────────────────────┘
```

**SSE 通信流程详解：**

在 MCP 协议的 SSE 通信中，**客户端建立 SSE 长连接**，是通过发送一个**标准的 HTTP GET 请求**来发起的。这个请求和普通网页请求没什么两样，关键在于客户端会设置特定的请求头（Headers），并告知服务端它希望接收事件流（`text/event-stream`）。

**1. 请求的构建**

客户端会构建一个 HTTP GET 请求，目标是指定的 SSE 端点（例如 `http://your-server.com/sse`）。请求头中会包含关键的信息：

- **`Accept: text/event-stream`**：这是**最核心的请求头**，它明确告诉服务端："我只接收服务器发送的事件流，请保持连接不要关闭。"
- **`Cache-Control: no-cache`**：指示中间节点（如代理服务器）不要缓存此响应，因为内容是实时流式的。
- **`Connection: keep-alive`**：提示服务端保持 TCP 连接打开，以便后续数据推送。

**2. 连接的生命周期**

当服务端收到这个请求并同意后，它会返回一个 **HTTP 200 OK** 响应，并设置响应头 `Content-Type: text/event-stream`。此后，这个 HTTP 连接会**一直保持打开状态**，直到以下情况发生才关闭：

- 客户端主动断开（如关闭应用程序）
- 服务端主动关闭连接（如发生错误或维护）
- 网络超时或中断

**3. 在 MCP 协议中的具体流程**

在 MCP 的 SSE 方案中，这个 GET 请求只是建立"下行通道"（服务端→客户端），它通常与另一个"消息端点"的 POST 请求配合使用：

```
客户端 (SSE Client)                          服务端 (MCP SSE Server)
   │                                              │
   │ ① GET /sse                                  │
   │    Accept: text/event-stream  ─────────────→│  建立 SSE 长连接（下行通道）
   │                                              │  返回 200 OK
   │                                              │  Content-Type: text/event-stream
   │ ② event: endpoint  ←───────────────────────│  立即推送消息端点 URL
   │    data: /message?session_id=xxx            │
   │                                              │
   │ ③ POST /message?session_id=xxx ────────────→│  上行通道（普通 HTTP POST）
   │    {method: "initialize", ...}              │  发送初始化信息
   │                                              │  服务端处理请求...
   │ ④ event: message  ←────────────────────────│  结果经 SSE 长连接推回
   │    {jsonrpc: "2.0", id: 1, result: {...}}   │
```

1. **客户端发起 SSE GET 请求**到 `/sse` 端点，建立长连接。
2. **服务端通过该连接，立即发送一个 `endpoint` 事件**，其中包含专用的消息端点 URL（例如 `/message?session_id=xxx`）。
3. **客户端记录这个 URL**，并通过**另一个独立的 HTTP POST 请求**向 `/message` 端点发送初始化信息（如 `initialize` 请求），告知服务端自己的能力和版本。
4. 服务端处理该 POST 请求，并将响应结果再通过第一步建立的 SSE 长连接推送给客户端。

**4. SDK 用法说明**

在代码层面，这个 SSE GET 请求通常不是由你手动编写底层 HTTP 库来发送的，而是通过 MCP 官方 SDK（如 Python 的 `mcp.client.sse` 模块）中的 `sse_client` 这类高级函数自动完成的。你只需要提供服务端的 SSE 端点 URL 即可：

```python
from mcp.client.sse import sse_client

async with sse_client("http://your-server.com/sse") as (read_stream, write_stream):
    # SDK 内部已经完成了上述所有 GET 请求、建立连接、获取 endpoint 等操作
    # 你直接使用 read_stream 和 write_stream 进行通信即可
    session = ClientSession(read_stream, write_stream)
    await session.initialize()
    # ...
```

**5. 完整可运行示例**

下面用 Python 代码模拟 SSE 通信流程的**客户端**和**服务端**，帮助你直观理解。

**服务端（模拟 MCP SSE 服务器）** — 使用 FastAPI + sse-starlette 实现：

```python
# server.py
import asyncio
import json
import uuid
import uvicorn
from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

# 存储客户端的消息队列，用于向特定客户端推送
clients: dict[str, asyncio.Queue] = {}

@app.get("/sse")
async def sse_endpoint(request: Request):
    """客户端建立 SSE 长连接的端点。"""
    client_id = str(uuid.uuid4())
    # 为每个客户端创建一个消息队列
    queue = asyncio.Queue()
    clients[client_id] = queue

    async def event_generator():
        try:
            # 立即推送 endpoint 事件，告知客户端消息端点地址
            yield {"event": "endpoint", "data": f"/message?session_id={client_id}"}
            # 持续监听队列，将服务端响应推送给客户端
            while True:
                if await request.is_disconnected():
                    break
                message = await queue.get()
                yield {"event": "message", "data": json.dumps(message)}
        except asyncio.CancelledError:
            pass
        finally:
            clients.pop(client_id, None)

    return EventSourceResponse(event_generator())

@app.post("/message")
async def message_endpoint(request: Request):
    """客户端发送 JSON-RPC 请求的消息端点。"""
    body = await request.json()
    session_id = request.query_params.get("session_id")
    queue = clients.get(session_id)
    if queue is None:
        return {"status": "error", "message": "session not found"}

    method = body.get("method")
    if method == "initialize":
        result = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "demo-mcp", "version": "0.1.0"},
            },
        }
    elif method == "tools/call":
        result = {
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "result": {
                "content": [{"type": "text", "text": "OCR 识别结果：文件已处理"}]
            },
        }
    else:
        result = {"jsonrpc": "2.0", "id": body.get("id"), "result": {}}

    # 通过 SSE 长连接推送响应
    await queue.put(result)
    return {"status": "accepted"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```


**关键点总结：**

| 步骤 | 动作 | 通信方式 |
| :--- | :--- | :--- |
| 1 | 客户端 → 服务端：建立 SSE 长连接，服务端经该连接返回 `endpoint` 事件（含消息端点 URL） | `GET /sse`（HTTP）→ `200 OK` + `Content-Type: text/event-stream`，长连接保持 |
| 2 | 客户端 → 服务端：发送指令（如 `initialize`） | `POST /message`（HTTP） |
| 3 | 服务端 → 客户端：推送响应或通知 | SSE 长连接 |

这就是 **SSE 双端点** 通信的完整流程：**下行用 SSE 长连接，上行用普通 HTTP POST**。你可以在此基础上扩展，模拟工具调用、进度通知等场景。

**三者核心差异：**

| 维度 | Stdio | streamable-http | SSE |
|------|-------|-----------------|-----|
| **连接建立** | fork 子进程（~2-5s） | 创建 HTTP client（瞬态） | GET SSE + 解析 endpoint |
| **request 响应路径** | 1 条：stdout 逐行读 | 1 条：POST body JSON | 2 条：POST body **或** GET SSE 流 |
| **并发模型** | 锁 + 一问一答 | 锁 + HTTP 同步 | 锁 + Future + 后台 Task |
| **断开方式** | `shutdown` → `stdin.close()` → `kill()` | `client.aclose()` | cancel Task → close SSE → close client |
| **断线感知** | 进程退出 (`returncode`) | HTTP 异常 | SSE 流关闭 / Task 异常 |
| **额外复杂度** | — | — | Event(端点同步) + Future(跨路径匹配) + 单 reader 约束 |

### 2.3 MCP 服务生命周期

MCP 服务进程由**客户端**管理，服务端仅维护安装状态和工具清单。

```
┌── Electron Client ──────────────────────────┐
│                                              │
│  POST /mcp/install → 获取配置                 │
│  spawn npx ... / HTTP connect                │
│  tools/list → 获取工具列表                    │
│  POST /mcp/tools → 上报        │
│                                              │
│  运行时: LLM 调用 MCP 工具                    │
│  client.tool_request → client 转发到 MCP      │
│  POST /tool-result → 回传结果                │
│                                              │
│  卸载: DELETE /mcp/uninstall → 服务端登记     │
│  kill 子进程 / 断开 HTTP → 上报 [] 工具列表    │
│                                              │
└──────────────────────────────────────────────┘

┌── FastAPI Server ───────────────────────────┐
│                                              │
│  mcp_state.json ← 仅记录 installed_ids       │
│  mcp-hub.json  ← Catalog（目录）              │
│                                              │
│  user_mcp_servers.tools ← 客户端上报的清单（按 user 持久化）│
│                                              │
│  服务端不再 spawn 任何 MCP 子进程              │
│                                              │
└──────────────────────────────────────────────┘
```

**客户端 MCP 连接状态机：**

```
                 ┌──────────────────────────┐
                 │      CLIENT SIDE          │
 ┌──────────┐    │ ┌──────────┐   ┌────────┐ │
 │DISCONNECT│───►│ │CONNECTING│──►│ READY  │ │
 │   ED     │    │ └──┬───────┘   └───┬────┘ │
 └────┬─────┘    │    │               │      │
      │          │    │ 连接/握手失败   │ 工具 │
      │ 重连     │    │ 超时或错误     │ 调用 │
      │          │    ▼               ▼      │
      │          │ ┌──────┐    ┌──────────┐  │
      │          │ │ERROR │    │TOOL_CALL │  │
      │          │ └──────┘    └──────────┘  │
      └──────────┴──────────────────────────┘
```

| 状态 | 说明 |
|------|------|
| **DISCONNECTED** | 未连接，客户端未启动 MCP 进程 |
| **CONNECTING** | 客户端正在 spawn 子进程或建立 HTTP 连接 |
| **READY** | 工具列表已获取，已上报服务端，可接收工具调用 |
| **ERROR** | 连接失败，等待重连（指数退避） |
| **TOOL_CALL** | 正在执行工具调用（阻塞等待 MCP 响应） |

**关键变化（相比旧架构）：**
- 服务端的 `initialize` / `tools/list` / `tools/call` 全部由客户端执行
- 服务端 `mcp_state.json` 只存 `installed_ids` 和 `custom_servers`，不再管理连接状态
- 工具清单通过 `POST /mcp/tools` 上报到 `user_mcp_servers.tools`，按 user 持久化（跨 session 共享），卸载时上报空列表 `[]`。`user_id` 由客户端在请求 body 中显式传入，路径不再携带 `session_id`

```python
async def _schedule_reconnect(self, server_def: MCPServerDefinition):
    """连接失败后指数退避重连。"""
    attempts = self._retry_counts.get(server_def.id, 0)

    if attempts >= settings.mcp_reconnect_max_retries:
        logger.error(f"MCP server {server_def.id}: max retries exceeded")
        self._set_status(server_def.id, "DISCONNECTED")
        return

    delay = settings.mcp_reconnect_backoff_base_seconds * (2 ** attempts)
    self._retry_counts[server_def.id] = attempts + 1

    await asyncio.sleep(delay)
    await self.connect_server(server_def)
```

### 2.4 MCP 服务配置

#### 2.4.1 配置文件格式

MCP 服务定义存储在 `mcp_servers.yaml`（路径由 `config.py` 的 `mcp_config_file` 指定），每个用户独立一份。

```yaml
# iWork MCP 服务定义（按用户隔离）
version: 1
servers:
  # ── stdio 传输 ──
  - id: "github"
    name: "GitHub MCP"
    description: "管理 Issues、PR、仓库操作"
    enabled: true
    transport: stdio
    command: "npx"
    args: ["-y", "@anthropic-ai/mcp-server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    enabled_tools: []          # 空 = 全部启用
    timeout_ms: 120000
    category: "开发"
    icon: "github"
    hub_id: "mh1"
    source: hub

  - id: "postgres"
    name: "PostgreSQL MCP"
    description: "数据库查询和管理"
    enabled: true
    transport: stdio
    command: "npx"
    args: ["-y", "@anthropic-ai/mcp-server-postgres"]
    env:
      DATABASE_URL: "postgresql://user:pass@localhost:5432/mydb"
    source: hub

  # ── Streamable HTTP 传输 ──
  - id: "slack"
    name: "Slack MCP"
    description: "发送消息、管理频道通知"
    enabled: false
    transport: streamable-http
    url: "https://slack-mcp.example.com/mcp"
    headers:
      Authorization: "Bearer ${SLACK_API_KEY}"
    timeout_ms: 60000
    source: hub

  # ── 用户自定义 ──
  - id: "internal-api"
    name: "内部 API MCP"
    description: "公司内部 API 调用"
    enabled: true
    transport: streamable-http
    url: "https://api.internal.example.com/mcp"
    headers:
      X-API-Key: "${INTERNAL_API_KEY}"
    source: custom
```

**字段定义：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 唯一标识，用作工具名前缀（如 `github.search_issues`） |
| `name` | string | 是 | 显示名称 |
| `description` | string | 否 | 功能描述 |
| `enabled` | boolean | 是 | 是否启用，false 则该服务不会连接 |
| `transport` | enum | 是 | `stdio` / `sse` / `streamable-http` |
| `command` | string | stdio 时必填 | 启动命令 |
| `args` | string[] | 否 | 命令行参数 |
| `env` | dict | 否 | 环境变量（支持 `${VAR}` 引用，运行时从 OS 环境解析） |
| `url` | string | http 时必填 | 远程 MCP 服务 URL |
| `headers` | dict | 否 | 自定义 HTTP 头（支持 `${VAR}` 引用） |
| `enabled_tools` | string[] | 否 | 工具白名单，空 = 全部启用 |
| `timeout_ms` | number | 否 | 按 server 覆盖默认超时 |
| `category` | string | 否 | Hub 分类标签 |
| `icon` | string | 否 | 前端图标标识 |
| `hub_id` | string | 否 | Hub 来源 ID（Hub 安装时填充） |
| `source` | enum | 否 | `hub` / `custom` / `builtin` |

#### 2.4.2 配置层级：会话级 vs 消息级

```
配置层级（越下层优先级越高）：

┌── mcp_servers.yaml（系统默认）              ← 最底层
│   定义所有已安装服务的连接信息和全局启停状态
│
├── Session.mcp_servers（会话级默认）          ← 创建会话时设置
│   POST /sessions 时传入，或后续 PATCH 更改
│   决定该会话默认启用哪些 MCP 服务
│
├── Message.mcp_servers（消息级覆盖）          ← 最顶层
│   POST /messages 时传入，仅该消息生效
│   不传则使用会话级默认值
│
└── MCPServerConfig.enabled_tools（工具白名单）
    消息级可进一步限制具体启用哪些工具
    空 = 该 MCP 服务的全部工具可用
```

> **设计原理：** 会话级配置确保同一任务的历史消息有一致的工具集——如果 MCP 服务在对话中途被禁用，已执行的历史消息不受影响。消息级覆盖允许用户在某次具体请求中临时调整工具集（如"这次不要调用 GitHub"）。

#### 2.4.3 凭据管理

MCP 服务的 API 密钥等敏感信息不直接写在 `mcp_servers.yaml` 中，而是使用环境变量引用：

```yaml
env:
  GITHUB_TOKEN: "${GITHUB_TOKEN}"
  DATABASE_URL: "${MY_DB_URL}"
```

服务端在建立连接时解析 `${...}` 引用，从 OS 环境变量中获取实际值。解析失败的变量记录警告并跳过该条目。

### 2.5 工具发现与注册

#### 2.5.1 工具列表拉取

客户端安装 MCP 后，按 transport 类型建立连接并拉取工具列表：

```
  ┌─ Client 端 ─────────────────────────────────────────┐
  │                                                      │
  │  1. POST /mcp/install → 服务端登记 + 返回配置           │
  │  2. 按 transport 类型建立连接:                          │
  │     stdio: spawn 子进程 (npx/node/python)              │
  │     streamable-http: HTTP POST JSON-RPC               │
  │     sse: GET 建立 SSE 连接获取 endpoint, POST JSON-RPC  │
  │  3. initialize 握手                                    │
  │  4. tools/list 请求                                    │
  │     → 返回: [{name, description, inputSchema}, ...]    │
  │  5. POST /mcp/tools 上报工具清单          │
  │     服务端自动添加前缀: {server_id}_{tool_name}          │
  │     → 存储到 user_mcp_servers.tools（按 user 持久化）                  │
  │                                                      │
  └──────────────────────────────────────────────────────┘
```

#### 2.5.2 工具命名规则

为避免不同 MCP 服务间工具名冲突，所有 MCP 工具加 `{server_id}_` 前缀（工具原名存入 `user_mcp_servers.tools`，读取时由 `get_user_tools()` 自动添加前缀）：

```
原始工具名                   →    iWork 内部统一工具名
────────────────────────────────────────────────────
get_weather                 →    mh6_get_weather
geocoder                    →    mh9_geocoder
search_issues               →    mh1_search_issues

LLM 调用时使用完整前缀名。
```

#### 2.5.3 合并到 LLM 上下文

构建 LLM 上下文时，从 `user_mcp_servers.tools` 读取用户所有已安装 MCP 服务的工具（跨 session 共享），按消息级 `mcp_servers` 白名单过滤后合并到可用工具列表：

```python
# query_loop.py - _mcp_tools()

async def _mcp_tools(self, msg: Message) -> list[dict]:
    """从 user_mcp_servers.tools 读取用户已安装 MCP 服务的工具。"""
    if not self._user_mcp_repo:
        return []
    tools = await self._user_mcp_repo.get_user_tools(self.session.user_id)
    if not tools:
        return []

    if msg.mcp_servers:
        enabled_ids = {s.server_id for s in msg.mcp_servers}
        whitelist = {}
        for s in msg.mcp_servers:
            whitelist[s.server_id] = set(s.enabled_tools) if s.enabled_tools else None

        filtered = []
        for tool in tools:
            name = tool.get("name", "")
            if "_" not in name:
                continue
            server_id, actual_tool = name.split("_", 1)
            if server_id not in enabled_ids:
                continue
            tool_wl = whitelist.get(server_id)
            if tool_wl is not None and actual_tool not in tool_wl:
                continue
            filtered.append(tool)
        return filtered

    return tools
```

**冲突处理：** MCP 工具带有 `{server_id}_` 前缀，不会与客户端工具 `bash`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`/`skill` 冲突。

### 2.6 MCP 工具执行流程

所有 MCP 工具现在通过 `client.tool_request` 路径执行，与客户端工具使用相同的机制。客户端的 `client_mcp_tools` 名称已通过 `ToolDispatcher.set_client_mcp_tool_names()` 注册到分类器中。

```
LLM 返回: {tool_name: "mh1_search_issues", input: {query: "bug"}}

        │
        ▼
┌─ QueryLoopEngine._execute_tool_chunk() ───────────────────────┐
│                                                                │
│  1. ToolDispatcher.classify("mh1_search_issues")               │
│     → 在 self._client_mcp_tool_names 中 → ToolLocation.CLIENT │
│                                                                │
│  2. 推送 client.tool_request:                                  │
│     {                                                          │
│       "type": "client.tool_request",                           │
│       "tool_name": "mh1_search_issues",                        │
│       "input": {"query": "bug"},                               │
│       "request_id": "uuid-xxx"                                 │
│     }                                                          │
│                                                                │
│  3. 等待 Client 回传结果 (120s 超时)                            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─ Electron Client ─────────────────────────────────────────────┐
│                                                                │
│  收到 client.tool_request "mh1_search_issues"                  │
│  → 解析 server_id = "mh1" (去掉前缀)                            │
│  → 找到对应的 MCP 连接:                                         │
│     stdio: 写入子进程 stdin                                     │
│     streamable-http: POST JSON-RPC                             │
│  → 等待 MCP 响应                                                │
│  → POST /sessions/{id}/tool-result/{request_id} 回传           │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**服务端分类逻辑（伪代码）：**

```python
class ToolDispatcher:
    CLIENT_TOOLS = {"bash", "read_file", "write_file", "edit_file", "glob", "grep", "skill"}

    def __init__(self, mcp_registry=None):
        self._mcp = mcp_registry
        self._client_mcp_tool_names: set[str] = set()

    def set_client_mcp_tool_names(self, names: set[str]) -> None:
        """引擎每轮构建上下文时更新当前可用的 MCP 工具名称。"""
        self._client_mcp_tool_names = names

    def classify(self, tool_name: str) -> ToolLocation:
        if tool_name in CLIENT_TOOLS or tool_name in self._client_mcp_tool_names:
            return ToolLocation.CLIENT
        return ToolLocation.SERVER

# 引擎在每轮上下文构建时同步 MCP 工具名称
mcp_tools = self._mcp_tools(msg)
self.tool_dispatcher.set_client_mcp_tool_names({t["name"] for t in mcp_tools})
```

**以 `mh1_search_issues` 为例，完整的数据流：**

```
LLM 调用 mh1_search_issues
  → classify("mh1_search_issues") → CLIENT
  → client.tool_request → Electron 收到
      → 从工具名解析: server_id="mh1", actual_tool="search_issues"
      → 找到 mh1 的 MCP 连接 (stdio 子进程或 HTTP client)
      → 发送 JSON-RPC: {"method":"tools/call", "params":{"name":"search_issues","arguments":{...}}}
      → 等待 MCP 响应
  → POST /tool-result/{request_id} → 结果注入上下文
```

**MCP 连接由客户端管理：** 客户端在 `POST /mcp/install` 获取配置后，自行 spawn 子进程（stdio）或建立 HTTP 连接（streamable-http/sse），完成 `initialize` 握手和 `tools/list` 后通过 `POST /mcp/tools` 上报工具清单。服务端不再管理任何 MCP 进程或连接。

### 2.7 错误处理

#### 2.7.1 MCP 错误分类

将第一章 1.8.1 异常全景图中 #17"Server 工具执行失败"展开为以下子场景：

| # | 子场景 | MCP 状态变化 | 错误注入上下文格式 | 重试策略 |
|---|--------|-------------|--------------------|---------|
| 17a | **Server 启动失败** | DISCONNECTED → ERROR | `"MCP 服务 '{name}' 启动失败: {error}。该服务的工具暂不可用。"` | 指数退避重连 (最多 3 次) |
| 17b | **initialize 握手超时** | CONNECTING → ERROR | 同上 | 指数退避重连 |
| 17c | **tools/list 失败** | INITIALIZED → ERROR | `"MCP 服务 '{name}' 无法获取工具列表: {error}"` | 重连 1 次 |
| 17d | **工具不存在** | READY（不变） | `"MCP 服务 '{id}' 不存在工具 '{tool}'。可用工具: {list}"` | 不重试，LLM 自适应 |
| 17e | **tools/call 执行异常** | READY（不变） | `"MCP 工具 '{tool}' 执行失败: {error}"` | 不重试（幂等风险），LLM 自适应 |
| 17f | **tools/call 超时** (120s) | READY（不变） | `"MCP 工具 '{tool}' 执行超时 ({timeout}s)"` | 不重试，LLM 决定替代方案 |
| 17g | **连接意外断开**（进程崩溃） | READY → DISCONNECTED | 下次调用时发现服务不可用才报错 | 后台自动重连 |
| 17h | **JSON-RPC 协议错误** | 依赖错误类型 | `"MCP 服务 '{name}' 返回协议错误 (code={code}): {msg}"` | 不重试 |

#### 2.7.2 MCP 三级异常处理策略

| 级别 | MCP 场景 | 处理方式 |
|------|---------|---------|
| **1. 重试** | Server 启动失败、initialize 超时、运行中断连 | 指数退避重连（2^n s，最多 3 次），推送 `mcp_reconnecting` / `mcp_reconnected`；3 次耗尽 → `mcp_permanently_down` |
| **2. 放入上下文，LLM 决定** | tools/call 失败、工具不存在、tools/call 超时、JSON-RPC 协议错误 | 错误文本注入 LLM 上下文，LLM 自行决定重试、换方案、或告知用户 |
| **3. 终止任务** | 重连耗尽（`mcp_permanently_down`） | 该 MCP 服务标记 DISCONNECTED，推送 `system.status` 通知用户检查配置；不影响引擎和其他 MCP 服务 |

#### 2.7.3 MCP 相关的 system.status 通知

```typescript
// MCP 服务重连中
{
  type: "system.status";
  code: "mcp_reconnecting";
  message: string;          // "MCP 服务 'GitHub MCP' 连接断开，正在重连..."
  server_id: string;
  attempt: number;
  max_attempts: number;
}

// MCP 服务恢复
{
  type: "system.status";
  code: "mcp_reconnected";
  message: string;          // "MCP 服务 'GitHub MCP' 已恢复，{N} 个工具可用"
  server_id: string;
  tool_count: number;
}

// MCP 服务永久不可用
{
  type: "system.status";
  code: "mcp_permanently_down";
  message: string;          // "MCP 服务 'GitHub MCP' 多次重连失败，已停止尝试"
  server_id: string;
}

// MCP 工具调用错误（从 call_tool() 推送）
{
  type: "system.status";
  code: "mcp_server_not_installed" | "mcp_server_not_ready"
      | "mcp_tool_not_found" | "mcp_tool_timeout"
      | "mcp_tool_error" | "mcp_connection_lost";
  message: string;          // 错误详情
  server_id: string;
}
```

#### 2.7.4 实现审查：已覆盖 vs 待修复

对照 2.7.1 的 17a~17h 场景，逐一审查 `server/tools/mcp_runtime.py` 中的实际处理情况。

**已正确覆盖：**

| 场景 | 代码位置 | 实际行为 |
|------|---------|---------|
| **17d** 工具不存在 | `call_tool()` L563-569 | 对比 `state.tools`，返回错误 dict（含可用工具列表），LLM 自适应 |
| **17e** tools/call 执行异常 | `call_tool()` L618 | `except Exception` 兜底捕获，返回 `{success: false, error: str(e)}` |
| **17f** tools/call 超时 120s | `call_tool()` L594 | `except asyncio.TimeoutError`，返回超时错误 |
| **17g** 连接意外断开 | `call_tool()` L600 | `except (ConnectionError, OSError)` → 清空 tools → `transport.disconnect()` → `_schedule_reconnect()` → 推送 `mcp_reconnecting` |
| **17h** JSON-RPC 协议错误 | 各 transport `request()` | 响应含 `"error"` 字段时抛出 `MCPError`（code + message），`call_tool()` L618 捕获后返回给 LLM |

**待修复的缺口：**

| # | 问题 | 严重程度 | 根因 | 修复方向 |
|---|------|---------|------|---------|
| **G1** | **初始连接失败不触发自动重连**（17a/17b/17c 的"重试策略"列实际未执行） | **高** | `_do_connect()` L676 `except → raise`，`connect_server()` 不捕获，异常最终被 `_auto_connect_installed_mcps()`（`main.py:99`）和 `install_mcp()`（`mcp_routes.py:131`）的 `logger.warning` 吞掉。`_schedule_reconnect()` 仅在两处被调用：`_reconnect_loop()` 重连链、`call_tool()` L612（运行时断连），缺少"初始连接失败 → 调度重连"路径 | `_do_connect()` 的 `except` 块中，在 `raise` 前调用 `self._schedule_reconnect(sid)` |
| **G2** | **`notify("notifications/initialized")` 失败会中断整个连接** | **中** | `_do_connect()` L667 的 `notify` 在 try 块内，如果进程/连接恰好在此时断开，抛出的异常会使 initialize 已经成功的连接被标记为 ERROR | 将该行移出 try 块，或用 `try/except` 包裹（initialized 通知按 MCP 规范是 best-effort） |
| **G3** | **SSE 流中断后等待中的 `request()` 不清醒** | **中** | `SseTransport._read_sse()` L367-373：endpoint 已就绪后若 SSE 流断开，`_pending` 中的 Futures 无人处理，硬等 120s 超时 | `_read_sse()` 异常退出时遍历 `self._pending` 全部 `set_exception(ConnectionError("SSE 流已断开"))` |`` |
| **G4** | **StdioTransport `_request()` 未包装 JSON 解析异常** | **低** | L181 `json.loads(response_line)` — 若子进程输出非 JSON 内容，`json.JSONDecodeError` 直接上抛，最终作为 raw traceback 注入 LLM 上下文 | 包装为 `MCPError`，让 LLM 看到的是有意义的错误文本 |
| **G5** | **StdioTransport `notify()` 无 null check** | **低** | L159 `self.process.stdin.write(...)` — 如果在 `connect()` 失败后调用 `notify()`，`self.process` 为 None 导致 `AttributeError`。`HttpTransport` 和 `SseTransport` 的 `notify()` 均有 null check | 加 `if self.process is None: return` 守卫 |

**重连触发路径梳理（现状 vs 设计）：**

```
设计文档约定的触发点:                    实际代码的触发点:
                                          
connect 失败 → _schedule_reconnect()    ✗ 未实现（G1）
  17a 启动失败                            异常被外层吞掉，server 卡在 ERROR
  17b 握手超时                            同上
  17c tools/list 失败                      同上
                                          
call_tool 中途断连 → _schedule_reconnect()  ✓ 已实现（L600-612）
  17g 进程崩溃 / HTTP 不可达               ConnectionError/OSError 捕获 → 重连
  
重连链 → _schedule_reconnect()             ✓ 已实现（L688-715）
  第 N 次重试失败 → 第 N+1 次               指数退避 2^n 秒，最多 3 次
  3 次耗尽 → mcp_permanently_down          推送 system.status + 停重连
```

### 2.8 MCP 服务管理（Hub）

#### 2.8.1 Hub 数据来源

Hub 数据以 JSON 配置文件形式存储于服务端 `server/mcp-hub.json`，管理员可直接编辑此文件增删条目。客户端通过 API 获取可安装的 MCP 服务列表。

每条 Hub 条目包含完整的连接信息，客户端可直接用于展示和安装：

```json
{
  "server_id": "mh1",
  "server_name": "GitHub MCP",
  "description": "管理 Issues、PR、仓库操作",
  "icon": "🐙",
  "category": "开发",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {},
  "url": null
}
```

#### 2.8.2 安装 / 卸载流程

服务端仅维护安装状态（`mcp_state.json`），MCP 进程由客户端管理：

```json
// mcp_state.json — 服务端仅存安装记录
{
  "installed_ids": ["mh1", "mh3", "mh4", "cm1"],
  "custom_servers": [
    {
      "server_id": "cm1",
      "server_name": "内部 API MCP",
      "description": "公司内部 API 接口调用",
      "transport": "stdio",
      "command": "node",
      "args": ["./internal-api-server.js"],
      "env": {}
    }
  ]
}
```

```
安装:
  用户点击 [安装]
  → POST /mcp/install  Body: {server_id: "mh1"}
  → 校验 server_id 在 Hub 中存在 (404)，未安装 (409)
  → 写入 mcp_state.json 的 installed_ids
  → 返回配置: {server_id, server_name, transport, command/args/env 或 url/headers}
  → 客户端收到配置 → 按 transport 类型 spawn/connect
  → tools/list → POST /mcp/tools 上报工具  Body: {user_id, server_id, tools}

卸载:
  用户点击 [卸载]
  → DELETE /mcp/uninstall/{server_id}
  → 从 mcp_state.json installed_ids 移除
  → 返回 200: {success: true}
  → 客户端收到 200 → kill 子进程 / 断开 HTTP → POST /mcp/tools 上报 Body: {user_id, server_id, tools: []}
  → 未安装的返回 404

自定义 MCP:
  创建: POST /mcp/custom → 自动分配 cmN 前缀 ID + 自动安装 → 返回完整配置
  删除: DELETE /mcp/custom/{server_id} → 同时卸载 → 客户端 kill 进程 + 上报 []
```

#### 2.8.3 与主对话流程的联通

MCP 配置通过以下现有字段与主对话流程打通，**无需新增消息级 API**：

```
POST /sessions         body.mcp_servers    → 会话级默认启用的 MCP 服务
PATCH /sessions/{id}   body.mcp_servers    → 更新会话默认
POST /messages         body.mcp_servers    → 消息级覆盖（已在 MessageCreate 定义）
```

### 2.9 MCP 查询接口定义

所有 MCP 管理接口挂载在 `/mcp` 前缀下，由 `server/api/mcp_routes.py` 实现。

| 方法 + 路径 | 说明 | 持久化 |
|------------|------|--------|
| `GET /mcp/hub` | 浏览 Hub 中所有可安装的 MCP 服务 | 读取 `mcp-hub.json` |
| `GET /mcp/installed` | 查看已安装的 MCP（含完整连接配置） | 读取 `mcp_state.json` |
| `POST /mcp/install` | 安装 Hub 中的 MCP — 登记 + 返回配置 | 写入 installed_ids |
| `DELETE /mcp/uninstall/{server_id}` | 卸载 MCP | 移除 installed_ids |
| `GET /mcp/custom` | 查看自定义 MCP 服务列表 | 读取 custom_servers |
| `POST /mcp/custom` | 创建自定义 MCP（自动分配 cmN + 自动安装） | 追加 custom_servers + installed_ids |
| `DELETE /mcp/custom/{server_id}` | 删除自定义 MCP（同步卸载） | 移除 custom_servers + installed_ids |

主对话相关：
| 方法 + 路径 | 说明 |
|------------|------|
| `POST /mcp/tools` | 客户端上报 MCP 工具清单（安装后/卸载后），`user_id` 由客户端在 body 中传入，无需 `session_id` |

```typescript
// ═══════════════════════════════════════════
// GET /mcp/hub — 浏览 Hub 所有可安装的 MCP（不变）
// ═══════════════════════════════════════════

Response 200:
{
  servers: {
    server_id: string;
    server_name: string;
    description: string;
    icon: string;
    category: string;
    transport: "stdio" | "sse" | "streamable-http";
    command: string | null;
    args: string[];
    url: string | null;
    env: Record<string, string>;
  }[];
}


// ═══════════════════════════════════════════
// GET /mcp/installed — 查看已安装的 MCP（不变）
// ═══════════════════════════════════════════

Response 200:
{
  installed: {
    server_id: string;
    server_name: string;
    description: string;
    icon: string;
    category: string;
    transport: string;
    command: string | null;
    args: string[];
    url: string | null;
    env: Record<string, string>;
  }[];
}


// ═══════════════════════════════════════════
// POST /mcp/install — 安装 Hub 中的 MCP
// ═══════════════════════════════════════════

Request Body:
{ server_id: string; }

Response 200 — 返回配置供客户端建立连接:
// stdio 类型:
{
  server_id: string;
  server_name: string;
  transport: "stdio";
  command: string;            // 如 "npx"
  args: string[];             // 如 ["-y", "hefeng-mcp-server"]
  env: Record<string, string>;  // 含 ${VAR} 占位符
}
// streamable-http 类型:
{
  server_id: string;
  server_name: string;
  transport: "streamable-http";
  url: string;                // 如 "https://mcp.example.com/mcp?ak=${BAIDU_MAP_AK}"
  headers: Record<string, string>;
}
// sse 类型:
{
  server_id: string;
  server_name: string;
  transport: "sse";
  url: string;
  headers: Record<string, string>;
}

Response 404:
{ error: "not_found"; message: "server_id 不在 hub 中"; }

Response 409:
{ error: "already_installed"; message: "该 MCP 已安装"; }


// ═══════════════════════════════════════════
// DELETE /mcp/uninstall/{server_id} — 卸载 MCP
// ═══════════════════════════════════════════

Response 200:
{ success: true; }

Response 404:
{ error: "not_found"; message: "MCP 不存在"; }


// ═══════════════════════════════════════════
// POST /mcp/tools — 客户端上报工具清单（多用户方案，user_id 由客户端传入）
// ═══════════════════════════════════════════

// 安装后上报（工具原名不含前缀，存入 user_mcp_servers.tools，
// 读取时由 get_user_tools() 自动加 {server_id}_ 前缀）:
Request:
{
  user_id: "00000000-0000-0000-0000-000000000001";
  server_id: "mh6";
  tools: [
    {
      name: "get_weather";
      description: "查询指定城市的天气信息";
      input_schema: {
        type: "object";
        properties: { city: { type: "string"; description: "城市名称" } };
        required: ["city"];
      };
    }
  ];
}

Response 200:
{ received: true; tool_count: 1; }

// 卸载后上报空列表:
Request:
{ user_id: "00000000-0000-0000-0000-000000000001"; server_id: "mh6"; tools: []; }

Response 200:
{ received: true; tool_count: 0; }


// ═══════════════════════════════════════════
// GET /mcp/custom — 查看自定义 MCP（不变）
// POST /mcp/custom — 创建自定义 MCP（返回配置即可）
// DELETE /mcp/custom/{server_id} — 删除自定义 MCP（不变）
// ═══════════════════════════════════════════
```

### 2.10 与 Query Loop 引擎的集成点总结

MCP 在第一章 Query Loop 引擎架构中的注入位置（新架构 — 客户端执行）：

```
QueryLoopEngine
│
├── _run_message_loop()
│   │
│   ├── _mcp_tools(msg)  →  从 user_mcp_servers.tools 读取（async）
│   │   └── 客户端已通过 POST /mcp/tools 上报到 user_mcp_servers
│   │
│   ├── tool_dispatcher.set_client_mcp_tool_names(...)
│   │   └── 将当前 MCP 工具名注入分类器，标记为 CLIENT
│   │
│   ├── context_mgr.build()
│   │   └── tools = [client_tools] + [skill] + [mcp_tools (filtered)]
│   │
│   ├── llm.stream(tools=[全部合并后的工具列表])
│   │
│   └── _execute_tool_chunk()
│       ├── tool_dispatcher.classify(name) → CLIENT (含 MCP)
│       └── if CLIENT: client.tool_request → sync_waiter.wait(120s)
│           └── Electron 本地执行 / 转发到 MCP 进程
│               └── POST /tool-result/{request_id}
│
├── _push_chunk() → 结果注入 LLM 上下文（服务端内部）
│
└── context_mgr.append_tool_result()
```

**关键变化（vs 旧架构）：**
- 服务端不再启动 MCP 子进程（`main.py` 移除 `_auto_connect_installed_mcps()`）
- 工具列表来自 `user_mcp_servers.tools`（客户端上报，按 user 持久化，跨 session 共享），不再来自 `MCPToolRegistry.collect_all_tools()`
- 所有 MCP 工具归类为 `CLIENT`，走 `client.tool_request` 路径
- `skill` 工具也归入 `CLIENT_TOOLS`，同样走 `client.tool_request`

---

<a id="3-skill-集成"></a>

