# iWork Agent 系统设计 — 面试问题答案

> 基于 `agent-client-serve-merry-meerkat.md` 技术详细设计文档及实际源码。

---

## 一、架构与整体设计

### 1. 为什么选择 Client-Server 架构而不是纯客户端或纯服务端方案？各自的优劣是什么？

**选择 Client-Server 的核心原因：**

- **安全性**：文件系统操作（bash、write_file 等）必须在客户端 Electron 沙箱内执行，服务端不应直接操作用户本地文件。Server 只负责 LLM 推理编排，Client 持有执行权。
- **工具执行位置**：客户端工具（bash、read_file、write_file、edit_file、glob、grep）需要访问本地文件系统和 shell，天然属于客户端。客户端 MCP 工具由用户本地进程提供，也在客户端执行。
- **持久化与共享**：会话历史、记忆、规则存储在服务端 PostgreSQL，多设备可共享上下文。如果纯客户端，换设备就丢失所有历史。
- **计算分离**：LLM API 调用在服务端（API Key 不暴露给客户端），思考/推理的 token 消耗在服务端计量和审计。

**各方案优劣：**

| 方案 | 优势 | 劣势 |
|------|------|------|
| Client-Server（本方案） | 安全隔离、多设备共享、API Key 保护、服务端可观测性 | 需要网络连接、服务端运维成本、离线不可用 |
| 纯客户端 | 离线可用、低延迟、无服务端成本 | API Key 暴露、无法多设备同步、审计困难 |
| 纯服务端 | 客户端零依赖、统一管控 | 无法操作用户本地文件、沙箱执行安全性差 |

---

### 2. 为什么用 MCP Streamable HTTP 而不是 WebSocket？SSE 和 Streamable HTTP 在你的设计里各有什么取舍？

**选择 Streamable HTTP 的原因：**

- MCP Streamable HTTP 是 MCP 协议 2025 年的标准传输方式，直接复用协议规范，无需自定义通信层。
- 主对话通道：`POST /sessions/{id}/messages` 的响应本身就是 NDJSON 流，一个 HTTP 请求即可完成"发送消息 + 接收流式回复"，不需要像 SSE 那样维护额外的 GET 长连接。
- 相比 WebSocket 的优势：无需升级协议、天然支持 HTTP 中间件（认证、限流）、防火墙友好、调试简单（curl 直接可用）。

**SSE vs Streamable HTTP 的取舍：**

| | SSE | Streamable HTTP（本方案） |
|--|-----|--------------------------|
| 主通道连接数 | 2 个（POST 发消息 + GET 收流） | 1 个（POST 即流） |
| 协议格式 | `event: xxx\ndata: {...}\n\n` | 每行一个 JSON（NDJSON） |
| 重连 | GET /stream?since_seq=N | 仅重连时用 GET /stream |
| 消息边界 | 空行分隔 | 换行符分隔 |

**不用 WebSocket 的原因**：WebSocket 需要长连接保活，断线重连逻辑复杂（需自定义心跳和重连协议），而 HTTP NDJSON 流天然支持断线后通过 `GET /stream?since_seq=N` 从 StreamBuffer 回放，重连逻辑更简单可靠。

---

### 3. 解释一下这个系统的核心数据流：从用户发消息到收到回复，经历了哪些关键步骤？

```
1. Client POST /sessions/{id}/messages（含 content、mode、workspace、model 等）
   → 服务端 enqueue()：写入 messages 表（status='pending'），设置 _wake_event

2. QueryLoopEngine.run() 被唤醒
   → _dequeue_next()：SELECT ... FOR UPDATE SKIP LOCKED 原子出队
   → status 改为 'processing'

3. _run_message_loop() 开始：
   a) message.before hooks → 可拦截/修改消息
   b) 构建上下文（ContextManager.build）：
      - system prompt（安全约束 + 场景 prompt + 模式 prompt + 日期）
      - <available_skills> XML + <rules> XML + <memories> XML
      - 工具定义（client_tools + mcp_tools + server_tools）
      - 从 conversation_history 表加载历史消息
   c) llm.before hooks → 可修改 messages/tools
   d) LLM 流式调用（_llm.stream()）
   e) 逐 chunk 处理：
      - thinking/text → 立即 _push_chunk() 推送客户端（agent.thinking / agent.text）
      - tool_use → 权限检查 → _execute_tool_chunk()
        - 客户端工具 → push client.tool_request → sync_waiter.wait() 等回传
        - 服务端工具 → dispatch 执行 → 结果注入上下文
   f) 终止判断 → llm.after hooks → message.after hooks

4. 每个 _push_chunk() 写入 StreamBuffer（重连回放）+ _chunk_queue（实时推送）

5. HTTP handler 的 chunk_generator 从 _chunk_queue 逐条读取，以 NDJSON 格式
   写入 HTTP 响应流，推送给客户端

6. 客户端收到 NDJSON 流，逐条解析渲染（打字机效果、工具确认卡片等）
```

---

### 4. 三种模式（Ask / Plan / Build）的设计动机是什么？什么场景下用户会选择不同的模式？

**设计动机**：将 AI 的自主权分为三个等级，对应不同的风险容忍度和用户控制需求。

| 模式 | 自主权 | 设计动机 | 典型场景 |
|------|--------|---------|---------|
| **Ask** | 零（纯文本） | 纯粹的知识问答，不需要任何工具，安全无副作用 | "什么是闭包？"、"解释这段代码" |
| **Plan** | 中（先审后做） | 复杂多步骤任务，需要用户前置审核方案，确认后自动执行 | "帮我搭建一个 React 项目"、"重构这个模块" |
| **Build** | 高（每步确认） | 需要精细控制每一步操作，每步都可确认/跳过/终止 | "把 src/utils.ts 里的 foo 重构并跑通测试" |

**Ask**：`tools=None, tool_choice="none"`，LLM 只能输出文本。
**Plan**：turn 0 不带 tools（只给 plan_question），生成计划文本，用户确认后 turn 1+ 带 tools 自动执行。
**Build**：每轮带 tools，每个 tool_use 产生后暂停，等待用户确认/跳过/终止。

---

### 5. 会话工具清单在创建时注册、之后不可变——这种设计的利弊是什么？如果用户升级了客户端、增加了新工具，怎么处理？

**优势：**
- 上下文一致性：同一会话的历史消息可复现（如果工具清单中途变了，重现历史对话时 LLM 看到的工具集不同）
- 带宽节省：不需要每条消息携带完整的工具定义
- 安全性：会话建立时锁定能力边界，防止后续恶意利用

**劣势：**
- 灵活性差：用户升级客户端后，旧会话只能用旧工具
- 如果客户端的工具定义有 bug，需要新建会话才能使用修复后的版本

**处理方式**：客户端升级后自动创建新会话。旧会话保留原有工具集以保持一致性。新会话使用新工具定义。这是"会话不可变配置"的设计哲学——类似于 Docker image 的不可变性。

---

### 6. 消息级配置独立于会话级默认值，为什么历史消息要独立存储配置？这会带来什么存储开销？

**为什么独立存储：**
- 可复现性：用户中途切换了 workspace/model/mode，历史消息仍保留当时的配置，重放时上下文一致
- 审计需求：每条消息的配置（用的什么 model、什么 workspace）需要可追溯
- 灵活性：用户可以在同一会话中切换模式（从 Ask 切到 Build），每条消息独立记录

**存储开销**：每条消息多存储 scene_mode、workspace、model、mode 四个字段（约 200 bytes），对于万级消息量约 2MB 额外存储，可忽略不计。

---

### 7. 如果让你把这个系统扩展到多用户高并发场景，瓶颈在哪？你会在哪些地方做改造？

**主要瓶颈：**

1. **单会话单协程模型**：QueryLoopEngine 是会话级单实例，FIFO 串行处理。同一会话内的消息天然串行没问题，但 10000 个活跃会话 = 10000 个协程，事件循环调度开销可控（asyncio 设计目标就是万级并发）。

2. **数据库连接池**：每个 turn 多次 DB 操作（context builder 读历史、tool 执行写结果、chunk 推送）。高并发下 PG 连接池（默认 10+20 overflow）是瓶颈。改造：读写分离、conversation_history 缓存到 Redis。

3. **LLM API 限流**：所有会话共享同一个 LLM API key，高并发下触发 429。改造：多 API key 轮询 + 优先级队列（付费用户优先）。

4. **StreamBuffer 内存**：每个会话的 StreamBuffer（deque 500 条）在万级会话时约占用 500MB+ 内存。改造：buffer 冷数据刷到 Redis/磁盘。

5. **水平扩展**：当前单进程模型，改造：引入 Redis Pub/Sub 做跨进程消息路由，会话 sticky 到特定进程。

6. **MCP 子进程**：每个 MCP server 一个子进程，多用户共享时需要进程池管理。

---

## 二、Query Loop 引擎

### 8. 为什么 Per-Message Loop 要串行执行消息（FIFO 队列）而不是并发处理？什么时候并发处理会是更好的选择？

**串行的原因：**

- **上下文一致性**：后一条消息的处理依赖于前一条消息对上下文的修改（工具结果注入、assistant 回复等）。如果并发处理，上下文会产生竞态条件。
- **用户心智模型**：用户期望消息按发送顺序处理，先发的先回复。并发处理会导致回复乱序。
- **LLM 连贯性**：每一轮 LLM 调用都基于完整的对话历史，并发会破坏这个连续性。

**并发处理的适用场景：**

- 独立会话之间——不同会话天然可以并发（已经是这样设计的，每个会话独立协程）
- 同一会话内的"子任务"——比如多 Agent 协作中，team lead 把任务分派给多个子 agent，这些子 agent 可以并行执行（通过 TaskToolHandler）
- 纯查询类消息（Ask 模式）之间——如果明确标记为"无副作用"，理论上可以并发

---

### 9. 解释一下 DB + asyncio.Event 混合方案的队列设计——为什么不用纯内存 asyncio.Queue？FOR UPDATE SKIP LOCKED 在这个场景解决什么问题？

**为什么不用纯内存 asyncio.Queue：**

| 问题 | 纯内存 asyncio.Queue | DB + Event（本方案） |
|------|---------------------|---------------------|
| 服务器重启 | 队列丢失 | 从 DB 恢复 status='pending' |
| 并发安全 | 单进程 OK | `FOR UPDATE SKIP LOCKED` |
| 用户手动取消 | 需自定义索引 | `UPDATE WHERE status='pending'` |
| 客户端查询队列 | 需额外 API 读内存 | `SELECT` 即得 |

**DB + Event 混合方案**：DB 是数据的真实来源（持久化、可查询、可恢复），asyncio.Event 是调度信号（避免轮询 DB）。

**FOR UPDATE SKIP LOCKED 解决的问题**：
- 防止并发出队：如果两个进程/协程同时 dequeue，`FOR UPDATE` 锁定选中的行，`SKIP LOCKED` 让另一方跳过被锁行取下一行
- 防止消息丢失：原子操作（SELECT + UPDATE 在同一事务中），不会出现"选中了但没更新状态"的情况

---

### 10. 状态机中的 WAITING_SYNC 状态如何处理超时？超时后引擎做什么？

WAITING_SYNC 状态有 4 种等待场景，超时行为不同：

| 场景 | 超时时间 | 超时后行为 |
|------|---------|-----------|
| Plan 模式等确认 | 300s（configurable） | 推送 timeout → 引擎回到 IDLE，消息终止 |
| Build 模式每步等确认 | 300s | 推送 timeout → 引擎回到 IDLE，消息终止 |
| plan.question 等回答 | 300s | 推送 `plan.question_timeout` → 注入"[用户未回应]" → LLM 继续 |
| Client 工具等回传 | 120s | 推送 `client.tool_timeout` → 结果设为失败 → 注入上下文 → LLM 决定重试/跳过 |

核心机制：`SyncWaiter.wait(session_id, timeout=N)` 使用 `asyncio.wait_for(event.wait(), timeout=N)`。超时返回 None，调用方根据场景决定下一步。不会永远卡住。

---

### 11. Plan 模式中 plan.question 的设计——为什么 LLM 要向用户提问而不是生成完整计划后再确认？这会导致什么问题？

**为什么中间提问：**

- 需求不完整时，LLM 与其猜测用户意图（可能猜错导致整个计划无效），不如直接追问澄清
- 减少"计划被拒绝 → 重新生成"的循环次数
- 用户可能在生成计划前就想补充关键信息

**可能导致的问题：**

1. **提问过多**：LLM 可能连续追问很多细节问题，用户体验变成"被 interrogated"
2. **用户疲劳**：如果用户期望快速得到结果，连续提问会让用户放弃
3. **强依赖用户在线**：用户必须实时回答才能推进，异步场景不友好
4. **LLM 可能不提问**：LLM 可能认为信息够了直接生成计划，但实际缺少关键信息

**设计上的缓解**：LLM 自己决定是否提问及提问次数，由 prompt 引导"在必要时才提问，不要过度追问"。

---

### 12. 如果一条消息处理中用户发了另一条消息，引擎如何保证不会丢失或乱序？详细说一下入队→排队→出队的完整流程。

**完整流程：**

```
1. 用户发 msg2 时，msg1 正在 PROCESSING
   → POST /sessions/{id}/messages
   → enqueue() 获取 _lock
   → SELECT MAX(queue_position) FROM messages WHERE status='pending' → 得 position=1
   → INSERT INTO messages (status='pending', queue_position=1)
   → _wake_event.set()（引擎醒着，无操作）
   → 释放 _lock
   → 返回 HTTP 200，开始 NDJSON 流（推送 queue.enqueued chunk）

2. msg1 处理完成
   → finally 块：msg1.status='completed'
   → _dequeue_next() 获取 _lock
   → SELECT ... WHERE status='pending' ORDER BY queue_position LIMIT 1 FOR UPDATE SKIP LOCKED
   → 选中 msg2 → UPDATE status='processing', queue_position=NULL
   → _renumber_queue()（此时队列空，无操作）
   → 释放 _lock
   → msg2 开始 PROCESSING

3. 如果用户发 msg3 的同时 msg2 正在 process
   → 同上，msg3 进入 queue_position=1
   → msg2 完成后自动出队 msg3
```

**保证不丢失**：DB 写入是持久化的，服务器重启后 pending 消息仍在。
**保证不乱序**：queue_position + ORDER BY + FIFO 出队。

---

### 13. 连续 3 次相同工具调用检测死循环——这个方法有盲区吗？如果 LLM 故意制造"每隔一次才相同的调用"，你怎么防御？

**盲区：**

1. **交替模式**：A → B → A → B → A → B（每次不同但模式循环）
2. **间隔重复**：A → B → C → A → B → C（3 次相同但中间隔了别的）
3. **参数微小变化**：每次改一个参数值但效果相同（如 `read_file` 偏移量 +1）
4. **语义等价但 Hash 不同**：参数顺序不同但语义相同（JSON key 顺序）

**防御方案：**

- **滑动窗口扩大**：不只是 3 次，而是看最近 N 次（如 10 次）中是否有周期性模式
- **语义 Hash**：对参数做规范化（JSON key 排序、数值四舍五入）后再 hash
- **工具类别去重**：同一类文件操作（read_file + read_file）即使参数不同也警告
- **Turn 上限兜底**：max_turns=25 是硬限制，即使检测漏了也不会无限循环
- **Token 成本监控**：连续无进展的 tool_use 会触发告警

---

### 14. _check_tool_permission 中权限拒绝后为什么选择注入错误上下文而非终止？这种"降级"策略在什么场景下会出问题？

**为什么选择降级而非终止：**

- LLM 可能有替代方案：比如 `/etc/passwd` 被拦截后，LLM 可以读取 `/etc/shadow` 的替代文件或换一种方式获取信息
- 保持对话连续性：如果因为一个工具被拒就终止整个消息，用户体验很差
- LLM 的自适应能力：LLM 看到错误反馈后可以调整策略

**可能出问题的场景：**

1. **安全绕过**：LLM 被告知某个路径被拦截后，可能尝试用其他工具绕过限制（如用 `bash cat` 代替 `read_file`）
2. **信息泄露**：错误反馈（"路径 /etc/passwd 被拦截"）本身可能泄露系统信息
3. **无限重试**：LLM 可能不断尝试不同路径，直到找到可读的，浪费 token
4. **权限判断依赖 LLM 理解**：LLM 可能不理解为什么被拒绝，继续产生更多违规调用

**缓解**：`PermissionChecker` 使用 blocklist 而非 allowlist，覆盖常见敏感路径。bash 命令有额外的 deny-rm Hook 防御。

---

### 15. 服务器重启后，正在处理的消息重置为 pending 并从头执行——这是否会导致副作用（如重复文件写入）？你有什么应对方案？

**是的，会导致副作用：**

- 如果消息在重启前已经执行了 `write_file`，重启后从头执行会再次写文件（可能重复写入）
- 如果已经执行了 `bash` 命令（如 `npm install`），重启后会再次执行
- `git commit` 可能被重复执行

**应对方案：**

1. **幂等性设计**：提示 LLM（通过 system prompt）尽量使用幂等工具调用（如 `edit_file` 而非 `write_file`，先读后写）
2. **checkpoint 机制**：将每个 tool_use 的执行结果持久化到 DB，重启后跳过已完成的工具调用
3. **消息级事务**：把消息处理拆分为多个子任务，每个子任务完成后持久化状态，重启后从中断点恢复
4. **用户通知**：当前方案已推送 `session_recovering` status chunk，告知用户"消息将重新执行"
5. **tool_call_id 去重**：利用 tool_call_id 在 context 中检测重复，如果 LLM 看到之前的相同 tool_call 结果，它会跳过

---

## 三、异常处理

### 16. 四级异常处理策略的设计哲学是什么？第 2 级"降级"和第 4 级"终止"的边界在哪？

**设计哲学**：按"可恢复性"和"是否需要用户介入"分级，最小化用户感知的中断。

```
第 1 级：重试（自动恢复）—— 瞬时故障，退避重试即可
第 2 级：降级（LLM 自适应）—— 局部失败，错误注入上下文让 LLM 调整
第 3 级：暂停（等用户介入）—— 需要用户决策，挂起等待
第 4 级：终止（不可恢复）—— 致命错误，释放引擎
```

**降级 vs 终止的边界：**

- **降级**：错误是**局部**的（一个工具调用失败）、**可替代**的（有其他方式达成目标）、**非安全相关**的（不是内容安全拦截）
- **终止**：错误是**全局**的（API 认证失败）、**不可替代**的（Rate Limit 耗尽）、**安全相关**的（content_filter）、**资源耗尽**的（max_turns、timeout）

---

### 17. 流连接断开时的缓冲回放机制——buffer 上限 500 条，超出后会丢弃什么？如果用户离线很久（比如合盖一晚上），回来看到的是什么？

**超出 500 条后**：FIFO 丢弃最旧的数据块（`buffer.pop(0)`）。丢弃的是**早期的中间状态**（如 agent.text 增量、agent.thinking），而非最终结果。`message.complete` 是最后推送的，如果 buffer 没满就还在。

**合盖一晚上的场景**：

1. 用户合盖 → 客户端断开 → 引擎继续执行（StreamBuffer 开始缓存）
2. 消息处理完成（通常 300s 超时），推 `message.complete`
3. 如果处理期间产生超过 500 条 chunk：最旧的前几条 agent.text 增量丢失
4. 用户开盖 → 自动重连 → `GET /stream?since_seq=N`
5. 回放 buffer 中 `seq > N` 的 chunk

**用户看到什么**：
- 如果 buffer 没满 500 条：完整追回所有内容（包括打字机效果）
- 如果 buffer 满了：缺少开头的部分文本增量，但 AI 完整回复可通过 `GET /sessions/{id}/replay` 从 stream_events 表获取
- `message.complete` 或 `message.error` 一定会被推送（如果消息已处理完）

---

### 18. LLM 流中断后重试 1 次——为什么只重试 1 次而不同于普通网络重试的 3 次？断点续传为什么在流式 LLM 场景不可行？

**为什么只重试 1 次：**

- 流中断和网络超时不同：流已经建立并传输了一部分数据，中断通常意味着 LLM 推理已经出错（而非临时网络抖动）
- 重新调用意味着从头生成（浪费之前已传输的 token），成本高昂
- 如果第一次重试也失败，环境条件大概率没变（API 仍然不健康），继续重试是浪费
- 普通网络重试 3 次针对的是"建立连接"阶段，流中断是"传输中"阶段

**断点续传不可行的原因：**

- LLM 是自回归生成：token_N 依赖 token_{1..N-1}。中断后无法从 token_N+1 恢复，因为服务端没有保存生成前的完整状态
- 即使保存了 KV Cache，不同的 API 提供商实现不同，没有统一的"从第 N 个 token 继续"的接口
- 流式响应本身不包含"已确认接收"的反馈机制（不像 TCP ACK），服务端不知道客户端收到了哪些

---

### 19. 你的系统里有很多超时时间——这些数字怎么定的？如果场景差异很大，怎么优雅配置？

**超时时间的制定依据：**

| 超时 | 值 | 依据 |
|------|-----|------|
| 消息总耗时 | 300s | Claude API 单次调用最大约 120s，2-3 轮 + 工具执行 |
| Plan 等待 | 300s | 用户阅读计划 + 思考的时间预期 |
| Client 工具回传 | 120s | `npm install` 等命令的合理上限 |
| Hook 超时 | 5000ms（可自定义） | 子进程执行不应阻塞主循环太久 |
| sync_wait_timeout | 300s | 全局默认 |

**优雅配置方案**：

- 分层配置：全局默认（settings）→ 会话级配置 → 消息级配置，允许逐层覆盖
- 工具粒度：不同 MCP 工具可配置不同的 timeout（`mcp_servers` 表中记录）
- 动态调整：根据历史工具执行时长自动调整（P95 耗时 + buffer）
- 环境自适应：通过 `IWORK_` 环境变量注入，K8s ConfigMap 热更新

---

### 20. sync_waiter.wait() 等待用户确认时，如果用户永远不回应，WAITING_SYNC 状态会永远卡住吗？

**不会永远卡住**：`SyncWaiter.wait()` 使用 `asyncio.wait_for(event.wait(), timeout=N)`，超时后返回 None。调用方（_execute_tool_chunk 等）收到 None 后会：

- Plan 确认超时 → 推送 timeout chunk → 消息终止
- Build 步骤超时 → 推送 timeout chunk → 消息终止
- plan.question 超时 → 推送 `plan.question_timeout` → 注入"[用户未回应]"
- Client 工具超时 → 推送 `client.tool_timeout` → 结果设为失败

**资源泄漏防护**：超时后 event 未被 set，但 asyncio.Event 本身不持有外部资源。DB 连接由连接池管理（有回收机制）。唯一残留的是 `SyncWaiter._events` 字典中过期的 session_id（在 resolve 时 pop 掉，超时后在下次等待时清理）。

---

## 四、MCP 工具集成

### 21. MCP 工具和客户端工具的核心区别是什么？为什么 MCP 工具在服务端执行而客户端工具在 Electron 端执行？

**核心区别：**

实际上，本设计中**所有工具执行权都在客户端**。MCP 工具和客户端工具的区别是：

| | 客户端工具 | 客户端 MCP |
|--|-----------|-----------|
| **执行方式** | Electron 本地直接执行 | Electron 转发到 MCP 子进程/HTTP 服务 |
| **注册方式** | POST /sessions 时上报 | 安装后 tools/list → POST /mcp/tools |
| **典型工具** | bash, read_file, write_file, edit_file | 天气查询、地图 API、搜索 |
| **生命周期** | 随客户端版本固化 | 可独立安装/卸载/更新 |

两者都通过相同的 `client.tool_request` → `POST /tool-result` 通道回传结果。区别仅在于客户端的执行路径不同。

---

### 22. 为什么 MCP 工具名要加 {server_id}_ 前缀？不加会有什么问题？

**加前缀的原因**：

- **命名空间隔离**：防止不同 MCP 服务提供同名工具（两个服务都有 `search`）→ 变成 `mh1_search` 和 `mh2_search`
- **路由依据**：`ToolDispatcher` 解析 `server_id_toolname` 前缀，将调用路由到正确的 MCP 服务
- **LLM 区分度**：LLM 看到 `mh6_get_weather` 和 `mh9_get_weather`，可以根据前缀判断来源

**不加前缀的问题**：

- 工具名冲突：两个服务都有 `search`，LLM 无法区分，dispatcher 无法路由
- 无法追踪来源：审计日志中只能看到 `search` 被调用，不知道是哪个 MCP 服务
- 无法做权限控制：无法针对特定 MCP 服务的工具做禁用

---

### 23. 三种传输方式（stdio / SSE / Streamable HTTP）分别适用于什么场景？如果让你选一种作为推荐方式，你选哪个？

| 传输方式 | 适用场景 | 优势 | 劣势 |
|---------|---------|------|------|
| **stdio** | 本地 MCP 服务（子进程） | 零网络开销，安全（不暴露端口） | 仅限本机，需 spawn 进程 |
| **SSE** | 远程 MCP 服务（只读/订阅） | 服务端主动推送，适合 streaming | GET 长连接 + POST 双通道 |
| **Streamable HTTP** | 远程 MCP 服务（通用） | 标准 HTTP，POST 即响应，无需双通道 | 不支持服务端主动推送 |

**推荐 Streamable HTTP**：原因——
- MCP 协议的标准推荐传输方式
- 单一 HTTP 连接，无需双通道
- 防火墙友好，调试简单
- 与主对话通道技术栈一致

---

### 24. MCP 凭据通过 ${ENV_VAR} 引用——如果环境变量在运行时被修改，正在运行的 MCP 进程会感知到吗？如何处理凭据轮换？

**不会感知**：MCP 进程启动时通过 `os.environ` 读取环境变量，进程运行期间 env 不会改变（每个进程有独立的环境变量副本）。

**凭据轮换处理**：

1. **进程重启**：修改 .env → 重启 MCP 进程（需要重新 spawn，`MCPServerState.status` 改为 `DISCONNECTED` → 自动重连）
2. **热加载**：`MCPToolRegistry` 可监听文件变更信号，触发 `disconnect + reconnect`
3. **通知机制**：推送 `mcp_reconnecting` status chunk 告知用户"MCP 服务正在重连"
4. **凭据错误检测**：MCP 工具返回认证错误 → 推 `mcp_tool_error` → 通知用户检查凭据

---

### 25. 如果两个 MCP 服务提供了同名的工具——虽然加了前缀，但 LLM 看到的功能描述相似，它会选错吗？除前缀外还有什么手段帮助 LLM 区分？

**LLM 可能会选错**：如果两个 tool description 高度相似（如两个 `search` 工具），LLM 可能随机选一个，或者选的不是最合适的。

**除前缀外的辅助手段：**

1. **描述差异化**：在 tool description 中加入来源信息（如 `[来自高德地图]`），帮助 LLM 理解差异
2. **System prompt 引导**：在 context 中说明可用的 MCP 服务及各自能力范围
3. **工具分组**：在 system prompt 中按服务分组列出工具
4. **命名规范**：server_id 使用有意义的名称（如 `gaode`、`baidu`）而非 `mh1`

---

### 26. MCP 工具发现流程为什么放在客户端而非服务端？这样设计有什么好处和代价？

**为什么放在客户端：**

- MCP 服务在用户本地运行（子进程或本地 HTTP），服务端无法直接连接
- 工具发现需要实际执行 `tools/list`，只有客户端能访问 MCP 进程
- 不同用户的 MCP 配置不同（凭据、环境变量），无法在服务端统一管理

**好处**：
- 安全性：MCP 凭据（API Key 等）不经过服务端
- 灵活性：用户可以安装自定义 MCP 服务
- 隐私性：本地 MCP 的 tool list 只有用户自己能看到

**代价**：
- 客户端复杂度增加
- 服务端需要信任客户端上报的 tool 定义（可能被篡改）
- 多设备同步难（每个设备要独立安装配置 MCP）

---

## 五、Skill 集成

### 27. Skill 和 MCP 的本质区别是什么？为什么 Skill 的核心指令在客户端读取而不是服务端？

**本质区别：**

| | Skill | MCP |
|--|-------|-----|
| **作用** | 给 LLM 注入专业知识和执行指南（提示词增强） | 给 LLM 提供外部工具调用能力（功能扩展） |
| **形式** | SKILL.md 文本（Markdown + 指令） | 工具定义（JSON Schema）+ 执行进程 |
| **执行位置** | LLM 调用 `skill` 工具 → 客户端读取 SKILL.md → 回传内容 | 客户端通过 MCP 协议调用工具 → 返回结果 |
| **成本** | 主要是 token（SKILL.md 注入上下文） | 工具执行时间 + 网络开销 |

**核心指令在客户端读取的原因**：

- Skill 的核心价值在 SKILL.md 的指令内容，这些内容需要客户端持有（因为 Skill zip 包在客户端解压）
- 客户端读取后回传给服务端，服务端注入 LLM 上下文
- 好处：Skill 的更新只需更新客户端本地文件，服务端不存 SKILL.md 内容
- 安全性：Skill 指令可能包含敏感的业务逻辑，保留在客户端更安全

---

### 28. <available_skills> XML + skill 工具的设计——为什么 Skill prompt 不是常驻 system prompt 而是 LLM 按需调用？

**按需调用的原因：**

- **Token 成本**：SKILL.md 可能很长（几百到几千字），所有 skill 全部常驻 system prompt 会消耗大量上下文窗口（工具目录 token 已占上下文 42%）
- **注意力稀释**：system prompt 越长，LLM 对每条指令的注意力越分散，可能忽略重要约束
- **上下文窗口有限**：Claude 200K 窗口虽然大，但实际可用空间被 system prompt + 历史消息 + tools 定义分摊

**漏用风险**：

- LLM 可能不知道某个场景应该调用 skill
- 需要 `<available_skills>` XML 中的 description 足够有区分度，让 LLM 能正确判断何时调用

**缓解**：通过 `/skill名` 预加载机制，用户在发消息时就明确指定要用哪个 skill，此时 SKILL.md 直接作为首条 user 消息注入。

---

### 29. 用户输入 /skill名 的预加载流程 vs LLM 自己选择 skill，这两条路径分别适用于什么场景？

**预加载流程（/skill名）**：
- 场景：用户明确知道需要哪个 skill（如 `/code-review`）
- 流程：客户端解析 `/skill名` → 放入 `skill_invocations` 字段 → 服务端预加载 SKILL.md → 作为首条 user 消息注入
- 优势：确定性强，不会漏加载，省去 LLM 一次 tool call

**LLM 自己选择**：
- 场景：用户没有明确指定 skill，但任务可能受益于某个 skill（如"帮我检查代码"→ LLM 判断需要 code-review skill）
- 流程：LLM 看到 `<available_skills>` XML → 判断需要某个 skill → 调用 `skill` 工具 → 客户端读取 SKILL.md → 注入上下文
- 优势：用户无需了解有哪些 skill，LLM 自动匹配

---

### 30. Skill 安装时返回 zip 包，客户端解压——为什么不是服务端直接读写？这种"客户端持有核心指令"的设计，在多设备同步时会出现什么问题？

**为什么客户端持有**：

- Skill 的核心指令可能在客户端本地执行（如 shell 脚本、前端组件模板），服务端存储无意义
- 减少服务端存储压力
- 客户端离线时仍可使用已安装的 skill
- 尊重 skill 开发者的知识产权（SKILL.md 不经过服务端）

**多设备同步问题**：

1. **Skill 不同步**：设备 A 安装了 skill，设备 B 没有（需要分别安装）
2. **版本不一致**：设备 A 是 v1.0，设备 B 是 v2.0，导致行为差异
3. **自定义 skill 不可见**：设备 A 创建的自定义 skill 在设备 B 看不到

**缓解**：服务端存储 `user_skills` 表记录安装状态，但 SKILL.md 内容需要设备本地持有。

---

### 31. 如果一个 Skill 的 SKILL.md 在 Install 后被用户手动修改了，系统能检测到吗？如何保证 Skill 执行的幂等性和一致性？

**当前系统不能检测**：SKILL.md 是文件系统上的静态文件，没有 hash 校验机制。

**检测方案**：
- Install 时保存 SKILL.md 的 hash（MD5/SHA256）到 DB
- 每次加载时对比 hash，不一致则推送 warning
- 前端可展示"此 Skill 已被修改，是否重新安装？"

**幂等性和一致性保证**：
- `user_skills` 表记录 version 字段，可以对比版本
- 重装 Skill 时覆盖本地文件（zip 解压）
- 关键 Skill 可以做签名验证

---

### 32. Skill 需要有凭据管理——如果 Skill 需要调用外部 API，怎么处理？

**当前设计未覆盖此场景**。Skill 本身只是指令文本，不包含凭据管理。

**可能的处理方式**：

1. **Skill 不直接调用 API**：Skill 指导 LLM 如何调用 API，LLM 使用已有的 MCP 工具或客户端工具来完成 API 调用
2. **凭据放在环境变量**：Skill 的 SKILL.md 中引用 `${API_KEY}`，客户端在加载 SKILL.md 时做变量替换
3. **MCP 代理**：为需要 API 的 Skill 配套一个 MCP 服务，凭据由 MCP 服务管理（走 ${ENV_VAR} 引用）
4. **Skill metadata 扩展**：在 skill.json 中增加 `env_vars` 字段，声明需要的环境变量，客户端在安装时提示用户配置

---

## 六、记忆模块

### 33. 四种记忆类型的划分逻辑是什么？为什么"什么不保存"的清单和保存清单一样重要？

**四种类型的划分逻辑**：

| 类型 | 内容 | 目的 |
|------|------|------|
| **user** | 用户角色、偏好、技能水平、职责 | 让 AI 了解"在和谁说话" |
| **feedback** | 用户纠正或确认的做法 | 避免重复犯错 |
| **project** | 项目目标、截止日期、架构决策 | 让 AI 了解"当前在做什么" |
| **reference** | 外部系统信息（Slack 频道、Linear 项目等） | 让 AI 知道"去哪里查" |

**"什么不保存"清单的重要性**：

- 防止记忆膨胀：代码模式、git history、文件路径等可从代码库直接获取，不应占据记忆空间
- 记忆质量 > 数量：低质量记忆会稀释 LLM 对关键信息的注意力
- 时效性管理：临时信息、一次性决策不应持久化
- 隐私边界：某些用户偏好不应该被永久记录

---

### 34. MEMORY.md 索引的动态生成——为什么不是一条 DB 记录而是每次查询拼装？200 行上限的原因是什么？

**动态生成而非固化**：

- 记忆会增删改，固化索引需要每次修改时更新，增加写入延迟和一致性风险
- 动态查询天然保证最新
- 索引格式（Markdown list）简单，DB 查询 + 拼接的开销可忽略

**200 行上限的原因**：

- 控制 token 消耗：每个记忆条目一行（约 80-150 chars），200 行 ≈ 20K chars ≈ 5K tokens
- 注意力预算：LLM 对长列表的注意力递减，太多记忆反而降低利用率
- 超出 200 行后截断（按 updated_at DESC），最旧的记忆被排除在索引外但未删除
- 需要时可调用 `load_memory` 加载具体内容

---

### 35. Rules 和 Memory 的核心区别：为什么 Rules 只能用户手动写入、AI 只读？如果 AI 判断某条规则已过时，它能做什么？

**核心区别**：

| | Rules | Memory |
|--|-------|--------|
| **写入者** | 仅用户手动 | 用户 + AI 自动 |
| **优先级** | 最高（强制约束） | 参考建议 |
| **修改权限** | 用户独有 | AI 可 update/delete |
| **稳定性** | 很少变 | 频繁增删改 |
| **语气** | "必须遵守" | "可以参考" |

**为什么 Rules 只能用户写入**：

- Rules 是硬约束，AI 不能修改用户设定的强制规则（安全性）
- 如果 AI 能修改 Rules，恶意 prompt 可能绕过安全约束
- Rules 的优先级需要绝对的稳定性——用户一旦设定了"始终用 TypeScript"，AI 不应擅自修改

**AI 能做什么**：如果 AI 判断规则过时，只能通过对话告知用户"你的这条规则可能已过时，建议更新或删除"，由用户手动操作。

---

### 36. AI 主动写记忆时，如何避免写入错误或重复的信息？

**当前设计的防护**：

1. **write_memory 工具描述中的指引**："保存前检查内容是否已有且一致，避免重复写入"
2. **去重检查**：同名记忆存在时自动更新，60s 内的重复写入直接跳过（`skipped`）
3. **内容一致性**：LLM 需要先在对话中 load_memory 检查已有内容，再判断是否需要写
4. **类型约束**：记忆类型必须是四种之一（user/feedback/project/reference）

**但 LLM 的"检查"能力有限**：

- LLM 可能没有调用 load_memory 就直接 write，导致重复
- LLM 可能产生语义相似但文本不同的重复记忆（"用户喜欢 TypeScript" vs "用户偏好 TypeScript"）

**改进方向**：服务端做语义去重（embedding 相似度比较），而非依赖 LLM 判断。

---

### 37. 如果用户说"不要再记住我的编码风格"，LLM 调 delete_memory 删除了一些记忆——但之前的对话中 LLM 已经用这些记忆做过决策了，会导致什么问题？

**问题**：

- 已做出的决策无法撤销（回复已发送、文件已修改）
- 后续对话失去上下文，LLM 可能重复询问已被记录过的偏好
- 删除的粒度不精确：LLM 可能删除了不该删的记忆（和编码风格相关的其他偏好）

**设计上的考虑**：

- 删除是"从现在开始不再使用"，而非"撤销历史决策"
- 用户指令优先级最高，即使有副作用也应该执行删除
- `protected` 标记可以保护关键记忆不被 AI 删除

---

### 38. protected 标记是什么场景下用的？为什么用户手动删除时不区分 protected，而 AI 调用 delete_memory 时要检查？

**protected 的使用场景**：

- 用户手动创建的重要规则/偏好（如"始终用 TypeScript strict mode"）
- 安全相关的配置
- 用户不希望 AI 意外删除的关键信息

**不对称的权限设计**：

- 用户是记忆的**所有者**，有完全的控制权——手动删除时不做 protected 检查
- AI 是记忆的**管理者**，权限受限——通过 delete_memory 工具调用时必须检查 protected 标记
- 这是一种最小权限原则：AI 是辅助者而非主人，不应能删除用户标记为重要的记忆
- protected 标记是用户对 AI 的"护栏"，而非对自己的限制

---

## 七、可观测性

### 39. 为什么 Tracing / Metrics / Logging 走各自的 API（OTel / structlog）而只有 Stream + Audit 走 EventBus？

**设计原则：关注点分离 + 业界标准 + 性能考虑**

| 管道 | 使用方式 | 原因 |
|------|---------|------|
| **Tracing** | OTel SDK 直调 | 业界标准，需要 Span context 传播，EventBus 无法传递 OTel context |
| **Metrics** | OTel Meter 直调 | 高性能要求（无锁、零分配），EventBus pub/sub 有额外开销 |
| **Logging** | structlog 直调 | 同步输出，需要 trace_id/span_id 注入，独立于业务事件 |
| **Stream** | EventBus | 面向多个 subscriber（不同 session），需要动态订阅/取消 |
| **Audit** | EventBus | 持久化需求，需要异步写入避免阻塞引擎主循环 |

**EventBus 的定位**：轻量级内存 pub/sub，仅用于"一对多通知"场景——Stream（推送给不同客户端）和 Audit（写入 DB）。不适合作为统一的事件管道，因为：
- 同步的 EventBus 会阻塞 emit 方（等待所有 subscriber 处理完）
- 不适合高频数据（如 metrics 的每次 tool call）

---

### 40. "零翻译"的 chunk 同名事件设计——为什么 StreamSubscriber 拿到 AgentEvent.data 能直接当 NDJSON 推送？

**设计核心**：`AgentEvent.data` 的格式和 NDJSON chunk 的格式完全一致。

```python
# 引擎中：_emit 和 _push_chunk 都推送相同结构
await self._emit(AgentEventType.MESSAGE_START, {
    "type": "message.start",
    "message_id": str(msg.id),
    ...
})
# StreamSubscriber 收到后：
await engine._push_chunk(event.data)  # 直接推送，零格式转换
```

**省了什么**：
- 无需事件 → chunk 的格式转换层
- 新增 chunk type 时只需在 AgentEventType 枚举中加一项
- 调试简单：chunk 和 event 的数据结构完全相同

---

### 41. 如果让你们团队在生产环境排查一次 AI 回复错误——"为什么 LLM 改了这个文件？"，你会怎么用可观测性体系来定位问题？

**排查步骤**：

1. **会话回放**：`GET /sessions/{id}/replay` 获取完整的 NDJSON 流，重现用户看到的全过程
2. **Trace 追踪**：用 trace_id 在 Jaeger/Tempo 中查看完整链路——看每轮 LLM 调用的 system prompt、messages、tools
3. **审计日志**：`GET /admin/audit?session_id=X` 查看所有 tool 调用记录，找到 `write_file` / `edit_file` 的参数
4. **结构化日志**：在日志文件中 grep trace_id，查看引擎日志——看 tool dispatch 的参数和结果
5. **Metrics 交叉验证**：看该时间段的 tool_call_total、llm_token_usage，确认操作是否符合预期

**定位思路**：先确认"LLM 调用了哪个工具、什么参数"（审计）→ 再确认"LLM 为什么这么决定"（trace 中的上下文）→ 最后检查"上下文是否有错误"（system prompt、历史消息、工具结果）。

---

### 42. 审计日志"存元数据不存本体"——优点和风险分别是什么？

**优点**：
- 存储成本低（几 KB vs 几 MB）
- 查询快速
- 敏感数据（文件内容、命令输出）不落审计库

**风险**：
- `shell_exec` 的完整输出不在审计表中，事后无法回溯完整命令输出
- 需要关联其他数据源（conversation_history、stream_events）才能完整还原
- 如果关联的外部数据源也被清理，审计不完整

**回溯方式**：
- conversation_history 表存储了 tool_result 的完整内容
- stream_events 表存储了 NDJSON 流（含 client.tool_request 和结果）
- 通过 `session_id + tool_call_id` 跨表关联还原完整上下文

---

### 43. 本地兜底方案在什么量级下会扛不住？你如何决定何时升级到 Grafana 全套？

**扛不住的量级**：

- 日志文件轮转：单日 > 10GB 日志 → 文件解析困难
- metrics snapshot：session 数 > 1000 → JSON dump 过大
- grep trace_id：日志量 > 100MB → grep 太慢

**升级决策**：

- 日志量达到 1GB/天 → 引入 Loki / ELK
- session 并发 > 100 → Prometheus + Grafana dashboard
- 审计日志 > 10 万条 → 引入 ClickHouse / 时序数据库
- 排查效率下降（grep > 5s）→ 必须上集中式日志

**渐进升级路径**：本地文件 → Prometheus + Loki（单机）→ Grafana Agent + Mimir/Loki/Tempo（全套）

---

## 八、Hooks 系统

### 44. Hooks 的责任链模型与 EventBus 的发布-订阅模型的本质区别是什么？为什么 Hooks 要等结果而 EventBus 不用？

**本质区别**：

| | Hooks（责任链） | EventBus（发布-订阅） |
|--|----------------|---------------------|
| **语义** | "我应该怎么做？" | "发生了什么？" |
| **返回值** | 需要（CONTINUE/MODIFY/STOP） | 不需要（fire-and-forget） |
| **顺序** | 有序，前一个的输出是后一个的输入 | 无序，subscriber 独立处理 |
| **中断** | STOP 可中断后续执行 | 不会中断其他 subscriber |
| **用途** | 影响执行流程 | 通知/记录 |

**Hooks 要等结果的原因**：Hook 返回 MODIFY 时会修改输入（如修改 tool args），后续 Hook 和实际执行都依赖这个修改结果。STOP 会阻止执行。这是**同步决策链**。

**EventBus 不等结果的原因**：Stream 和 Audit 是"旁观者"，不改变执行流程，异步通知即可。

---

### 45. Hook 超时或异常时"视为 CONTINUE"——为什么不终止执行？如何保证安全 Hook 不会被跳过？

**为什么视为 CONTINUE**：

- Hook 是**增强层**而非**核心路径**，Hook 的失败不应阻止用户完成工作
- 超时可能因为 Hook 脚本自身的 bug 或环境问题，不是用户消息的问题
- 高可用性优先：Hook 挂了但核心功能仍可用 > Hook 挂了导致整个系统不可用

**保证安全 Hook 不被跳过的措施**：

1. **独立部署**：安全 Hook 应该以高可用方式部署（健康检查、自动重启）
2. **超时配置**：关键安全 Hook 设置更长的 timeout_ms
3. **告警**：Hook 超时/异常时记录日志并触发告警
4. **审计补偿**：即使 Hook 被跳过，审计日志仍记录操作，可以事后审查
5. **deny-rm Hook 的设计**：它是最典型的防御性 Hook，但如果它超时了，系统不会阻止所有 bash 操作（否则无法工作）

---

### 46. Hook 的 MODIFY 返回值——如果两个 Hook 都在改同一个参数，以谁为准？

**责任链模型**：后一个 Hook 的 MODIFY **覆盖**前一个的修改。

```python
modified_input = hook_input
for hook in hooks:
    result = await hook.run(modified_input)  # 前一个的结果传给后一个
    if result.action == MODIFY:
        modified_input = result.modified_input  # 覆盖
```

**排序依赖**：多个 Hook 的 `order` 字段决定了执行顺序。如果两个 Hook 修改同一参数，最后一个生效。

**潜在问题**：如果 Hook A（order=0）设置了 `args.workspace="/safe"`，Hook B（order=1）又改成了 `args.workspace="/unsafe"`——这就产生了冲突。

**缓解**：依赖 Hook 开发者遵守约定（如安全类 Hook 设置更高的 order 以确保最后执行），但当前设计没有硬性保证。

---

### 47. message.before 如果被 Hook STOP 了，消息算处理完成还是处理失败？

**算处理完成**（但被阻断）：消息状态仍是 completed，只是没有执行 LLM 调用。引擎会推送 `error.diagnosis` chunk 告知用户消息被 Hook 阻止。

队列中的下一条消息正常处理（引擎继续 dequeue 循环）。

这样设计的原因是：Hook STOP 是**有意**的拦截（符合用户策略），不是系统故障。消息本身"完成"了它应该完成的事——被策略拦截。

---

### 48. Hooks 安全模型里提到"Hook 子进程不继承敏感环境变量"——如果 Hook 脚本本身需要 API Key 才能工作，它怎么拿到这个配置？

**当前设计中的传递方式**：

1. **stdin 传递**：Hook 脚本通过 stdin 接收 JSON 输入（包含 session_id、user_id、turn 等上下文信息）。可以在 hooks.json 的配置中加入 `env_vars` 字段，由 HookManager 在调用时注入到子进程环境。

2. **配置文件**：Hook 脚本可以自己读取配置文件（如 `~/.config/iwork/hooks/config.json`），从自己的配置文件中读取凭据。

3. **显式注入**：在 hooks.json 的 Hook 定义中增加 `env` 字段：
```json
{
  "name": "teams-notify",
  "on": "tool.after",
  "script": "./hooks/teams-webhook.sh",
  "env": {
    "TEAMS_WEBHOOK_URL": "${TEAMS_WEBHOOK_URL}"
  }
}
```
这些 env 会被显式传递给子进程（白名单方式）。

---

## 九、多 Agent 协作

### 49. 为什么选星型拓扑而不是网格拓扑？星型拓扑的主要瓶颈是什么？

**选择星型拓扑的原因**：

- **简单可控**：主 agent 掌握所有决策权，子 agent 只是任务执行者
- **避免循环通信**：网格拓扑中子 agent 互相通信可能导致无限循环
- **上下文管理简单**：主 agent 汇总所有结果，不需要子 agent 之间同步状态
- **调试友好**：问题追溯只需看主 agent 的决策链

**星型拓扑的瓶颈**：

- **主 agent 单点**：主 agent 是通信枢纽和决策中心，性能和处理能力受限
- **上下文爆炸**：主 agent 需要理解所有子 agent 的输出，信息汇总压力大
- **并行度受限**：主 agent 需要逐个处理子 agent 的结果，无法真正并行
- **主 agent 失败**：主 agent 出错或产生幻觉，所有子 agent 的工作白费

---

### 50. task 工具设计中为什么用单一工具 + agent_name 枚举，而不是每个子 agent 注册一个独立工具？

**单一工具 + 枚举的优势**：

- **工具目录简洁**：1 个 `task` 工具 vs N 个 `task_agent1`、`task_agent2`...
- **动态性**：团队成员变化时只需更新枚举值，不需要增删工具定义
- **LLM 易理解**：`task(agent_name="frontend-dev", prompt="...")` 语义清晰
- **减少 token 消耗**：工具定义数量减少 = system prompt 更短

**每个子 agent 独立工具的劣势**：

- 工具目录膨胀（已经是个问题）
- 增加/删除成员需要修改工具定义
- LLM 选择工具的准确率下降（工具太多）

---

### 51. 为什么要禁止子 agent 再调用 task 工具？如果没有这个限制，可能出现什么问题？

**禁止的原因**：

- **防止无限递归**：子 agent → 孙 agent → 曾孙 agent... 可能创建无限层级的 agent 层级
- **防止循环委派**：agent A 委派 B，B 又委派回 A
- **成本失控**：每个子 agent 独立调用 LLM，递归委派会导致 token 消耗爆炸
- **调试困难**：多层级委派的审计和追溯非常复杂

**实现方式**：子 agent 的 session 创建时 `_disable_task_tool=True`，`_build_task_tool_definition()` 返回 None。

---

### 52. 子 agent 在隔离 session 中独立运行——子 agent 能看到主 agent 的上下文吗？不能的话，主 agent 怎么把关键信息传递给子 agent？

**不能看到**：子 agent 有独立的 session，独立的 conversation_history，独立的 context。

**信息传递机制**：

1. **prompt 参数**：主 agent 通过 `task(agent_name="frontend-dev", prompt="...")` 传递任务描述和上下文
2. **提示词约定**：主 agent 的 system prompt 引导它"在 prompt 中包含足够的背景信息"
3. **工作空间共享**：子 agent 的 workspace 和主 agent 相同，文件系统状态是共享的

**局限性**：主 agent 传递的 prompt 可能遗漏关键约束（如"不要修改特定文件"），导致子 agent 行为偏离预期。

---

### 53. 如果主 agent 让 3 个子 agent 并行处理不同文件，但有一个子 agent 失败了——主 agent 怎么知道？失败后应该重试还是换策略？

**主 agent 怎么知道**：

`task` 工具的返回结果包含 `success: true/false` 和 `error` 信息。`TaskToolHandler.execute()` 返回 dict 给主 agent。

```python
result = {"success": False, "error": str(exc), "agent_name": agent_name}
```

这个结果被注入主 agent 的上下文（作为 tool result），主 agent 可以看到子 agent 的完整输出（含 `<final_output>` 标记）。

**失败后策略**：

当前设计由**主 agent 自行决定**。主 agent 看到失败信息后可能：
- 重试同一个子 agent（如果错误是瞬时的）
- 换另一个子 agent（如果是能力不匹配）
- 自己处理（如果子 agent 无法胜任）
- 向用户报告失败

---

### 54. 这个多 Agent 系统如何处理子 agent 产出冲突？

**当前设计没有自动冲突解决**。

两个子 agent 都改了同一个文件 → 后执行的覆盖先执行的（文件系统级别）。

**可能的改进方向**：

1. **文件锁定**：TaskToolHandler 分配文件给子 agent，加锁防止冲突
2. **主 agent 审核**：子 agent 产出后不直接写文件，而是返回修改建议，主 agent 汇总后统一写入
3. **工作区分区**：给每个子 agent 分配独立的 workspace 子目录
4. **Git 分支**：每个子 agent 在自己的 git 分支上工作，主 agent 负责 merge

---

## 十、遗留问题（技术债务）

### 55. "工具目录膨胀"和"单 Turn 串行执行"——怎么排序优先级？

**优先解决：工具目录膨胀。**

**理由**：

1. **直接影响**：工具定义占上下文 42%，每轮 LLM 调用都浪费 token（成本直接上升）
2. **间接影响**：LLM 的工具选择准确率随工具数量增加而下降（幻觉问题）
3. **解决成本低**：工具筛选/分类可以在 context build 阶段完成，不影响引擎核心逻辑
4. **前置依赖**：单 Turn 并行执行需要准确的依赖检测，而依赖检测本身依赖 LLM 对工具的理解。如果工具目录膨胀导致 LLM 工具选择错误，并行执行的问题会更严重

**单 Turn 串行执行的优先级较低**，因为：
- 需要改变引擎核心的 turn loop 结构（风险大）
- 依赖检测是未解决的研究问题
- 当前通过 max_turns=25 和时间预算控制延迟

---

### 56. "工具目录膨胀"的 7 种潜在解法中，你倾向哪种？如果只能用一种、且不能增加一次额外的 LLM 调用，你选哪个？

**倾向：按场景/模式做工具预筛选。**

在 context build 阶段，根据 mode + scene_mode 对工具做预筛选：

- Ask 模式：不需要任何工具（已实现）
- Plan turn 0：仅 `plan_question`
- Build code 模式：排除 office 场景的 MCP 工具
- 根据 workspace 内容：如果项目中没有 Python 文件，排除 Python 相关工具

**不增加 LLM 调用的最佳选择**：**工具描述的语义压缩**。

将相似工具的 description 合并，如多个 MCP 服务的 `search` 合并为一个 `search` 工具定义，参数中通过 `provider` 枚举区分来源。这样工具数量直接减少 30-50%，不增加任何 LLM 调用。

---

### 57. "单 Turn 内工具串行执行"中最难的是依赖检测——你能想到什么启发式规则来判断两个工具调用是否可以并发？

**启发式规则**：

1. **文件依赖**：如果 tool A 创建/修改了文件 X，tool B 读取文件 X → B 依赖 A（不可并发）
2. **路径不相交**：两个工具操作的文件路径完全无交集 → 大概率可并发
3. **工具类型互斥**：`glob` + `grep` 是只读操作 → 互相独立，可并发；`write_file` + `read_file` 同一文件 → 有依赖
4. **工具类别分组**：只读工具（read/glob/grep）天然可并发；写工具（write/edit/bash）相对其他工具不可并发
5. **参数语义分析**：如果 tool B 的参数引用了 tool A 的输出 → 依赖关系（如 `bash("cat result.txt")` 依赖前一步的 `write_file("result.txt")`）

**保守策略**：默认串行，只对标记为"无副作用"的只读工具做自动并发。

---

### 58. 假设 v0.1 上线一周的数据——你会优先解决哪个问题？为什么？

**数据回顾**：
- 单 turn 平均 tool_use = 4.2
- 用户感知延迟 = 平均 14s
- 工具目录 token 占上下文 42%
- 幻觉工具名频率 = 每 100 条消息 2 次

**优先：工具目录膨胀（42% 上下文）**

**理由**：

1. **ROI 最高**：工具目录占 42% → 如果压缩到 20%，每轮 LLM 调用节省 ~20% token 成本，且 LLM 注意力更集中
2. **延迟改善**：更少的工具定义 → LLM 处理更快 → 每轮 LLM 调用时间缩短 → 14s 延迟的一部分可以被改善
3. **幻觉降低**：工具数量减少 → LLM 选择工具更准确 → 2 次/100 条幻觉可能降低
4. **影响面大**：工具目录膨胀影响所有模式的每一轮 LLM 调用，14s 延迟和幻觉问题可能部分来源于此

**不优先解决延迟的原因**：14s 中大部分是 LLM API 延迟（不可控）+ 串行工具执行（4.2 个工具），压缩工具目录对延迟有改善但不是根本解决。

**不优先解决幻觉的原因**：2 次/100 条虽然需要关注，但频率相对较低，且工具目录压缩后可能自然改善。

---

## 十一、综合设计能力

### 59. 命令行注入防御——除了 Hook 层防御外，系统架构层面有哪些手段来防御工具滥用？

**架构层面的防御手段**：

1. **权限分层**：`PermissionChecker` 对文件路径做 blocklist 检查，敏感路径（如 `/etc/passwd`）直接拒绝
2. **模式隔离**：Ask 模式根本不给 LLM 传 tools（`tool_choice="none"`），Plan 模式 turn 0 只有 `plan_question`
3. **Build 模式每步确认**：每个 tool_use 都需要用户确认才执行，用户是最后一道防线
4. **工作空间沙箱**：`workspace` 参数定义了沙箱边界，客户端应限制工具只能操作 workspace 内的文件
5. **重复操作检测**：连续 3 次相同工具+参数 → 注入警告，防止自动化攻击
6. **安全 system prompt**：context.py 中注入安全约束（"不要执行危险命令"、"不要读取敏感文件"）
7. **审计日志**：所有工具调用被记录，支持事后追溯和异常检测
8. **Hook 链**：deny-rm.sh 等 Hook 对危险命令（rm -rf、sudo 等）做模式匹配拦截

---

### 60. 弹性设计——如果所有外部 LLM API 提供商同时挂了，你的系统还能提供什么价值？

**仍然可用的功能**：

1. **会话管理**：查看历史会话、消息、工具调用记录
2. **会话回放**：`GET /sessions/{id}/replay` 查看完整的历史交互
3. **记忆管理**：浏览、编辑、删除已有的记忆和规则
4. **Skill 管理**：浏览已安装的 skill、安装新 skill（Skill Hub 可能仍可用）
5. **MCP 管理**：配置 MCP 服务（MCP 服务的本地进程可能仍可运行）
6. **离线排队**：消息可以继续入队（存储在 DB），API 恢复后自动处理

**Fallback 链设计**：

1. **多 Provider**：settings 支持切换 provider（DeepSeek ↔ Anthropic），config 中可配置多个 API key
2. **重试机制**：3 次指数退避重试，流中断重试 1 次
3. **降级通知**：推送 `system.status` + `message.error` 告知用户 API 不可用
4. **队列保持**：处理失败的消息保持 `pending` 状态，API 恢复后可手动或自动重试

---

### 61. 如果让你重做一次——有没有哪个决策你觉得自己做错了？

**几个"如果有多 2 周时间，我会改"的设计决策**：

1. **工具目录膨胀问题应该在设计初期就解决**：当前方案在会话创建时注册所有工具、之后不可变，但缺少工具筛选机制。应该在 context build 阶段加入工具相关性评分，只传相关工具给 LLM。

2. **StreamBuffer 500 条上限太低**：应该做成可配置的、或基于数据量（如 10MB）而非条数的限制。对于高频 agent.thinking 增量的场景，500 条可能几分钟就满了。

3. **Skill 和 MCP 的统一抽象**：当前 Skill 和 MCP 是完全独立的两个系统（不同的 API、不同的存储、不同的安装流程），但它们本质上都是"扩展 LLM 能力"的方式。应该抽象出统一的 Plugin 接口层。

4. **消息级配置独立存储是对的，但可以更进一步**：应该存储 context hash（system prompt + tools 的 hash），用于判断同一会话中是否可以用缓存结果。

5. **sync_waiter 的设计过于简单**：每个 session 只有一个 Event，同时只能有一个等待点。这意味着 Plan 确认和 plan.question 不能同时等待。应该改成支持多 request_id 的等待注册表。

---

> **备考建议**：重点掌握第 1、8、12、15、17、28、39、44、55-58 题，这些最能展示对系统深度的思考。结合实际源码阅读 `query_loop.py`、`context.py`、`mcp_runtime.py` 加深理解。
