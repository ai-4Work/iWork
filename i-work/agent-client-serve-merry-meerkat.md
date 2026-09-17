# iWork Agent 技术详细设计

> 基于 `docs/requirements.md` v0.1，Client-Server 模式
>
> Client: Electron | Server: Python FastAPI | 通信: MCP Streamable HTTP | 存储: PostgreSQL + pgvector

---

## 目录

- [0. 架构总览与设计合理性](#0-架构总览与设计合理性)
- [1. Query Loop 引擎](#1-query-loop-引擎)
  - [1.1 架构概览](#11-架构概览)
  - [1.2 状态机](#12-状态机)
  - [1.3 Per-Message 核心循环](#13-per-message-核心循环)
  - [1.4 三种模式的行为汇总](#14-三种模式的行为汇总)
  - [1.5 Plan 模式子流程](#15-plan-模式子流程)
  - [1.6 Build 模式子流程](#16-build-模式子流程)
  - [1.7 消息排队流程](#17-消息排队流程)
  - [1.8 异常处理](#18-异常处理)
    - [1.8.1 异常全景图（按 Loop 执行链路排列）](#181-异常全景图按-loop-执行链路排列)
    - [1.8.2 四级处理策略](#182-四级处理策略)
    - [1.8.3 重试与退避实现](#183-重试与退避实现)
    - [1.8.4 重复操作检测](#184-重复操作检测)
    - [1.8.5 流连接断开时的缓冲回放](#185-流连接断开时的缓冲回放)
    - [1.8.6 引擎恢复（服务器重启后）](#186-引擎恢复服务器重启后)
  - [1.9 前后端主对话接口完整定义](#19-前后端主对话接口完整定义)
    - [1.9.1 接口总览](#191-接口总览)
    - [1.9.2 接口详细定义](#192-接口详细定义)
      - [工具分类：客户端工具 vs 客户端 MCP](#工具分类客户端工具-vs-客户端-mcp)
      - [Plan 模式数据块详解](#plan-模式数据块详解)
      - [Build 模式数据块详解](#build-模式数据块详解)
    - [客户端处理汇总](#客户端处理汇总)
    - [1.9.3 错误码参考](#193-错误码参考)
    - [1.9.4 典型交互时序](#194-典型交互时序)
- [2. MCP 工具集成](#2-mcp-工具集成)
  - [2.1 架构概览](#21-架构概览)
  - [2.2 MCP 协议基础](#22-mcp-协议基础)
  - [2.3 MCP 服务生命周期](#23-mcp-服务生命周期)
  - [2.4 MCP 服务配置](#24-mcp-服务配置)
    - [2.4.1 配置文件格式](#241-配置文件格式)
    - [2.4.2 配置层级：会话级 vs 消息级](#242-配置层级会话级-vs-消息级)
    - [2.4.3 凭据管理](#243-凭据管理)
  - [2.5 工具发现与注册](#25-工具发现与注册)
    - [2.5.1 工具列表拉取](#251-工具列表拉取)
    - [2.5.2 工具命名规则](#252-工具命名规则)
    - [2.5.3 合并到 LLM 上下文](#253-合并到-llm-上下文)
  - [2.6 MCP 工具执行流程](#26-mcp-工具执行流程)
  - [2.7 错误处理](#27-错误处理)
    - [2.7.1 MCP 错误分类](#271-mcp-错误分类)
    - [2.7.2 MCP 三级异常处理策略](#272-mcp-三级异常处理策略)
    - [2.7.3 MCP 相关的 system.status 通知](#273-mcp-相关的-systemstatus-通知)
  - [2.8 MCP 服务管理（Hub）](#28-mcp-服务管理hub)
    - [2.8.1 Hub 数据来源](#281-hub-数据来源)
    - [2.8.2 安装 / 卸载流程](#282-安装-卸载流程)
    - [2.8.3 与主对话流程的联通](#283-与主对话流程的联通)
  - [2.9 MCP 查询接口定义](#29-mcp-查询接口定义)
  - [2.10 与 Query Loop 引擎的集成点总结](#210-与-query-loop-引擎的集成点总结)
- [3. Skill 集成](#3-skill-集成)
  - [3.1 架构概览](#31-架构概览)
  - [3.2 Skill 格式与结构](#32-skill-格式与结构)
  - [3.3 Skill 生命周期](#33-skill-生命周期)
  - [3.4 Skill 配置](#34-skill-配置)
    - [3.4.1 配置文件格式](#341-配置文件格式)
    - [3.4.2 配置层级变化](#342-配置层级变化)
  - [3.5 Skill 发现与注册](#35-skill-发现与注册)
    - [3.5.1 启动加载流程](#351-启动加载流程)
    - [3.5.2 Skill ID 命名规则](#352-skill-id-命名规则)
    - [3.5.3 合并到 LLM 上下文](#353-合并到-llm-上下文)
  - [3.6 Skill 执行流程（Tool-based 按需加载）](#36-skill-执行流程tool-based-按需加载)
  - [3.7 错误处理](#37-错误处理)
  - [3.8 Skill 管理（Hub）](#38-skill-管理hub)
    - [3.8.1 Hub 数据来源](#381-hub-数据来源)
    - [3.8.2 安装 / 卸载流程](#382-安装-卸载流程)
    - [3.8.3 自定义 Skill](#383-自定义-skill)
    - [3.8.4 与主对话流程的联通](#384-与主对话流程的联通)
  - [3.9 Skill 查询接口定义](#39-skill-查询接口定义)
  - [3.10 与 Query Loop 引擎的集成点总结](#310-与-query-loop-引擎的集成点总结)
- [4. 数据库与种子数据运维](#4-数据库与种子数据运维)
  - [4.1 种子数据重新初始化](#41-种子数据重新初始化)
  - [4.2 Schema 重建](#42-schema-重建)
  - [4.3 手动触发种子脚本](#43-手动触发种子脚本)
- [5. 记忆模块](#5-记忆模块)
  - [5.1 架构概览](#51-架构概览)
  - [5.2 数据模型](#52-数据模型)
  - [5.3 MEMORY.md 索引生成](#53-memorymd-索引生成)
  - [5.4 Rules 表](#54-rules-表)
  - [5.5 工具定义](#55-工具定义)
  - [5.6 Query Loop 注入](#56-query-loop-注入)
  - [5.7 记忆生命周期](#57-记忆生命周期)
  - [5.8 异常处理](#58-异常处理)
  - [5.9 Rules 管理 API](#59-rules-管理-api)
  - [5.10 Memory 管理 API](#510-memory-管理-api)
  - [5.11 与 Skill 模块的设计对比](#511-与-skill-模块的设计对比)
- [6. 遗留问题](#6-遗留问题)
  - [6.1 工具目录膨胀导致 LLM 稳定性下降](#61-工具目录膨胀导致-llm-稳定性下降)
  - [6.2 单 Turn 内工具串行执行](#62-单-turn-内工具串行执行)
  - [6.3 Skill 维护管理：版本升级、测试与质量保障](#63-skill-维护管理版本升级测试与质量保障)
  - [6.4 会话终止 / 系统异常时的内部状态收尾](#64-会话终止--系统异常时的内部状态收尾)
- [7. 可观测性](#7-可观测性)
  - [7.1 架构概览](#71-架构概览)
    - [7.1.1 三种可观测性管道](#711-三种可观测性管道)
    - [7.1.2 AgentEvent 类型定义（仅 Stream + Audit 使用）](#712-agentevent-类型定义仅-stream-audit-使用)
      - [7.1.2.1 与 NDJSON Chunk 的关系](#7121-与-ndjson-chunk-的关系)
      - [7.1.2.2 与 Hooks 的关系](#7122-与-hooks-的关系)
    - [7.1.3 EventBus 接口](#713-eventbus-接口)
    - [7.1.4 引擎侧用法](#714-引擎侧用法)
    - [7.1.5 系统启动时的订阅注册](#715-系统启动时的订阅注册)
    - [7.1.6 两层通道](#716-两层通道)
    - [7.1.7 存储方案](#717-存储方案)
    - [7.1.8 事件发布与订阅](#718-事件发布与订阅)
  - [7.2 链路追踪 (Tracing)](#72-链路追踪-tracing)
    - [7.2.1 Trace 结构](#721-trace-结构)
    - [7.2.2 Span 属性定义](#722-span-属性定义)
    - [7.2.3 TracerProvider 初始化](#723-tracerprovider-初始化)
    - [7.2.4 Context 传播](#724-context-传播)
  - [7.3 指标 (Metrics)](#73-指标-metrics)
    - [7.3.1 指标清单](#731-指标清单)
    - [7.3.2 Meter 定义与引擎直调](#732-meter-定义与引擎直调)
    - [7.3.3 Grafana 告警规则](#733-grafana-告警规则)
  - [7.4 结构化日志](#74-结构化日志)
    - [7.4.1 引擎直调](#741-引擎直调)
  - [7.5 审计日志](#75-审计日志)
    - [7.5.1 数据模型](#751-数据模型)
    - [7.5.2 审计动作类型](#752-审计动作类型)
    - [7.5.3 审计查询 API](#753-审计查询-api)
  - [7.6 用户侧可观测性](#76-用户侧可观测性)
    - [7.6.1 实时执行步骤](#761-实时执行步骤)
    - [7.6.2 Token 用量与成本追踪](#762-token-用量与成本追踪)
    - [7.6.3 错误诊断卡片](#763-错误诊断卡片)
    - [7.6.4 会话回放](#764-会话回放)
- [8. Hooks 系统](#8-hooks-系统)
  - [8.1 架构概览](#81-架构概览)
  - [8.2 拦截点](#82-拦截点)
  - [8.3 Hook 上下文与返回值](#83-hook-上下文与返回值)
  - [8.4 责任链执行模型](#84-责任链执行模型)
  - [8.5 配置](#85-配置)
  - [8.6 引擎集成](#86-引擎集成)
  - [8.7 安全模型](#87-安全模型)
  - [8.8 实现路径](#88-实现路径)
  - [8.9 常用 Hook 示例](#89-常用-hook-示例)
    - [8.9.1 `tool.before` — 拦截危险命令](#891-toolbefore-拦截危险命令)
    - [8.9.2 `tool.before` — 限制工作空间外的文件访问](#892-toolbefore-限制工作空间外的文件访问)
    - [8.9.3 `tool.after` — 审计文件修改](#893-toolafter-审计文件修改)
    - [8.9.4 `llm.before` — 注入项目上下文](#894-llmbefore-注入项目上下文)
    - [8.9.5 `llm.after` — Token 用量记录](#895-llmafter-token-用量记录)
    - [8.9.6 `message.before` — 敏感信息脱敏](#896-messagebefore-敏感信息脱敏)
    - [8.9.7 `message.after` — Webhook 通知](#897-messageafter-webhook-通知)
    - [8.9.8 `llm.before` — 强制 Think 模式](#898-llmbefore-强制-think-模式)
  - [Structlog 日志标签](#structlog-日志标签)
- [9. 多 Agent 协作](#9-多-agent-协作)
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
- [10. 上下文管理](#10-上下文管理)
  - [10.1 问题定义](#101-问题定义)
  - [10.2 上下文三分类处理总览](#102-上下文三分类处理总览)
  - [10.3 第一类：System Prompt — 核心约束钉死，其余可压缩](#103-第一类system-prompt-核心约束钉死其余可压缩)
  - [10.4 第二类：配置注入 — 轻量索引常驻，完整内容按需加载](#104-第二类配置注入-轻量索引常驻完整内容按需加载)
  - [10.5 第三类：对话历史 — 分层压缩 + 外部记忆](#105-第三类对话历史-分层压缩-外部记忆)
  - [10.6 与 Query Loop 引擎的集成点](#106-与-query-loop-引擎的集成点)
- [11. 成本控制](#11-成本控制)
  - [11.1 问题定义](#111-问题定义)
  - [11.2 成本构成与三大方向](#112-成本构成与三大方向)
  - [11.3 成本估算器（Cost Estimator）](#113-成本估算器cost-estimator)
  - [11.4 记账引擎（Cost Ledger）](#114-记账引擎cost-ledger)
  - [11.5 预算模型（Budget Model）](#115-预算模型budget-model)
  - [11.6 超限行为：告警、降级、阻断](#116-超限行为告警降级阻断)
  - [11.7 模型路由（讨论，不落地）](#117-模型路由讨论不落地)
  - [11.8 与现有模块的关系](#118-与现有模块的关系)
  - [11.9 实现路径](#119-实现路径)
- [12. Agent 系统异常处理全景](#12-agent-系统异常处理全景)
  - [12.1 问题定义：Agent 异常处理与传统软件的本质差异](#121-问题定义agent-异常处理与传统软件的本质差异)
  - [12.2 全景视角：外部资源环境 × 内部循环系统](#122-全景视角外部资源环境-内部循环系统)
  - [12.3 视角一：外部资源环境（故障从哪来）](#123-视角一外部资源环境故障从哪来)
    - [12.3.1 LLM 连接层](#1231-llm-连接层)
    - [12.3.2 内置工具与客户端工具](#1232-内置工具与客户端工具)
    - [12.3.3 MCP 服务](#1233-mcp-服务)
    - [12.3.4 Skill](#1234-skill)
    - [12.3.5 记忆库与外部系统](#1235-记忆库与外部系统)
    - [12.3.6 多 Agent（子 agent 作为外部执行单元）](#1236-多-agent子-agent-作为外部执行单元)
    - [12.3.7 外部资源的统一应对框架](#1237-外部资源的统一应对框架)
  - [12.4 视角二：内部循环系统（故障如何在循环中传播）](#124-视角二内部循环系统故障如何在循环中传播)
    - [12.4.1 循环机理总览：推理 → 行动 → 观察](#1241-循环机理总览推理-行动-观察)
    - [12.4.2 推理 Reason 阶段（LLM 语义层）](#1242-推理-reason-阶段llm-语义层)
    - [12.4.3 行动 Act 阶段（工具调用，衔接外部资源）](#1243-行动-act-阶段工具调用衔接外部资源)
    - [12.4.4 观察 Observe 阶段（结果解析与注入）](#1244-观察-observe-阶段结果解析与注入)
    - [12.4.5 循环治理：死循环与空转检测、轮次与超时、暂停与终止](#1245-循环治理死循环与空转检测轮次与超时暂停与终止)
    - [12.4.6 终止收尾与恢复（用户取消 vs 系统异常）](#1246-终止收尾与恢复用户取消-vs-系统异常)
  - [12.5 横切机制（贯穿两个视角）](#125-横切机制贯穿两个视角)
    - [12.5.1 安全护栏](#1251-安全护栏)
    - [12.5.2 成本预算与熔断](#1252-成本预算与熔断)
    - [12.5.3 人类在环（HITL）](#1253-人类在环hitl)
    - [12.5.4 可观测闭环（异常即数据）](#1254-可观测闭环异常即数据)
  - [12.6 现状盘点与缺口清单](#126-现状盘点与缺口清单)
- [13. 权限控制](#13-权限控制)
  - [13.1 问题定义](#131-问题定义)
  - [13.2 权限模型：主体 × 客体 × 动作](#132-权限模型主体-客体-动作)
  - [13.3 策略模型（Policy Engine）](#133-策略模型policy-engine)
  - [13.4 决策流程（ALLOW、DENY、CONFIRM）](#134-决策流程allowdenyconfirm)
  - [13.5 人工确认通道（客户端即时确认）](#135-人工确认通道客户端即时确认)
  - [13.6 权限数据模型](#136-权限数据模型)
  - [13.7 引擎接线（集成点）](#137-引擎接线集成点)
  - [13.8 与现有模块的关系](#138-与现有模块的关系)
  - [13.9 实现路径](#139-实现路径)

---

<a id="0-架构总览与设计合理性"></a>

## 0. 架构总览与设计合理性

> 本章回答"为什么采用 客户端执行 + 服务端推理 的 client-server 形态"，以及这套设计对企业内网场景的价值与边界。作为全文的设计前提。

### 0.1 架构形态：治理集中，执行本地

系统按「资源 vs 决策」切分职责，而非按功能模块切分：

| 归属 | 承载 | 为什么 |
|---|---|---|
| **客户端（Electron）** | 工具执行（bash/文件/glob/grep）、客户端 MCP、skill 脚本 | 资源（工作区文件、本地进程、凭证）在客户端，执行必须贴近资源 |
| **服务端（FastAPI）** | query loop、上下文管理、记忆管理、skill/mcp 注册、多 agent 编排、hooks、权限 | 全部是"决策与策略"，需要全局视图 |

客户端本质是"手"（执行 + 渲染），服务端是"大脑 + 政策制定者"。

```
┌───────────────────────────┐          ┌───────────────────────────┐
│       服务端（决策与治理）    │          │    客户端（执行与渲染）      │
│  query loop · 上下文 · 记忆  │          │  bash / 文件 / glob / grep │
│  skill/mcp · 多agent · hooks│  NDJSON  │  MCP · skill 脚本          │
│  权限 · 审计 · 成本          │◄────────►│  （资源在工作区，本地执行）   │
└───────────────────────────┘ 工具往返  └───────────────────────────┘
```

### 0.2 为什么这样切（三层论证）

1. **工具在客户端 —— 资源位置决定执行位置**。工具的语义是"操作资源"，文件、进程、凭证全在员工本机，工具必须在客户端；推理循环是"无状态决策器"，可放任何位置。这条边界由资源位置决定，是硬约束（data gravity：计算靠近数据）。
2. **逻辑在服务端 —— 集中管理价值**。LLM key 集中（不发给每个客户端）、状态集中（会话/记忆/审计可管理可续传）、策略集中（权限/成本/安全一个点落地）、客户端薄（换平台不换核心）。
3. **内网适配 —— 通信成本可忽略**。内网高带宽低延迟，每次工具调用多一跳往返仅数毫秒；该形态隐含前提是可靠低延迟的内网，企业内网恰好满足。

### 0.3 企业价值四维：可管控 / 可审计 / 可量化 / 可复用

统一逻辑：**每一项都要求一个全局控制点，全局控制点天然只有服务端一个位置**。

| 维度 | 记忆词 | 一句话 | 涵盖 | 典型例子 |
|---|---|---|---|---|
| **可管控** | 管人 | 谁能用、能用什么 | 多用户、SSO/RBAC、审批式发布、统一配置 | skill/MCP 需审批才进入员工可用列表（DBA 审核权限范围）；财务可调支付工具、研发不可 |
| **可审计** | 防外 | 干了什么、出不出得去 | 全链路审计、DLP 出网闸门、敏感内容外发阻断 | 事故追溯：谁→几点→哪个 agent→调了哪个工具→改了哪几行→当时 prompt；LLM 出网请求全留痕 |
| **可量化** | 量化 | 花多少、稳不稳、谁在用 | 成本/用量/行为/性能四类度量 | 月底 token 成本按部门分摊；某 skill 调用量异常；工具失败率驱动优化；LLM 延迟 p95 |
| **可复用** | 沉淀 | 沉淀什么、怎么共享 | 企业知识库/记忆、skill 资产化、配置版本灰度 | 内部代码规范写成 rule 全员自动遵守；资深工程师沉淀的 skill 审批后全公司复用，不随离职丢失 |

### 0.4 与主流形态的对比

| 形态 | 划分 | 为什么不选 |
|---|---|---|
| **Claude Code 形态**（全本地） | loop+工具+记忆全在客户端 | 单机工具：无法集中管控、无多用户、无审计、无成本核算 |
| **纯 Server-side Agents**（全服务端） | 工具也在服务端 | 文件必须留在员工本机（数据主权/合规），工具搬不上来 |
| **iWork 形态**（本系统） | **治理集中 + 执行本地** | 在"集中管控"与"数据合规"两个硬约束下唯一站得住的折中 |

### 0.5 权衡与边界条件

**权衡**：
- 服务端是唯一瓶颈与单点（所有推理/记忆/LLM 调用压一处；服务端挂了全挂）
- 客户端零兜底（断网即全瘫，无本地降级）
- 推理侧敏感信息集中（文件不上传是合规优点，但对话/记忆/审计在服务端，安全责任全压 server）

**边界（这些场景下形态应翻转）**：
- 公网/移动/弱网 → 循环挪到客户端，服务端退化为同步+存储层
- 离线需求 → 客户端需本地推理
- 企业要求集中式工作区（代码统一放 server）→ 工具搬服务端，架构反过来

### 0.6 演进方向（对应企业就绪缺口）

- 认证与多用户隔离、会话生命周期与并发上限（防资源线性堆积）
- 工具回传路径加固（request_id 精确匹配、结果大小截断防上下文膨胀）
- 单 turn 多工具批量执行 + 批量回传（减往返）
- 断线重连 + 引擎状态恢复
- 多 agent 落地（docs/多agent协作设计.md 8 步方案）

---

<a id="1-query-loop-引擎"></a>

## 1. Query Loop 引擎

### 1.1 架构概览

QueryLoopEngine 是 Server 端每个会话的**长生命周期、单实例协程**，内部维护消息 FIFO 队列，逐条出队执行，单条消息内部按 turn 循环调用 LLM，直到达到终止条件或上限。

```
┌─ QueryLoopEngine (会话级单实例, 生命周期=会话) ─────────────┐
│                                                             │
│   MessageQueue (FIFO, 上限 10)                               │
│   ┌──────┐ ┌──────┐ ┌──────┐                               │
│   │ msg3 │ │ msg2 │ │ msg1 │ ← 队首                         │
│   └──────┘ └──────┘ └──────┘                               │
│        ↑                          ↓                         │
│   用户发送消息入队           dequeue → 开始执行               │
│   (loop 运行中不中断)         完成后自动取下一条               │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │                Per-Message Loop                      │  │
│   │        turn 0..max_turns(25), timeout(300s)          │  │
│   │                                                      │  │
│   │  context → llm.stream() → parse → dispatch → append  │  │
│   │                                                      │  │
│   │  ┌──────────┐  ┌─────────────┐  ┌───────────────┐   │  │
│   │  │StepPlanner│  │ToolDispatcher│  │ResponseBuilder│   │  │
│   │  └──────────┘  └─────────────┘  └───────────────┘   │  │
│   └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 状态机

```
                    ┌──────────────────────────────────────┐
                    │           SESSION_ACTIVE               │
                    │                                        │
    ┌──────┐ 入队    │  ┌──────────┐    ┌──────────────┐    │
    │ IDLE │───────►│  │PROCESSING│───►│WAITING_SYNC  │    │
    └──────┘        │  └──────────┘    └──────┬───────┘    │
       ↑            │       ↑                 │            │
       │ 队空,无待   │       │ 确认/跳过/      │            │
       │ 处理消息    │       │ 工具结果回传     │            │
       │            │       │                 │            │
       │            │  ┌────┴────┐            │            │
       │            │  │ 有下一条 │◄───────────┘            │
       │            │  │ 消息     │                         │
       │            │  └─────────┘                         │
       └────────────┴───────────────────────────────────────┘
```

| 状态 | 说明 |
|------|------|
| **IDLE** | 消息队列为空，等待新消息。空闲超时（默认 30 分钟）后会话可归档 |
| **PROCESSING** | 队首消息出队，执行 per-message loop（内部 turn 0..25） |
| **WAITING_SYNC** | 暂停等待：Plan 模式等用户确认计划或回答问题 / Build 模式每步等确认 / Client 工具执行等结果回传 |

重试路径：工具执行失败 → 错误计数累加 → < 3 次重试，≥ 3 次终止并反馈用户 → `TERMINAL`

### 1.3 Per-Message 核心循环

```python
# 伪代码
async def run_message(session_id: str, message: Message):
    turn = 0
    terminal = False
    mode = await session_manager.get_mode(session_id)  # ask / plan / build
    plan_confirmed = False
    plan_text_buffer = ""  # Plan 模式 turn 0 累积 LLM 输出的计划文本
    build_step = 0

    while turn < MAX_TURNS and not terminal:
        # 1. 构建上下文（含 mode 对应的 system prompt）
        ctx = await context_manager.build(session_id, turn, mode)

        # 2. LLM 流式调用
        # 构建 thinking 参数（Claude API extended thinking）
        thinking_budget = message.thinking_budget or 0
        thinking = {"type": "enabled", "budget_tokens": thinking_budget} \
            if thinking_budget >= 1024 else None

        response = await llm.stream(
            messages=ctx.messages,
            tools=ctx.available_tools if mode != "ask" else None,
            tool_choice="none" if mode == "ask" else "auto",
            system=ctx.system_prompt,
            thinking=thinking,
        )

        # 3. 逐数据块解析
        async for chunk in response:
            # ── 文本 + 思考：三种模式都直接流式推客户端 ──
            if chunk.type in ("thinking", "text"):
                await self._push_chunk({"type": f"agent.{chunk.type}", "delta": chunk.delta})
                if mode == "plan" and not plan_confirmed and chunk.type == "text":
                    plan_text_buffer += chunk.delta  # Plan 模式累积计划文本

            # ── Plan 模式专项：LLM 向用户提问 ──
            elif chunk.type == "tool_use" and chunk.tool_name == "plan_question":
                # plan_question 是一种特殊 tool，LLM 用它向用户发起选择题或追问
                # 前端渲染为选项卡片或输入框，用户回答后注入上下文
                await self._push_chunk({
                    "type": "plan.question",
                    "message_id": message.id,
                    "question": chunk.tool_input.get("question"),
                    "options": chunk.tool_input.get("options"),
                    "input_type": chunk.tool_input.get("input_type", "select"),  # select | text
                })
                answer = await self.sync_waiter.wait(self.session.id, timeout=300)
                if answer is None:
                    await context_manager.append_text(
                        session_id, "[用户未回应此问题，请跳过并继续]"
                    )
                else:
                    await context_manager.append_user_response(session_id, chunk, answer)
                plan_reask = True
                break  # 退出当前 LLM 流，下次循环用新上下文（含答案）重新调用

            # ── 工具调用：三种模式行为不同 ──
            elif chunk.type == "tool_use":

                # === Ask 模式：不应出现，忽略 ===
                if mode == "ask":
                    log.warning(f"Ask mode got tool_use, skipping")

                # === Plan 模式 ===
                elif mode == "plan":
                    if not plan_confirmed:
                        # turn 0: 把第一个 tool_use 当"计划生成"处理
                        # LLM 在 Plan 模式下先输出 plan 文本，不应直接有 tool_use
                        # 走到这里说明 LLM 跳过了 plan 输出，做兜底处理
                        await self._push_chunk({
                            "type": "plan.generated",
                            "plan_text": "（LLM 直接发出了工具调用，跳过计划阶段）"
                        })
                        confirmed = await self.sync_waiter.wait(self.session.id, timeout=300)
                        if not confirmed:
                            terminal = True
                            break
                        plan_confirmed = True

                    # 计划已确认，走正常工具执行流程（不暂停）
                    await _execute_tool_chunk(session_id, chunk)

                # === Build 模式：每步暂停确认 ===
                elif mode == "build":
                    # 权限检查
                    tool_name = chunk.tool_name or "unknown"
                    tool_call_id = chunk.tool_call_id or ""
                    if not await self._check_tool_permission(msg, tool_name, chunk.tool_input or {}, tool_call_id, turn):
                        continue
                    # 推送待确认步骤
                    build_step += 1
                    await self._push_chunk({
                        "type": "`client.tool_request`",
                        "tool_name": chunk.tool_name,
                        "input": chunk.tool_input,
                        "step": build_step
                    })
                    decision = await self.sync_waiter.wait(self.session.id, timeout=300)
                    if decision == "confirm":
                        await _execute_tool_chunk(session_id, chunk)
                    elif decision == "skip":
                        await context_manager.append_skip_feedback(session_id, chunk)
                    elif decision == "abort":
                        terminal = True
                        break

            # ── plan_question 拿到答案后，用新上下文重启 turn ──
            if plan_reask:
                continue

            # ── 推送实时 token 统计 ──
            await self._push_chunk({
                "type": "token.usage",
                "message_id": message.id,
                "tokens_in": message.tokens_in,
                "tokens_out": message.tokens_out,
            })

            # 4. Plan 模式 turn 0 完成：推送 plan.generated，等待用户确认
        if mode == "plan" and not plan_confirmed:
            await self._push_chunk({
                "type": "plan.generated",
                "plan_text": plan_text_buffer  # turn 0 中通过 text 数据块累积的计划文本
            })
            confirmed = await self.sync_waiter.wait(self.session.id, timeout=300)
            if not confirmed:
                terminal = True
            else:
                plan_confirmed = True
                continue  # 跳过 turn+=1，直接进入下一轮（带 tools 的执行轮）

        # 5. 终止判断
        if response.stop_reason == "end_turn":
            terminal = True
        elif mode == "ask":
            terminal = True  # Ask 模式一轮就停

        # 超时 / 轮次耗尽 → 强制终止
        elapsed_s = (time.monotonic() - msg_start_time)
        if elapsed_s > MESSAGE_TIMEOUT_S:
            terminal = True

        turn += 1


async def _execute_tool_chunk(self, msg: Message, chunk: LLMChunk, turn: int):
    """工具执行：分类 → 分发 → 结果注入 → 推送客户端"""
    location = self.tool_dispatcher.classify(chunk.tool_name)
    if location == ToolLocation.CLIENT:
        await self._push_chunk({
            "type": "client.tool_request",
            "request_id": str(uuid4()),
            "tool_name": chunk.tool_name,
            "input": chunk.tool_input,
        })
        result = await self.sync_waiter.wait(self.session.id, timeout=120)
        if result is None:
            await self._push_chunk({"type": "client.tool_timeout", ...})
            result = {"success": False, "error": "客户端工具执行超时"}
    else:
        try:
            result = await asyncio.wait_for(
                self.tool_dispatcher.dispatch(self.session.id, chunk), timeout=120)
        except asyncio.TimeoutError:
            result = {"success": False, "error": "服务端工具执行超时"}
    await self.context_mgr.append_tool_result(self.session.id, chunk, result)
    msg.tool_calls_count += 1


async def _check_tool_permission(self, msg, tool_name, tool_input, tool_call_id, turn):
    """权限检查：被拒绝的工具注入错误上下文并继续"""
    try:
        self.permission.check(tool_name, tool_input or {})
        return True
    except Exception as e:
        await self.context_mgr.append_error_feedback(self.session.id, tool_name, str(e))
        msg.tool_calls_count += 1
        return False
```

### 1.4 三种模式的行为汇总

| | Ask | Plan | Build |
|--|-----|------|-------|
| **tools 参数** | `None`, `tool_choice="none"` | turn 0 仅 plan_question; 确认后传 `auto` | `auto` |
| **LLM 行为** | 纯文本回复 | turn 0 生成计划; turn 1..n 自动执行 | 每轮正常调用工具 |
| **暂停点** | 无 | 1 次（计划→确认）+ N 次（plan.question） | 每个 tool_use 1 次 |
| **Client 交互** | 仅接收流式文本 | 流推计划 → 等 POST confirm/edit/reject; 流推问题 → 等 POST plan/answer | 流推每步 → 等 POST confirm/skip/abort |
| **终止条件** | stop_reason=end_turn 或 1 轮结束 | 计划拒绝 / 步骤全部完成 | 用户 abort / 自然完成 |
| **典型场景** | "什么是闭包？" | "帮我搭建一个 React 项目" | "把 src/utils.ts 里的 foo 重构并跑通测试" |
| **thinking** | 三种模式均支持 `thinking_budget`（与 mode 正交），启用后 LLM 每轮调用传入 `thinking={"type":"enabled","budget_tokens":N}`，思考内容通过 `agent.thinking` 推送客户端 |
### 1.5 Plan 模式子流程

Plan 模式的核心思想：**先审方案，再自动执行**。整个消息处理过程暂停一次——在 LLM 生成执行计划后、开始具体行动前。但在计划生成过程中，LLM 可以通过 `plan.question` 随时向用户发起交互式追问（选择题/填空题），确保需求没有遗漏。

#### 完整流程

```
用户发消息 "帮我写一个 Python 爬虫"
        │
        ▼
┌─ PROCESSING (turn 0, 不带 tools) ─────────────┐
│                                                │
│  LLM 推理中，可能穿插多次 plan.question:          │
│                                                │
│  ┌─ plan.question ────────────────────────────┐ │
│  │ "你希望爬取哪个网站？"                        │ │
│  │ options: ["新闻网站", "电商平台", "社交平台"]  │ │
│  │                                            │ │
│  │   → WAITING_SYNC                           │ │
│  │   用户选 "电商平台"                           │ │
│  │   → POST /plan/answer                      │ │
│  │   → PROCESSING，继续 turn 0                 │ │
│  └────────────────────────────────────────────┘ │
│                                                │
│  ┌─ plan.question ────────────────────────────┐ │
│  │ "需要处理反爬机制吗？"                        │ │
│  │ options: ["是，需要", "不需要"]               │ │
│  │                                            │ │
│  │   → 用户选 "是，需要"                         │ │
│  │   → POST /plan/answer → PROCESSING          │ │
│  └────────────────────────────────────────────┘ │
│                                                │
│  LLM 收集够信息后，输出完整计划文本:               │
│  { type: "plan.generated",                     │
│    plan_text: "1. 创建项目目录\n                   │
│                2. 分析目标电商网站结构\n            │
│                3. 实现反爬绕过\n                   │
│                4. 编写爬虫主逻辑\n                 │
│                5. 数据清洗与存储\n                 │
│                6. 编写 README" }                │
│                                                │
│  → 切换到 WAITING_SYNC                         │
└────────────────────────────────────────────────┘
        │
        ▼  客户端展示计划文本，用户做出选择:
        │
┌─ WAITING_SYNC ────────────────────────────────┐
│                                                │
│  [确认] 用户认可方案                             │
│  → POST /sessions/{id}/plan/confirm             │
│  → 切回 PROCESSING                              │
│  → turn 1..n 自动执行，工具调用不再暂停            │
│  → 所有步骤完成后 TERMINAL                       │
│                                                │
│  [编辑] 用户修改计划文本                          │
│  → POST /sessions/{id}/plan/edit                │
│    Body: { plan_text: "修改后的计划..." }         │
│  → 服务端更新计划文本                             │
│  → 重新推送 plan.generated                      │
│  → 再次等待用户确认（循环，不限制编辑次数）          │
│                                                │
│  [拒绝] 用户不满意方案                            │
│  → POST /sessions/{id}/cancel                         │
│  → LLM 追加回复"计划已取消，请重新描述您的需求"     │
│  → TERMINAL，等待用户发送新消息                   │
│                                                │
└────────────────────────────────────────────────┘
```

#### plan.question 详解

`plan.question` 是 Plan 模式 turn 0 中 LLM 可调用的特殊 tool，用于向用户发起交互式提问。它只出现在 plan 未确认阶段，一旦用户确认计划进入 turn 1+，不再触发。

**三种问题类型：**

| `input_type` | 用途 | 前端渲染 | 用户返回值 |
|-------------|------|---------|-----------|
| `select` | 选择题，多选一 | 选项卡片列表 | 选中的 `option` 值 |
| `text` | 填空题，自由输入 | 文本输入框 | 用户输入的字符串 |

**plan.question 数据块格式：**

```typescript
{
  type: "plan.question";
  seq: number;
  message_id: string;
  question: string;                    // 问题文本，如 "你希望部署到哪个平台？"
  options?: string[];                  // select 类型时必填，选项列表
  input_type: "select" | "text";
  context?: string;                    // 可选，解释为什么问这个问题
}
```

**用户回答：**

```
POST /sessions/{id}/plan/answer
Body: { answer: "电商平台" }          // select: 选项文本; text: 自由文本
```

回答直接注入 LLM 上下文，形式为：`用户关于"<question>"的回答：<answer>`。引擎切回 PROCESSING，继续当前 turn。

**超时处理：** 300s 内用户未响应 → 推送 `plan.question_timeout` → 注入 `[用户未回应此问题，请跳过并继续]` → LLM 自行决定下一步。

**与 `plan.generated` 的关系：**

- `plan.question` 是**中途暂停**，LLM 还在收集信息
- `plan.generated` 是**最终交付物**，LLM 认为信息够了，给出完整方案
- LLM 自行判断何时不再提问、何时输出最终计划
- 一次 turn 0 中 `plan.question` 可以有 0 到 N 次

#### 与 Build 模式的核心区别

| | Plan | Build |
|--|------|-------|
| 暂停次数 | 1 次（确认计划）+ N 次（plan.question，可选） | **每步暂停**（每执行一个工具前等确认） |
| 用户确认什么 | 确认"整体方案对不对"，或在计划阶段回答追问 | 确认"这一步做不做" |
| 适用场景 | 复杂多步骤任务，需要前置审核和需求澄清 | 需要精细控制每一步操作

### 1.6 Build 模式子流程

```
PROCESSING:
  LLM 返回 tool_use → push stream "client.tool_request" (Build 模式带 requires_approval=true)
  → 切换到 WAITING_SYNC

WAITING_SYNC:
  收到 POST /sessions/{id}/tool-result/{request_id}  → 工具结果注入上下文 → 切回 PROCESSING
  (Build 模式: 客户端先展示确认卡片, 用户确认后执行并回传结果; 跳过回传 {skipped:true})
  收到 POST /sessions/{id}/cancel    → TERMINAL
```

### 1.7 消息排队流程

**实现原理：DB 为数据源 + asyncio.Event 为调度信号**

```
                  POST /sessions/{id}/messages
                         │
          ┌──────────────▼──────────────┐
          │  INSERT INTO messages       │
          │  (status='pending',         │
          │   queue_position=N)         │
          └──────────────┬──────────────┘
                         │
          ┌──────────────▼──────────────┐
          │  self._wake_event.set()     │  ← 跨协程唤醒引擎
          └──────────────┬──────────────┘
                         │
          ┌──────────────▼──────────────┐
          │  引擎被唤醒，dequeue:         │
          │  SELECT ... WHERE           │
          │  status='pending' ORDER BY  │
          │  queue_position LIMIT 1     │
          │  FOR UPDATE SKIP LOCKED     │
          │  → UPDATE status='processing'│
          │  → 开始 per-message loop     │
          └──────────────────────────────┘

DB + Event 混合方案的原因:

| 问题 | 纯内存 asyncio.Queue | DB + Event (本方案) |
|------|---------------------|--------------------|
| 服务器重启 | 队列丢失 | 从 DB 恢复 status='pending' |
| 并发安全 | 单进程 OK | FOR UPDATE SKIP LOCKED |
| 用户手动移除 | 需自定义索引 | UPDATE WHERE status='pending' |
| 客户端查询队列 | 需额外 API 读内存 | SELECT 即得 |
```

**引擎主循环**:

```python
class QueryLoopEngine:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state = "IDLE"
        self._wake_event = asyncio.Event()
        self._current_msg: Message | None = None
        self._lock = asyncio.Lock()

    async def run(self):
        """会话主循环: 休眠 → 唤醒 → 处理 → 检查队列 → 休眠"""
        while True:
            msg = await self._dequeue_next()
            if msg is None:
                # 队空 → IDLE
                self.state = "IDLE"
                await self._wake_event.wait()
                self._wake_event.clear()
                continue

            # 有消息 → PROCESSING
            self.state = "PROCESSING"
            self._current_msg = msg
            await self._push_chunk({
                "type": "message.start",
                "seq": self._next_seq(),
                "message_id": msg.id,
                "mode": msg.mode,
                "scene_mode": msg.scene_mode,
                "workspace": msg.workspace,
            })

            try:
                await self._run_message_loop(msg)
            except Exception as e:
                await self._push_chunk({"type": "message.error", "message_id": msg.id, "message": str(e)})
            finally:
                await self._mark_completed(msg)
                self._current_msg = None

            # 循环 back to dequeue_next()
```

**入队 / 出队 / 移除**:

```python
async def _dequeue_next(self) -> Message | None:
    """DB 原子出队: SKIP LOCKED 避免并发竞争"""
    async with self._lock:
        row = await db.fetchrow(
            """UPDATE messages SET status='processing', queue_position=NULL
               WHERE id = (
                 SELECT id FROM messages
                 WHERE session_id=$1 AND status='pending'
                 ORDER BY queue_position ASC LIMIT 1
                 FOR UPDATE SKIP LOCKED
               )
               RETURNING *""",
            self.session_id
        )
        if row:
            await self._renumber_queue()    # 剩余消息重排 queue_position
        return Message.from_row(row) if row else None


async def enqueue(self, user_id, content, scene_mode, workspace, model, mode,
                     files=None, skill_ids=None, mcp_servers=None) -> Message:
    """HTTP handler 调用: 写入 DB + 唤醒引擎"""
    async with self._lock:
        next_pos = await db.fetchval(
            """SELECT COALESCE(MAX(queue_position), 0) + 1
               FROM messages WHERE session_id=$1 AND status='pending'""",
            self.session_id
        )
        if next_pos > 10:
            raise HTTPException(429, "队列已满，最多 10 条")

        msg = await db.fetchrow(
            """INSERT INTO messages
               (session_id, user_id, content, scene_mode, workspace, model, mode,
                files, skill_ids, mcp_servers, status, queue_position)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pending',$11)
               RETURNING *""",
            self.session_id, user_id, content, scene_mode, workspace, model, mode,
            files or [], skill_ids or [], mcp_servers or [], next_pos
        )

    self._wake_event.set()          # 唤醒引擎
    return Message.from_row(msg)


async def remove_from_queue(self, msg_id: UUID):
    """用户手动移除队列中的消息"""
    async with self._lock:
        await db.execute(
            """UPDATE messages SET status='cancelled', queue_position=NULL
               WHERE id=$1 AND session_id=$2 AND status='pending'""",
            msg_id, self.session_id
        )
        await self._renumber_queue()


async def _renumber_queue(self):
    """重排剩余 pending 消息的 queue_position 为 1,2,3..."""
    pending = await db.fetch(
        """SELECT id FROM messages
           WHERE session_id=$1 AND status='pending'
           ORDER BY queue_position ASC""",
        self.session_id
    )
    for i, row in enumerate(pending, start=1):
        await db.execute(
            "UPDATE messages SET queue_position=$1 WHERE id=$2",
            i, row["id"]
        )


```

**运行示例**:

```
1. 用户发送 msg1 → enqueue() → _wake_event.set()
   → 引擎 IDLE → 被唤醒 → _dequeue_next() → msg1 PROCESSING

2. PROCESSING 期间:
   用户发送 msg2 → enqueue(queue_position=1) → _wake_event.set() (引擎已醒,无操作)
   用户发送 msg3 → enqueue(queue_position=2)
   用户手动移除 msg3 → remove_from_queue() → msg3 cancelled

3. msg1 处理完成 → 循环回 _dequeue_next()
   → 查到 msg2 (queue_position=1) → 出队 → msg2 PROCESSING
   → 再次 _dequeue_next() → None → IDLE
```

### 1.8 异常处理

#### 1.8.1 异常全景图（按 Loop 执行链路排列）

```
enqueue() → context.build() → llm.stream() → parse events → permission.check()
                                                                    │
                                                             tool_dispatcher.dispatch()
                                                                    │
                                                     context.append_tool_result()
                                                                    │
                                                       sync_waiter.wait()
                                                                    │
                                                              _push_chunk 给客户端
```

| # | 阶段 | 异常 | 触发条件 |
|---|------|------|---------|
| 1 | **入队** | 队列满 | 队中已有 10 条 pending → HTTP 429 |
| 2 | **入队** | 重复入队 | 同一条消息 ID 重复 POST，幂等处理：查重后返回已有记录 |
| 3 | **入队** | 会话已归档 | session.status='archived' → HTTP 410 Gone |
| 4 | **build context** | DB 查询超时/失败 | PG 连接断开、慢查询 |
| 5 | **build context** | @文件不存在/过大 | 文件被外部删除或超过 8000 字限制 → 注入警告文本替代 |
| 6 | **build context** | Token 超限 | 截断历史后仍超窗口 90% → 强制摘要压缩最早轮次 |
| 7 | **llm.stream()** | 网络超时 | API 不可达 → 指数退避重试 3 次 |
| 8 | **llm.stream()** | Rate Limit (429) | 超出并发/速率配额 → 退避重试 3 次，仍失败则排队降级 |
| 9 | **llm.stream()** | 认证失败 (401/403) | API key 过期或余额不足 → 致命错误，通知用户 |
| 10 | **llm.stream()** | 流中断 | 流中途断开 → 重试 1 次，断点续传不可用则重新调用 |
| 11 | **llm.stream()** | 空响应 | LLM 未返回任何 content 或 tool_use → 重试 1 次，仍空则返回"抱歉，我暂时无法回答" |
| 12 | **llm.stream()** | 格式异常 | JSON 解析失败，tool_use.input 非法 → 错误注入上下文，让 LLM 修正 |
| 13 | **parse events** | 无限循环 | LLM 连续 3 次调用同一工具且输入/输出相同 → 强制终止，反馈"检测到重复操作" |
| 14 | **parse events** | 内容安全拦截 | API 返回 content_filter → 中止，推送"内容被安全策略拦截" |
| 15 | **permission** | 权限拒绝 | 工具/路径被策略拦截 → 错误注入上下文，LLM 尝试替代方案 |
| 16 | **dispatch** | 工具不存在 | LLM 捏造了 tool name → 错误注入上下文，列出可用工具 |
| 17 | **dispatch** | Server 工具执行失败 | MCP 不可达、API 错误 → 错误注入上下文 |
| 18 | **dispatch** | Client 工具回传超时 | 120s 内客户端未回 → 推送 tool_timeout，LLM 决定重试/跳过 |
| 19 | **dispatch** | Client 工具执行失败 | Shell 非零退出、文件写入权限不足 → 错误(含 stderr)注入上下文 |
| 20 | **dispatch** | Client 回传篡改 | request_id 不匹配 → 忽略，继续等待正确回传 |
| 21 | **wait sync** | 用户无响应超时 | Plan/Build 模式下 300s 无确认 → 推送 timeout，释放等待 |
| 22 | **stream push** | 连接断开 | 客户端离线 → 引擎继续执行，数据块写入 buffer 等待重连回放 |
| 23 | **总耗时** | 执行超时 | 单条消息总耗时超 300s → 强制终止，推送 max_turns_exceeded |
| 24 | **Max turns** | 轮次耗尽 | turn >= 25 未终止 → 强制终止 |
| 25 | **引擎层** | 并发冲突 | asyncio.Lock 兜底，2s 未获取锁则记录告警日志 |

#### 1.8.2 四级处理策略

```
第 1 级：重试（瞬时故障，自动恢复）
  网络超时、流中断、DB 连接断开
  → 指数退避: 1s → 2s → 4s，最多 3 次
  → 每次重试前推送 system.status(level="info") 告知用户后端正在重试
  → 3 次均失败升级为第 4 级

第 2 级：降级（可恢复错误，LLM 自适应）
  工具不存在、权限拒绝、工具执行失败、@文件不存在、格式异常
  → 错误文本注入上下文: "工具 'xxx' 执行失败: <原因>。请尝试其他方法。"
  → LLM 自行调整策略，继续 loop
  → Token 压缩等场景推送 system.status(level="warning") 告知用户上下文已变化

第 3 级：暂停（等待用户介入）
  用户 Plan/Build 无响应、Client 工具回传超时
  → 引擎进入 SUSPENDED 状态，等待用户手动恢复或取消
  → 客户端断开时推送 system.status(level="info")，重连回放时再次推送

第 4 级：终止（不可恢复，释放引擎）
  Rate Limit 耗尽、认证失败、内容安全拦截、Max turns、执行超时、死循环检测
  → 直接推送错误数据块: { type: "message.error", message: "...", fatal: true }
  → 消息标记 error，引擎回到 IDLE 或归档
```

**四级策略中 system.status 的 code 对照：**

| 级别 | 异常场景 | system.status code | 推荐 message 文案 |
|------|---------|-------------------|-------------------|
| 1 重试 | LLM 网络超时 | `llm_retrying` | "AI 服务连接超时，正在重试（第 2/3 次，4 秒后）..." |
| 1 重试 | Rate Limit 429 | `llm_rate_limited` | "请求过于频繁，AI 服务已限流，将在 4 秒后重试（第 2/3 次）..." |
| 1 重试 | DB 连接断开 | `db_retrying` | "数据库连接异常，正在重试（第 2/3 次）..." |
| 1 重试 | LLM 流中断 | `llm_stream_interrupted` | "AI 响应流意外中断，正在重新建立连接..." |
| 1 重试 | LLM 空响应 | `llm_empty_response` | "AI 未返回有效内容，正在重新请求..." |
| 2 降级 | Token 超限压缩 | `token_compressing` | "对话上下文已超过模型窗口限制，正在自动压缩较早的对话记录，被压缩部分的细节可能丢失。" |
| 2 降级 | LLM 输出格式异常 | `llm_format_error` | "AI 返回的数据格式不符合预期，已将错误反馈给模型进行修正..." |
| 3 暂停 | 客户端连接断开 | `client_disconnected` | "您的客户端已断开连接，AI 将在后台继续执行任务。重新打开或刷新页面后会自动同步进度。" |
| 3 暂停 | 重连回放缓冲 | `stream_buffer_replaying` | "已重新连接，正在同步您离线期间产生的 N 条新内容..." |
| — | 服务器重启恢复 | `session_recovering` | "服务器刚刚完成重启，您之前中断的消息将从头重新执行。" |

> **不推送 system.status 的场景：** 第 4 级终止类异常（死循环检测、内容安全拦截、执行超时、轮次耗尽、认证失败、Rate Limit 耗尽）直接走 `message.error(fatal=true)`；入队阶段异常（队列满/重复/归档）通过 HTTP 状态码直接返回；权限拒绝/工具不存在/工具执行失败通过降级注入上下文，LLM 自适应；Client 工具超时已有 `client.tool_timeout`。

#### 1.8.3 重试与退避实现

```python
import asyncio

RETRIABLE_ERRORS = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.HTTPStatusError,   # 仅 429, 502, 503
)

async def call_llm_with_retry(ctx, tools, system, push_status):
    last_exc = None
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        try:
            return await llm.stream(
                messages=ctx.messages,
                tools=tools,
                system=system,
            )
        except RETRIABLE_ERRORS as e:
            last_exc = e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code not in (429, 502, 503):
                raise   # 401/403 等不重试
            if attempt < MAX_RETRIES - 1:
                delay = 2 ** attempt  # 1s → 2s → 4s
                code = "llm_rate_limited" if (
                    isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429
                ) else "llm_retrying"
                await push_status({
                    "type": "system.status",
                    "code": code,
                    "message": (
                        f"请求过于频繁，AI 服务已限流，将在 {delay}s 后重试（第 {attempt+1}/{MAX_RETRIES} 次）..."
                        if code == "llm_rate_limited" else
                        f"AI 服务连接超时，正在重试（第 {attempt+1}/{MAX_RETRIES} 次，{delay}s 后）..."
                    ),
                    "detail": str(e)[:200],
                    "attempt": attempt + 1,
                    "max_attempts": MAX_RETRIES,
                })
                await asyncio.sleep(delay)

    raise MaxRetriesExceeded(last_exc)
```

**流中断处理：**

```python
async def call_llm_stream_with_reconnect(ctx, tools, system, push_status):
    """LLM 流式调用，流中断时自动重连一次"""
    try:
        async for chunk in llm.stream(messages=ctx.messages, tools=tools, system=system):
            yield chunk
    except (httpx.RemoteProtocolError, httpx.StreamClosed) as e:
        # 流意外中断 → 通知客户端后重试一次
        await push_status({
            "type": "system.status",
            "code": "llm_stream_interrupted",
            "message": "AI 响应流意外中断，正在重新建立连接...",
            "detail": str(e)[:200],
        })
        async for chunk in llm.stream(messages=ctx.messages, tools=tools, system=system):
            yield chunk
```

**空响应和格式异常处理：**

```python
async def check_llm_response(response_chunks, push_status) -> bool:
    """检查 LLM 响应是否有效，无效时推送 status 并返回 False"""
    has_content = any(c.type in ("text", "tool_use") for c in response_chunks)

    if not has_content:
        await push_status({
            "type": "system.status",
            "code": "llm_empty_response",
            "message": "AI 未返回有效内容，正在重新请求...",
        })
        return False

    for chunk in response_chunks:
        if chunk.type == "tool_use":
            try:
                json.loads(chunk.tool_input_json)
            except json.JSONDecodeError:
                await push_status({
                    "type": "system.status",
                    "code": "llm_format_error",
                    "message": "AI 返回的数据格式不符合预期，已将错误反馈给模型进行修正...",
                    "detail": f"tool={chunk.tool_name} input 不是合法 JSON",
                })
                return False
    return True
```

**上下文 Token 压缩通知：**

```python
async def _maybe_compress_context(self) -> bool:
    """Token 超限时压缩历史，并通知客户端"""
    if self.context.estimated_tokens <= self.context.max_tokens * 0.9:
        return False

    await self._push_chunk({
        "type": "system.status",
        "code": "token_compressing",
        "message": "对话上下文已超过模型窗口限制，正在自动压缩较早的对话记录，被压缩部分的细节可能丢失。",
        "detail": f"压缩前 tokens: {self.context.estimated_tokens}",
    })
    self.context.compress_earliest_turns()
    return True
```

**数据库操作重试：**

```python
async def db_execute_with_retry(query, *args, push_status, max_retries=3):
    """数据库操作带重试，重试期间通知客户端"""
    for attempt in range(max_retries):
        try:
            return await db.execute(query, *args)
        except (ConnectionError, OperationalError) as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                await push_status({
                    "type": "system.status",
                    "code": "db_retrying",
                    "message": f"数据库连接异常，正在重试（第 {attempt+1}/{max_retries} 次）...",
                    "detail": str(e)[:200],
                    "attempt": attempt + 1,
                    "max_attempts": max_retries,
                })
                await asyncio.sleep(delay)
    raise e
```

#### 1.8.4 重复操作检测

```python
# 在 per-message loop 中:
_recent_tool_calls: list[tuple[str, str]] = []  # [(tool_name, input_hash), ...]

async def _check_loop_detection(self, chunk) -> bool:
    """连续 3 次相同工具+相同输入 → 判定为死循环"""
    key = (chunk.tool_name, hashlib.md5(chunk.tool_input_json.encode()).hexdigest())
    self._recent_tool_calls.append(key)
    if len(self._recent_tool_calls) > 3:
        self._recent_tool_calls.pop(0)
    if len(self._recent_tool_calls) == 3 and len(set(self._recent_tool_calls)) == 1:
        await self._push_chunk({
            "type": "message.error",
            "message": "检测到连续三次相同工具调用，可能是死循环，已自动终止。"
        })
        return True
    return False
```

#### 1.8.5 流连接断开时的缓冲回放

```python
class StreamBuffer:
    """客户端断开时缓冲数据块，重连后回放"""
    def __init__(self, session_id: str, max_size: int = 500,
                 push_status: Callable | None = None):
        self.buffer: list[dict] = []
        self.max_size = max_size
        self._push_status = push_status
        self._client_connected = True

    def push(self, chunk: dict):
        if self._push_status and self._client_connected:
            # 首次检测到客户端断开（流 push 失败），通知用户
            self._client_connected = False
            asyncio.create_task(self._push_status({
                "type": "system.status",
                "code": "client_disconnected",
                "message": "您的客户端已断开连接，AI 将在后台继续执行任务。重新打开或刷新页面后会自动同步进度。",
            }))
        if len(self.buffer) >= self.max_size:
            self.buffer.pop(0)
        self.buffer.append(chunk)

    async def drain(self, since_seq: int | None = None) -> list[dict]:
        """客户端重连时回放 since_seq 之后的数据块"""
        if self._push_status:
            await self._push_status({
                "type": "system.status",
                "code": "stream_buffer_replaying",
                "message": f"已重新连接，正在同步您离线期间产生的 {len(self.buffer)} 条新内容...",
            })
        self._client_connected = True
        if since_seq is None:
            return list(self.buffer)
        return [c for c in self.buffer if c.get("seq", 0) > since_seq]
```

#### 1.8.6 引擎恢复（服务器重启后）

```python
async def recover_session(session_id: UUID):
    """服务启动时恢复未完成的会话"""
    session = await db.fetchrow(
        "SELECT * FROM sessions WHERE status='active'"
    )
    if not session:
        return

    engine = QueryLoopEngine(session_id)

    # 恢复当前正在处理的消息（如果有）
    if session["current_message_id"]:
        msg = await db.fetchrow(
            "SELECT * FROM messages WHERE id=$1 AND status='processing'",
            session["current_message_id"]
        )
        if msg:
            # 重置为 pending，重新处理
            await db.execute(
                "UPDATE messages SET status='pending', queue_position=0 WHERE id=$1",
                msg["id"]
            )
            await engine._push_chunk({
                "type": "system.status",
                "code": "session_recovering",
                "message": "服务器刚刚完成重启，您之前中断的消息将从头重新执行。",
                "detail": f"message_id={msg['id']}",
            })
        await db.execute(
            "UPDATE sessions SET current_message_id=NULL WHERE id=$1",
            session["id"]
        )

    asyncio.create_task(engine.run())
    return engine
```

### 1.9 前后端主对话接口完整定义

#### 1.9.1 接口总览

**通信协议：Streamable HTTP**

| 分类 | 方法 + 路径 | 方向 | 服务端作用 | 触发时机 | 前端作用 |
|------|------------|------|-----------|---------|---------|
| **会话** | `GET /sessions` | Cli ← Svr | 查询当前用户的所有会话列表，按更新时间倒序 | 侧边栏任务列表加载、切换任务 | 获取任务列表数据（id、标题、更新时间），渲染侧边栏任务项。 |
| **会话** | `POST /sessions` | Cli → Svr | 创建会话，注册客户端工具清单并存储初始配置。**客户端工具注册的唯一入口。** | 用户点击侧边栏"新建任务" | Electron 本地生成 session_id (UUID)，携带 `client_tools`（完整工具定义）、`workspace`、`model`、`mode`、`scene_mode` 创建会话。工具清单存储为会话元数据，后续该会话所有消息自动使用。 |
| **会话** | `GET /sessions/{id}` | Cli ← Svr | 获取会话详情（含已注册的 client_tools、当前配置、消息列表） | 恢复已有任务、断线重连后加载会话状态 | 加载会话完整信息，含工具清单、工作空间、模型、模式等，前端据此恢复 UI 状态。 |
| **会话** | `PATCH /sessions/{id}` | Cli → Svr | 更新会话配置（工作空间、模型、模式等） | 用户在首页右栏修改工作空间/模型/使用模式 | Body 携带变更字段，服务端更新会话级默认配置。后续新消息继承新配置，历史消息不受影响。 |
| **会话** | `DELETE /sessions/{id}` | Cli → Svr | 归档会话（软删除） | 用户右键任务 → 删除 | 会话标记为 archived，数据保留但不再出现在列表中。已归档会话拒绝新消息（返回 410）。 |
| **主对话** | `POST /sessions/{id}/messages` | Cli → Svr | 入队，返回 NDJSON 流式响应，流式推送该消息的所有处理数据块 | 用户每次发送消息 | 将输入文本、工作模式、工作空间等打包提交，接收 NDJSON 流并逐条渲染 AI 思考、文本回复、工具调用状态。**整个对话 UI 实时更新的核心通道。** |
| **重连** | `GET /sessions/{id}/stream?since_seq=N` | Cli ← Svr | 断线重连，从 since_seq 续传丢失数据块。**关键机制：前端断开后引擎继续执行**，数据块写入 StreamBuffer（上限 500 条），重连后从 buffer 回放 `seq > N` 的所有数据块，再继续实时推送。 | 主通道 NDJSON 流断开时自动触发（网络抖动、合盖唤醒、切 Wi-Fi），前端通过流 `onerror` 自动检测并重连，无需用户手动操作 | 记录最后收到的 `seq`，自动重连后从断点续传。**合盖期间 AI 不中断**，开盖后断线期间的数据块一次性追回，无缝衔接最新进度。连续重试失败后展示"连接已断开，点击重试"兜底按钮。 |
| **队列** | `GET /sessions/{id}/queue` | Cli ← Svr | 查询当前队列 | 展示消息排队状态 | 获取队列快照，渲染"前面还有 N 条消息等待处理"及每条排队消息预览。 |
| **队列** | `DELETE /sessions/{id}/queue/{msg_id}` | Cli → Svr | 移除排队中的消息 | 用户取消排队中的消息 | 移除 `pending` 状态的消息（处理中不可移除），收到 200 后从 UI 队列清除。 |
| **Plan** | `POST /sessions/{id}/plan/confirm` | Cli → Svr | 确认计划，开始自动执行 | 用户点击 **[确认计划]** | 通知服务端自动执行计划，前端继续接收工具调用等流式数据块。 |
| **Plan** | `POST /sessions/{id}/plan/edit` | Cli → Svr | 替换计划文本，重新推送 `plan.generated` | 用户点击 **[编辑]** → 修改计划 → 提交 | Body 带 `plan_text`，服务端替换后重新推送 `plan.generated` 给用户再次确认（可反复编辑）。 |
| **通用** | `POST /sessions/{id}/cancel` | Cli → Svr | 取消当前操作：plan 模式追加"计划已取消"后终止；build 模式直接终止 | 用户点击 **[拒绝]**（Plan）或 **[终止]**（Build） | 无 Body。根据引擎当前模式自动处理。 |
| **Plan** | `POST /sessions/{id}/plan/answer` | Cli → Svr | 用户回答 plan.question 的追问 | 用户选择选项或输入文本 | Body 带 `answer` 字符串，注入 LLM 上下文后继续当前 turn。 |
| **Build** | `POST /sessions/{id}/tool-result/{request_id}` | Cli → Svr | 工具结果回传（Build 模式统一入口）：确认后执行回传结果；跳过回传 `{skipped:true}` | 用户点击 **[确认]** / **[跳过]** | 确认→注入结果继续；跳过→注入 skip 反馈继续。 |
| **工具回传** | `POST /sessions/{id}/tool-result/{request_id}` | Cli → Svr | 结果注入 LLM 上下文继续推理 | 前端本地执行完 `client.tool_request` 后 | 回传成功（stdout + 文件变更）或失败（stderr + 退出码）。**串联 AI 思考与本地执行的闭环。** |

**与 SSE 方案的关键区别：**

| | SSE | Streamable HTTP |
|--|-----|-----------------|
| 主通道 | GET /stream 长连接（独立于请求） | POST /messages 响应本身就是流 |
| 客户端请求数 | 2 个连接（POST + GET） | 1 个连接（POST 即流） |
| 断线重连 | GET /stream?since_seq=N | GET /stream?since_seq=N（仅重连用） |
| 协议格式 | `event: xxx\ndata: {...}\n\n` | 每行一个 JSON: `{"type":"xxx",...}\n` |
| 消息边界 | 空行分隔 | 换行符分隔（NDJSON） |

---

#### 1.9.2 接口详细定义

以下按 1.9.1 接口总览中的分类逐一说明每个接口的请求/响应/流式数据块。

---

##### 会话列表 — `GET /sessions`

用户登录后首次加载侧边栏任务列表时调用，也可用于刷新列表。

**Query 参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status` | `string` | 否 | 过滤条件：`active`（默认，未归档）、`archived`（已归档）、`all` |
| `limit` | `number` | 否 | 每页条数，默认 50，最大 100 |
| `offset` | `number` | 否 | 分页偏移，默认 0 |

**Response (JSON):**

```typescript
// 200 OK
{
  sessions: {
    id: string;
    title: string;                      // 任务标题（默认"新建任务"，首条消息后自动截取前 20 字符）
    mode: "ask" | "plan" | "build";
    scene_mode: "office" | "code";
    model: string;
    workspace: string;
    message_count: number;
    created_at: string;
    updated_at: string;                 // 最后活跃时间
    status: "active" | "archived";
  }[];
  total: number;
}
```

---

##### 会话创建 — `POST /sessions`

用户点击侧边栏"新建任务"时触发。**客户端在此接口中上报本地工具清单**，服务端将其与会话绑定存储。此后该会话的所有消息自动使用这份工具定义，无需每条消息重复携带。

**触发流程：**

```
用户点击侧边栏"新建任务"
  → Electron 本地生成 session_id (UUID v4)
  → POST /sessions  携带 client_tools + 初始配置
  → 服务端创建会话记录，存储工具清单
  → 返回 201，前端获得 session_id
  → 后续 POST /sessions/{id}/messages 基于此会话
```

**Request Body (JSON):**

```typescript
{
  // ── 必填 ──
  id: string;                           // 客户端生成的 UUID v4，服务端做幂等校验

  // ── 初始配置（必填，与消息级配置一致）──
  scene_mode: "office" | "code";
  workspace: string;                    // 工作空间根目录绝对路径（沙箱边界）
  model: string;                        // e.g. "claude-opus-4-7"
  mode: "ask" | "plan" | "build";
  thinking_budget?: number;             // 会话级默认 thinking token 预算（>=1024），后续消息可逐条覆盖

  // ── 客户端本地工具清单（必填，注册后该会话所有消息共享）──
  client_tools: {
    name: string;                       // 工具名，e.g. "bash", "read_file", "write_file", "edit_file"
    description: string;                // 给 LLM 看的功能描述
    input_schema: {                     // JSON Schema，定义工具参数
      type: "object";
      properties: Record<string, unknown>;
      required?: string[];
    };
  }[];

  // ── 可选 ──
  mcp_servers?: {                       // 用户默认启用的 MCP 服务列表
    server_id: string;
    server_name: string;
    enabled_tools?: string[];
  }[];
}
```

> **client_tools 示例：**
>
> ```json
> {
>   "name": "bash",
>   "description": "Execute a shell command in the workspace directory. Returns stdout, stderr, and exit code.",
>   "input_schema": {
>     "type": "object",
>     "properties": {
>       "command": { "type": "string", "description": "The shell command to execute" },
>       "timeout_ms": { "type": "number", "description": "Timeout in milliseconds, default 120000" }
>     },
>     "required": ["command"]
>   }
> }
> ```

> **设计要点：**
>
> | 决策 | 理由 |
> |------|------|
> | client_tools 在会话创建时注册，非每条消息携带 | 工具定义对客户端版本固定，重复传输浪费带宽；绑定会话保证历史消息可复现 |
> | 由客户端生成 session_id | 离线可用（无需等服务端返回 ID），且客户端可在网络恢复后重试创建（幂等） |
> | 会话创建后工具清单不可变 | 保证会话内上下文一致性；客户端升级后新会话用新工具，旧会话不受影响 |
> | 不传 `client_tools` 或传空数组 | 表示该会话仅使用纯文本对话和服务端 MCP，无本地工具可用 |

**Response (JSON):**

```typescript
// 201 Created
{
  id: string;                           // 回显 session_id
  title: string;                        // "新建任务"
  mode: "ask" | "plan" | "build";
  scene_mode: "office" | "code";
  model: string;
  workspace: string;
  client_tools_count: number;           // 已注册的客户端工具数量
  mcp_servers_count: number;
  created_at: string;
}

// 409 Conflict — 幂等：已存在同 ID 会话
{ error: "duplicate"; message: "该 session_id 已存在"; existing_session: { id: string; title: string; created_at: string; }; }

// 400 — client_tools 为空或格式非法
{ error: "invalid_request"; message: "client_tools 不能为空且必须为合法 JSON Schema 数组"; }
```

---

##### 会话详情 — `GET /sessions/{id}`

用于恢复已有任务或断线重连后加载会话完整状态。

**Response (JSON):**

```typescript
// 200 OK
{
  id: string;
  title: string;
  mode: "ask" | "plan" | "build";
  scene_mode: "office" | "code";
  model: string;
  workspace: string;
  status: "active" | "archived";
  client_tools: {                     // 创建时注册的工具清单
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
  }[];
  mcp_servers: {
    server_id: string;
    server_name: string;
    enabled_tools: string[];
  }[];
  current_processing: {               // 当前正在处理的消息（如有）
    message_id: string;
    started_at: string;
  } | null;
  queue_size: number;                  // 排队中的消息数
  message_count: number;
  created_at: string;
  updated_at: string;
}

// 404
{ error: "not_found"; message: "会话不存在"; }
```

---

##### 会话更新 — `PATCH /sessions/{id}`

用户在首页右栏修改工作空间、模型或使用模式时触发。**仅更新会话级默认值**，历史消息的配置不变。

**Request Body (JSON):**

```typescript
{
  // 以下字段均为可选，传哪些更新哪些
  workspace?: string;
  model?: string;
  mode?: "ask" | "plan" | "build";
  scene_mode?: "office" | "code";
  mcp_servers?: {
    server_id: string;
    server_name: string;
    enabled_tools?: string[];
  }[];
}
```

**Response (JSON):**

```typescript
// 200 OK — 返回更新后的完整会话信息（字段同 GET /sessions/{id} 响应）
{ ... }

// 410 — 会话已归档
{ error: "session_archived"; message: "该会话已归档，无法更新配置"; }
```

> **与消息级配置的关系：** 会话级配置是默认值，`POST /sessions/{id}/messages` 发布时客户端仍携带当前生效的配置（独立存储于每条消息）。用户中途切换工作空间后：新消息使用新 workspace，历史消息保留旧 workspace（可复现）。

---

##### 会话删除 — `DELETE /sessions/{id}`

软删除（归档），会话数据保留，但不展示在列表中，且拒绝新消息。

**Response (JSON):**

```typescript
// 200 OK
{ status: "archived"; id: string; archived_at: string; }

// 404
{ error: "not_found"; message: "会话不存在"; }
```

---

##### 主对话 — `POST /sessions/{id}/messages`

**Request Body (JSON):**

```typescript
{
  // ── 必填 ──
  content: string;                    // 用户输入文本

  // ── 运行配置（每条消息独立携带，覆盖会话级默认值） ──
  scene_mode: "office" | "code";     // 工作场景: office=日常办公, code=代码开发
  workspace: string;                  // 工作空间根目录绝对路径（沙箱边界）
  model: string;                      // 模型标识符, e.g. "claude-opus-4-7", "deepseek-v3"
  mode: "ask" | "plan" | "build";    // 使用模式: ask=问答, plan=规划, build=构建

  // ── 可选 ──
  thinking_budget?: number;            // Claude extended thinking token 预算（>=1024，0 或不传关闭）。仅 Opus/Sonnet 生效，其他模型忽略
  skill_invocations?: {               // / 调用的 Skill 列表，服务端预加载 SKILL.md 作为首条 user 消息
    skill_id: string;                 // Skill 唯一标识（如 "sk1"）
    skill_name: string;               // Skill 名称（如 "code-review"）
  }[];
  files?: string[];                   // @ 引用的文件绝对路径列表
  mcp_servers?: {                     // 本消息启用的 MCP 服务列表
    server_id: string;
    server_name: string;
    enabled_tools?: string[];
  }[];
}
```

> **设计说明：** `scene_mode`, `workspace`, `model`, `mode` 四条为消息级必填，服务端按每条消息独立存储，确保历史消息上下文可复现。`mcp_servers` 可选，不传则使用用户默认启用的列表。消息入队时锁定配置，后续变更不影响已入队消息。

**Response（成功 — NDJSON 流）:**

```
HTTP/1.1 200 OK
Content-Type: application/x-ndjson
Transfer-Encoding: chunked
```

响应头立即返回（毫秒级），Body 为 NDJSON 流，逐条推送处理数据块，流持续到该消息处理完成。

```typescript
// 引擎空闲 → 立即处理，首个数据块为 message.start
{"type":"message.start","seq":0,"message_id":"m1","mode":"build","scene_mode":"code","workspace":"/path/to/project"}
```

**Response（错误 — 非流式，立即返回）:**

```typescript
// 429 — 队列已满（最多 10 条）
{ error: "queue_full"; message: "队列已满，最多 10 条"; current_queue_size: number; }

// 410 — 会话已归档
{ error: "session_archived"; message: "该会话已归档，无法发送新消息"; }

// 400 — 参数不合法
{ error: "invalid_request"; message: "缺少必填参数 scene_mode"; }
```

**流中可能出现的数据块类型：**

| 数据块 | 出现时机 |
|------|---------|
| `message.start` | 消息开始处理 |
| `agent.thinking` | AI 思考过程（增量，可折叠展示） |
| `agent.text` | AI 回复正文（增量） |

| `client.tool_request` | 要求 Client 执行工具。Build 模式下带 `requires_approval=true` 时先展示确认 UI |
| `client.tool_timeout` | Client 工具执行超时 |
| `plan.generated` | Plan 模式：计划生成完毕 |
| `plan.question` | Plan 模式：LLM 向用户提问 |
| `plan.question_timeout` | Plan 模式：用户超时未回答 |
| `queue.enqueued` | 消息入队，告知当前排位和队列长度 |
| `queue.position_changed` | 前方消息完成/取消导致排位变化 |
| `token.usage` | 每次 LLM 调用完成后推送累计 token 消耗 |
| `system.status` | 后端执行补救措施时（重试、压缩、缓冲回放等），告知用户引擎正在做什么 |
| `message.complete` | 消息处理完成（含 turn/token 摘要） |
| `message.error` | 消息处理异常 |

**响应时序：**

```
引擎空闲，立即处理：
Client                              Server
  │─ POST /messages ──────────────→│
  │◄── HTTP 200 ──────────────────│  Content-Type: application/x-ndjson
  │◄── {"type":"message.start",…}
  │◄── {"type":"agent.thinking","delta":"…"}
  │◄── {"type":"agent.text","delta":"…"}
  │◄── ...
  │◄── {"type":"message.complete",…}
  │                                    │  ← 流关闭

引擎正忙，排队等待：
Client                              Server
  │─ POST /messages ──────────────→│
  │◄── HTTP 200 ──────────────────│
  │◄── {"type":"queue.enqueued","queue_position":2,"queue_size":2,…}
  │       … 等待前序消息完成 …            ← 流保持连接，排位变化时推送
  │◄── {"type":"queue.position_changed","new_position":1,"queue_size":1}
  │◄── {"type":"message.start",…}
  │◄── {"type":"agent.text",…}
  │◄── ...
  │◄── {"type":"message.complete",…}
  │                                    │  ← 流关闭
```

> **注意：** 引擎串行处理消息，每条 POST /messages 返回的流仅包含该消息自身的数据块。客户端可同时持有多个流连接（每个对应一条已发送消息）。流最长生命周期 = 排队等待 + 处理（单条上限 300s）。

---

##### 重连 — `GET /sessions/{id}/stream`

纯续传通道，不触发入队。主通道流断开后自动调用，从断点续传。

**Query 参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `since_seq` | `number` | 是* | 该序号之前的数据块均已收到，从 seq+1 开始续推 |
| `since_message_id` | `string` | 否 | 多条流并存时，指定续哪条消息的流 |

> *重连时必填。不传从当前处理中的消息开头推送；无处理中消息则返回空流立即关闭。

**Response:** 与主通道完全一致，`Content-Type: application/x-ndjson`。

**重连时序：**

```
Client                                    Server
  │─ POST /messages (msg m1) ──────────────→│  主通道
  │◄══ NDJSON stream ═══════════════════════│
  │   {"type":"agent.text","seq":14,...}     │
  │   ... connection drops ...              │  ← 网络中断
  │                                          │
  │─ GET /stream?since_seq=15&since_message_id=m1 ──→│  续传
  │◄══ seq 16+ 回放 ═══════════════════════│  ← 从 StreamBuffer 回放
  │   ... 追上实时后继续增量推送 ...         │
  │◄── {"type":"message.complete",…}        │
  │                                          │  ← 流关闭
```

> StreamBuffer 机制见 [1.8.5 流连接断开时的缓冲回放](#185-流连接断开时的缓冲回放)。

---

##### 队列查询 — `GET /sessions/{id}/queue`

**Response (JSON):**

```typescript
// 200 OK
{
  session_id: string;
  queue: {
    message_id: string;
    content_preview: string;          // 前 100 个字符
    queue_position: number;
    status: "pending";
    created_at: string;
  }[];
  current_processing: {
    message_id: string;
    content_preview: string;
    started_at: string;
  } | null;
}
```

---

##### 队列移除 — `DELETE /sessions/{id}/queue/{msg_id}`

**Response (JSON):**

```typescript
// 200 OK
{ success: true; removed_message_id: string; }

// 404 — 不存在或已不在队列
{ error: "not_found"; message: "消息不存在或已开始处理，无法移除"; }

// 409 — 已在处理中
{ error: "already_processing"; message: "该消息正在处理中，无法移除"; }
```

---

##### Plan 确认 — `POST /sessions/{id}/plan/confirm`

**Request Body:** 无（服务端从当前等待状态获取上下文）

**Response (JSON):**

```typescript
// 200 OK — 开始自动执行计划，后续步骤通过流推送
{ status: "confirmed"; message_id: string; }
```

---

##### Plan 编辑 — `POST /sessions/{id}/plan/edit`

**Request Body (JSON):**

```typescript
{ plan_text: string; }               // 用户修改后的计划文本，必填
```

**Response (JSON):**

```typescript
// 200 OK — 替换计划后重新推送 plan.generated
{ status: "edited"; message_id: string; }

// 400 — plan_text 为空
{ error: "invalid_request"; message: "plan_text 不能为空"; }
```

---

##### 取消操作 — `POST /sessions/{id}/cancel`

统一处理 Plan 拒绝和 Build 终止。根据引擎当前模式自动选择处理方式。

**Request Body:** 无

**Response (JSON):**

```typescript
// 200 OK — plan 模式追加"计划已取消"后终止；build 模式直接终止
{ status: "cancelled"; message_id: string; }
```

---

##### Plan 回答 — `POST /sessions/{id}/plan/answer`

回答 `plan.question` 的追问。注入 LLM 上下文后继续当前 turn。

**Request Body (JSON):**

```typescript
{ answer: string; }                  // select: 选项文本; text: 自由文本
```

**Response (JSON):**

```typescript
// 200 OK — 回答已注入，引擎继续 turn 0
{ status: "answered"; message_id: string; }
```

---

##### Build 工具回传 — `POST /sessions/{id}/tool-result/{request_id}`

Build 模式下 `client.tool_request` 带 `requires_approval=true`，客户端展示确认 UI 后回传结果。

**确认并执行:**

```typescript
// Request: 工具执行结果
{ success: true; output: "..."; ... }

// Response 200
{ received: true; request_id: string; }
```

**跳过:**

```typescript
// Request: 跳过此步骤
{ skipped: true; tool_call_id: string; }

// Response 200
{ received: true; request_id: string; }
```

---

##### 工具结果回传 — `POST /sessions/{id}/tool-result/{request_id}`

服务端推送 `client.tool_request` 后，Client 本地执行完毕，回传结果。

**Request Body (JSON):**

```typescript
// 成功
{
  status: "success";
  output: string;                     // stdout 内容
  files?: {                           // 产生的文件（可选）
    path: string;
    content: string;
    action: "created" | "modified" | "deleted";
  }[];
  duration_ms: number;
}

// 失败
{
  status: "error";
  error: string;                      // 错误信息（含 stderr）
  exit_code?: number;
  duration_ms: number;
}
```

**Response (JSON):**

```typescript
// 200 OK — 结果已注入上下文，LLM 继续下一 turn
{ received: true; request_id: string; }

// 404
{ error: "not_found"; message: "request_id 不存在或已超时过期"; }

// 409 — 幂等保护
{ error: "duplicate"; message: "该 request_id 已收到过结果"; received_at: string; }
```

---

##### 流式数据块类型总览

所有数据块按**输出类型**分为四大类。NDJSON 每行一个完整 JSON，`\n` 分隔：

```
{"type":"agent.text","seq":42,"delta":"你好","turn":1,"message_id":"m1"}
{"type":"message.complete","seq":44,"message_id":"m1","summary":{…}}
```

| 分类 | 数据块 | 增量/完整 | 说明 |
|------|------|----------|------|
| **文本** | `agent.thinking` | 增量 (`delta`) | AI 思考过程，可折叠展示 |
| **文本** | `agent.text` | 增量 (`delta`) | AI 回复正文，打字机效果 |

| **工具** | — | — | 工具结果由客户端持有，`POST /tool-result` 回传给服务端注入 LLM 上下文 |
| **工具** | `client.tool_request` | 完整 | 要求 Client 端执行本地工具 |
| **工具** | `client.tool_timeout` | 完整 | Client 工具执行超时 |
| **Plan** | `plan.generated` | 完整 | 计划文本生成完毕，等待确认 |
| **Plan** | `plan.question` | 完整 | 计划阶段 LLM 向用户发起追问（选择题/填空） |
| **Plan** | `plan.question_timeout` | 完整 | 用户超时未回答 plan.question |



| **Build** | ``client.tool_request`` | 完整 | 步骤待用户确认 |

| **队列** | `queue.enqueued` | 完整 | 消息入队，含排位和队列长度 |
| **队列** | `queue.position_changed` | 完整 | 前方消息完成/取消，排位前移 |

| **Token** | `token.usage` | 完整 | 每次 LLM 调用完成后推送累计 input/output tokens |

| **系统** | `message.start` | 完整 | 消息开始处理 |
| **系统** | `message.complete` | 完整 | 处理完成，含 turn/token 摘要 |
| **系统** | `message.error` | 完整 | 处理异常 |
| **系统** | `system.status` | 完整 | 后端正在执行补救操作（重试、降级、回放等），告知用户引擎当前状态 |

> **流终止：** `message.complete` 或 `message.error`(fatal=true) 后，服务端关闭该流。

##### 完整数据块类型定义

```typescript
type StreamChunk =
  // ═══════════════════════════════════════════
  // 1. 思考与文本（所有模式实时推送）
  // ═══════════════════════════════════════════
  | {
      type: "agent.thinking";
      seq: number;
      delta: string;                    // 思考片段（增量）
      turn: number;
      message_id: string;
    }
  | {
      type: "agent.text";
      seq: number;
      delta: string;                    // 文本片段（增量）
      turn: number;
      message_id: string;
    }

  // ═══════════════════════════════════════════
  // 2. 工具调用（客户端执行，结果由客户端本地持有，无需服务端回推）
  // ═══════════════════════════════════════════
  | {
      type: "client.tool_request";
      seq: number;
      request_id: string;
      tool_name: string;
      input: Record<string, unknown>;
      message_id: string;
    }
  | {
      type: "client.tool_timeout";
      seq: number;
      request_id: string;
      message: string;
    }

  // ═══════════════════════════════════════════
  // 3. Plan 模式专用
  // ═══════════════════════════════════════════
  | {
      type: "plan.generated";
      seq: number;
      message_id: string;
      plan_text: string;
    }
  | {
      type: "plan.question";
      seq: number;
      message_id: string;
      question: string;
      options?: string[];
      input_type: "select" | "text";
      context?: string;
    }
  | { type: "plan.question_timeout"; seq: number; message_id: string; }

  // ═══════════════════════════════════════════
  // 4. 客户端工具请求（含 Build 确认）
  // ═══════════════════════════════════════════
  | {
      type: "client.tool_request";
      seq: number;
      request_id: string;
      message_id: string;
      tool_name: string;
      input: Record<string, unknown>;
      tool_call_id?: string;       // Build 模式: 关联的 tool_call
      step?: number;               // Build 模式: 步骤序号
      requires_approval?: boolean; // Build 模式: 是否需要用户确认
      reasoning?: string;          // Build 模式: LLM 解释
    }
  // ═══════════════════════════════════════════
  // 5. 队列事件
  // ═══════════════════════════════════════════
  | {
      type: "queue.enqueued";
      seq: number;
      message_id: string;
      queue_position: number;
      queue_size: number;
      ahead_message_id: string | null;
    }
  | {
      type: "queue.position_changed";
      seq: number;
      message_id: string;
      new_position: number;
      queue_size: number;
    }

  // ═══════════════════════════════════════════
  // 6. Token 统计
  // ═══════════════════════════════════════════
  | {
      type: "token.usage";
      seq: number;
      message_id: string;
      tokens_in: number;           // 累计 input tokens
      tokens_out: number;          // 累计 output tokens
    }

  // ═══════════════════════════════════════════
  // 7. 生命周期 & 系统
  // ═══════════════════════════════════════════
  | {
      type: "message.start";
      seq: number;
      message_id: string;
      mode: "ask" | "plan" | "build";
      scene_mode: "office" | "code";
      workspace: string;
    }
  | {
      type: "message.complete";
      seq: number;
      message_id: string;
      summary: { turns: number; tokens_in: number; tokens_out: number; duration_ms: number; tool_calls_count: number; };
    }
  | {
      type: "message.error";
      seq: number;
      message_id: string;
      message: string;
      code: string;
      fatal: boolean;
      turn?: number;
    }

  // ═══════════════════════════════════════════
  // 8. 系统状态通知（后端补救措施透明度）
  // ═══════════════════════════════════════════
  | {
      type: "system.status";
      seq: number;
      message_id: string;
      code: "llm_retrying" | "llm_stream_interrupted" | "llm_empty_response"
          | "llm_format_error" | "llm_rate_limited"
          | "token_compressing" | "db_retrying"
          | "client_disconnected" | "stream_buffer_replaying"
          | "session_recovering"
          | "mcp_reconnecting" | "mcp_reconnected" | "mcp_permanently_down"
          | "mcp_server_not_installed" | "mcp_server_not_ready"
          | "mcp_tool_not_found" | "mcp_tool_timeout" | "mcp_tool_error"
          | "mcp_connection_lost";
      message: string;                     // 给人看的中文描述，如 "AI 服务连接超时，正在重试（第 2/3 次，4 秒后）..."
      turn?: number;                       // 关联的 turn（可选）
      detail?: string;                     // 补充信息（可选），如异常原文截取
      attempt?: number;                    // 当前重试次数（可选，重试类场景用）
      max_attempts?: number;               // 最大重试次数（可选）
    }
```

##### 工具分类：客户端工具 vs 客户端 MCP

所有可调用工具均由客户端执行，按来源分为两类：

| | 客户端工具 (Client Tools) | 客户端 MCP (Client MCP Tools) |
|--|--------------------------|------------------------------|
| **执行位置** | Electron 本地 | Electron 本地（通过 MCP 子进程或 HTTP 连接） |
| **数据块流** | `client.tool_request` → Client 执行 → `POST /tool-result` → 注入 LLM 上下文 | `client.tool_request` → Client 转发到 MCP → `POST /tool-result` → 注入 LLM 上下文 |
| **是否需要 Client 在线** | 是（断开则超时） | 是（断开则超时） |
| **超时** | 120s | 120s |
| **典型工具** | `bash`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `skill` | `mh6_get_weather`, `mh9_geocoder`, `mh1_search_issues` |
| **注册方式** | `POST /sessions` 时由 Client 上报完整工具定义（name + description + input_schema），服务端存为会话元数据，会话生命周期内不变 | Client 安装 MCP 后 spawn 进程 → `tools/list` → `POST /mcp/tools` 上报工具清单，服务端存储到 `user_mcp_servers.tools`（按 user+server 持久化，跨 session 共享） |
| **安装方式** | 客户端内置，无需安装 | `POST /mcp/install` 获取配置 → 客户端 spawn/connect → 完成后工具立即可用 |

**完整链路对比：**

```
客户端工具:
  LLM 决定调用 bash / skill
      → client.tool_request (前端收到，调用 shell 执行 / 读取本地 SKILL.md)
      → [Electron 本地执行中...]
      → POST /tool-result/{request_id} (回传结果)
      → 注入 LLM 上下文，继续推理
      → 超时 120s 则推送 client.tool_timeout

客户端 MCP:
  client 安装 MCP 服务 → spawn 子进程 / 建立 HTTP 连接
      → tools/list → POST /mcp/tools 上报
  LLM 决定调用 mh6_get_weather
      → client.tool_request (前端收到)
      → [Electron 转发到 MCP 进程/服务]
      → POST /tool-result/{request_id} (回传结果)
      → 注入 LLM 上下文，继续推理
```

> **核心区别：** 所有工具执行权均在客户端。`client.tool_request` 是服务端把执行权**委派**给前端的桥接数据块——前端必须响应，否则引擎卡在 WAITING_SYNC 直到超时。客户端工具与客户端 MCP 的区别仅在于：前者由 Electron 本地直接执行，后者由 Electron 转发到 MCP 子进程或远端服务执行。

##### Plan 模式数据块详解

Plan 模式共 2 个数据块，围绕"生成计划 → 用户决策 → 自动执行"这一条线。

**`plan.generated`** — LLM 生成了执行计划

Plan 模式下 turn 0 完成后推送，流在此**暂停**，引擎切到 WAITING_SYNC。

```typescript
{
  type: "plan.generated";
  seq: number;
  message_id: string;
  plan_text: string;               // 完整计划（Markdown，含步骤列表），直接渲染
}
```

**前端：** 展示计划文本 + **[确认]** **[编辑]** **[拒绝]** 三个按钮。

---

##### Build 模式数据块详解

Build 模式复用了 `client.tool_request` 数据块，通过 `requires_approval` 字段区分：LLM 每个 tool_use 都会触发 `client.tool_request`（带 `requires_approval=true`）。流在此**暂停**，引擎切到 WAITING_SYNC。

**前端：** 当 `requires_approval=true` 时，展示步骤卡片（工具名、参数预览、step 序号、reasoning），附带 **[确认]** **[跳过]** **[终止]** 三个按钮。确认后执行工具并回传结果；跳过回传 `{skipped: true}`。

> 详细流程见 [1.6 Build 模式子流程](#16-build-模式子流程)。

---

##### 客户端处理汇总

客户端收到每种 `StreamChunk` 后，需要做的处理和需要调用的接口汇总如下。

**一、文本类（增量渲染，无需调接口）**

| 数据块 | 客户端处理 | 调用的接口 |
|--------|-----------|-----------|
| `agent.thinking` | 将 `delta` 追加到思考缓冲区，渲染在可折叠面板中 | 无 |
| `agent.text` | 将 `delta` 追加到回复缓冲区，打字机效果逐字渲染 | 无 |

**二、工具类**

| 数据块 | 客户端处理 | 调用的接口 |
|--------|-----------|-----------|
（客户端执行完工具即知结果，无需服务端回推）
| **`client.tool_request`** | `requires_approval=false`：直接在本地执行工具，回传结果<br>`requires_approval=true`（Build 模式）：先展示确认卡片（工具名、参数、step、reasoning），**[确认]** 后执行并回传结果，**[跳过]** 回传 `{skipped:true}`，**[终止]** 调 cancel | **`POST /sessions/{id}/tool-result/{request_id}`**<br>成功: `{ success:true, output, ... }`<br>跳过: `{ skipped:true, tool_call_id }`<br>失败: `{ success:false, error, ... }` |
| `client.tool_timeout` | 渲染超时提示，标记该工具请求为"已超时" | 无（服务端已判定超时，无需回传） |

**三、Plan 模式专用（需用户交互）**

| 数据块 | 客户端处理 | 调用的接口 |
|--------|-----------|-----------|
| **`plan.generated`** | 渲染完整计划文本（Markdown），展示三个按钮：**[确认]** **[编辑]** **[拒绝]** | 确认 → **`POST /sessions/{id}/plan/confirm`**（无 Body）<br>编辑 → **`POST /sessions/{id}/plan/edit`** Body: `{ plan_text }`（服务端替换后重新推送 `plan.generated`，可反复编辑）<br>拒绝 → **`POST /sessions/{id}/cancel`**（无 Body，LLM 追加"计划已取消"后终止） |
| **`plan.question`** | 根据 `input_type` 渲染不同 UI：<br>• `select` → 选项卡片列表（单选）<br>• `text` → 文本输入框 | **`POST /sessions/{id}/plan/answer`** Body: `{ answer }`（select 传选项文本、text 传输入字符串） |
| `plan.question_timeout` | 渲染"用户超时未回答"提示 | 无（服务端已注入 `[用户未回应此问题，请跳过并继续]`，LLM 自行继续） |

**五、队列事件**

| 数据块 | 客户端处理 | 调用的接口 |
|--------|-----------|-----------|
| `queue.enqueued` | 记录 `queue_position`，渲染排队 UI："排队中，前方还有 N 条消息"。如果 `ahead_message_id` 非空则显示前方消息预览。按 `queue_position` 排序展示排队列表。 | 无（也可调用 `GET /queue` 获取完整队列快照补充渲染） |
| `queue.position_changed` | 更新当前消息的排位（`new_position`），排队列表序号前移。当 `new_position == 1` 时提示"即将处理"。 | 无 |
| **`[取消排队]` 按钮** | — | **`DELETE /sessions/{id}/queue/{msg_id}`**（仅 `pending` 状态可取消，processing 不可取消） |

**六、生命周期 / 系统**

| 数据块 | 客户端处理 | 调用的接口 |
|--------|-----------|-----------|
| `message.start` | 初始化消息 UI 容器，记录 `mode`、`scene_mode`、`workspace`，准备接收后续流数据块。将当前 `seq` 置为基准 | 无 |
| `token.usage` | 实时更新当前消息的累计 token 消耗（input / output），可在 UI 顶部或底部展示 | 无 |
| `message.complete` | 渲染摘要信息（turns、tokens、耗时、工具调用次数），标记消息为"已完成"。**此数据块后服务端关闭流** | 无 |
| `message.error` | 渲染错误信息。`fatal: true` → 标记消息终止，展示错误码和描述；`fatal: false` → 展示警告但仍等待后续数据块 | 无（但 `fatal` 错误后可能需要用户手动重发消息） |
| `system.status` | 以 toast / 内联提示条渲染 `message` 文本（灰色提示条 + loading 图标，3~5 秒后自动消失）。同一 `code` 的新 chunk 覆盖旧提示。`detail` 可折叠展示（点击展开）。不需要用户交互 | 无（纯渲染，引擎自行继续） |

**七、断线重连**

当 NDJSON 流因网络中断而断开时，客户端需维护当前消息 ID 和最后收到的 `seq`，在流的 `onerror` / `onclose`（非正常关闭）时自动发起重连：

```
Client 记录最后收到的 seq
  → onerror 自动触发重连
  → GET /sessions/{id}/stream?since_seq={last_seq}&since_message_id={current_msg_id}
  → 服务端从 StreamBuffer 回放 seq > last_seq 的所有数据块
  → 追上实时后继续增量推送
```

多次重试失败后展示"连接已断开，点击重试"兜底按钮。StreamBuffer 机制见 [1.8.5 流连接断开时的缓冲回放](#185-流连接断开时的缓冲回放)。

**八、客户端需要主动调用的接口汇总（按触发源）**

| 触发源 | 调用的接口 |
|--------|-----------|
| `client.tool_request`（requires_approval=false） | `POST /sessions/{id}/tool-result/{request_id}`（执行结果） |
| `client.tool_request`（requires_approval=true, Build） | `POST /sessions/{id}/tool-result/{request_id}`（确认: 执行结果; 跳过: `{skipped:true}`） |
| 用户点 **[确认计划]** | `POST /sessions/{id}/plan/confirm` |
| 用户点 **[编辑]** → 修改 → 提交 | `POST /sessions/{id}/plan/edit` |
| 用户点 **[拒绝]**（Plan）/ **[终止]**（Build） | `POST /sessions/{id}/cancel` |
| 用户回答 `plan.question` | `POST /sessions/{id}/plan/answer` |
| 用户点 **[跳过]**（Build） | `POST /sessions/{id}/tool-result/{request_id}` |
| 用户点 **[取消排队]** | `DELETE /sessions/{id}/queue/{msg_id}` |
| 流断开（自动） | `GET /sessions/{id}/stream?since_seq=N` |

其余 `agent.thinking`、`agent.text`、`system.status`、`message.*` 等均为纯渲染，不需要客户端回调任何接口。

---

#### 1.9.3 错误码参考

| code | HTTP 状态码 | 说明 |
|------|-----------|------|
| `queue_full` | 429 | 队列已满（最多 10 条） |
| `session_archived` | 410 | 会话已归档 |
| `not_found` | 404 | 消息/request_id 不存在 |
| `already_processing` | 409 | 消息已在处理中，不可移除 |
| `duplicate` | 409 | 重复回传工具结果 |
| `invalid_request` | 400 | 请求参数不合法 |
| `max_turns_exceeded` | — | 流数据块，轮次耗尽 |
| `execution_timeout` | — | 流数据块，单条消息总耗时超 300s |
| `loop_detected` | — | 流数据块，检测到重复操作死循环 |
| `content_filter` | — | 流数据块，内容被安全策略拦截 |
| `auth_failed` | — | 流数据块，LLM API 认证失败 |
| `rate_limited` | — | 流数据块，LLM API 速率限制 |

---

#### 1.9.4 典型交互时序

##### Ask 模式（"什么是闭包？"）

```
Client                              Server
  │                                    │
  │─ POST /messages {"content":"...", mode:"ask", ...} ──→│
  │◄══ Content-Type: application/x-ndjson ════════════════│
  │◄── {"type":"message.start","seq":0,"message_id":"m1","mode":"ask"}
  │◄── {"type":"agent.text","seq":1,"delta":"闭包是…"}
  │◄── {"type":"agent.text","seq":2,"delta":"…函数的…"}
  │◄── {"type":"message.complete","seq":3, ...}
  │                                    │  ← 流关闭
```

##### Plan 模式（"帮我搭建一个 React 项目"）

```
Client                              Server
  │                                    │
  │─ POST /messages {"mode":"plan",...} ─────────→│
  │◄══ NDJSON stream ════════════════════════════│
  │◄── {"type":"message.start","mode":"plan"}
  │◄── {"type":"plan.generated","plan_text":"..."}
  │   ═══ 流暂停，等待用户决策 ═══       │
  │                                    │
  │─ POST /plan/confirm                │ ← 用户点击"确认计划"
  │◄─ 200 {status:"confirmed"}         │
  │                                    │
  │◄── {"type":"message.start"}       │  ← 流继续
  │◄── {"type":"agent.text","delta":"项目已创建完成"}
  │◄── {"type":"message.complete",...}
  │                                    │  ← 流关闭
```

##### Build 模式（"把 src/utils.ts 重构并跑通测试"）

```
Client                              Server
  │                                    │
  │─ POST /messages {"mode":"build",...} ─────────→│
  │◄══ NDJSON stream ═════════════════════════════│
  │◄── {"type":"message.start","mode":"build"}
  │◄── {"type":"client.tool_request","tool_name":"read_file","step":1,"requires_approval":true}
  │   ═══ 流暂停, 客户端展示确认卡片 ═══     │
  │                                    │
  │─ POST /tool-result/req1            │ ← 用户点击"确认"→执行→回传
  │  {success:true, output:"..."}      │
  │◄── {"type":"client.tool_request","tool_name":"edit_file","step":2,"requires_approval":true}
  │   ═══ 流暂停, 客户端展示确认卡片 ═══     │
  │                                    │
  │─ POST /tool-result/req2            │ ← 用户点击"跳过"
  │  {skipped:true, tool_call_id:"..."}│
  │◄── {"type":"client.tool_request","tool_name":"bash","step":3,"requires_approval":true}  │ ← 流继续
  │   ═══ 流暂停, 客户端展示确认卡片 ═══     │
  │                                    │
  │─ POST /tool-result/req3            │ ← 用户点击"确认"
  │  {success:true, output:"ok"}
  │◄── {"type":"message.complete",...}
  │                                    │  ← 流关闭
```

##### Client 端工具执行

```
Client                              Server
  │                                    │
  │◄══ NDJSON stream ═════════════════│
  │◄── {"type":"client.tool_request",
  │      "request_id":"r1",
  │      "tool_name":"bash",
  │      "input":{"command":"npm test"}}
  │                                    │
  │  … Client 本地执行 npm test …      │
  │                                    │
  │─ POST /tool-result/r1              │
  │   {status:"success",               │
  │    output:"Tests: 5 passed",       │
  │    duration_ms: 3200}              │
  │◄─ 200 {received:true}              │
  │                                    │
  │◄── {"type":"agent.text","delta":"测试全部通过"}
```

##### 消息排队（发消息时前序消息还在处理中）

```
Client 发送 msg2，此时 msg1 正在 PROCESSING

msg2 Client                          Server
  │                                    │
  │─ POST /messages {"content":"msg2",…}
  │◄══ NDJSON stream ═════════════════│
  │◄── {"type":"queue.enqueued",
  │      "message_id":"msg2",
  │      "queue_position":1,
  │      "queue_size":1,
  │      "ahead_message_id":"msg1"}
  │                                    │
  │   … msg1 处理完成，引擎 dequeue msg2 …
  │                                    │
  │◄── {"type":"message.start","message_id":"msg2",…}
  │◄── {"type":"agent.text",…}
  │◄── ...
  │◄── {"type":"message.complete",…}
  │                                    │  ← 流关闭
```

```
Client 发送 msg3，此时 msg1 正在 PROCESSING，msg2 排在前面

msg3 Client                          Server
  │                                    │
  │─ POST /messages {"content":"msg3",…}
  │◄══ NDJSON stream ═════════════════│
  │◄── {"type":"queue.enqueued",
  │      "queue_position":2,
  │      "queue_size":2,
  │      "ahead_message_id":"msg1"}
  │                                    │
  │   … msg2 被用户取消，触发 renumber …
  │                                    │
  │◄── {"type":"queue.position_changed",
  │      "message_id":"msg3",
  │      "new_position":1,
  │      "queue_size":1}
  │                                    │
  │   … msg1 完成，msg3 出队 …
  │                                    │
  │◄── {"type":"message.start","message_id":"msg3",…}
  │◄── ...
  │◄── {"type":"message.complete",…}
  │                                    │  ← 流关闭
```
---

> **下一节**：上下文管理系统（待用户确认本节省后继续展开）

---

<a id="2-mcp-工具集成"></a>

## 2. MCP 工具集成

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

## 3. Skill 集成

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

## 4. 数据库与种子数据运维

### 4.1 种子数据重新初始化

种子数据文件 (`server/skill-hub.json` / `server/mcp-hub.json`) 修改后，需先清表再重启服务（种子函数仅在目标表为空时插入，幂等设计）。

**步骤：**

1. 连接 PostgreSQL 数据库，执行清表：

```sql
TRUNCATE TABLE skill_hub, mcp_hub CASCADE;
```

2. 重启 FastAPI 服务，`seed_hub_data()` 在 lifespan 启动时自动重新插入 JSON 数据。

### 4.2 Schema 重建

当 `server/db/models.py` 有结构变更时，通过 Alembic 重建：

```bash
cd server
alembic downgrade base     # 删除所有表（保留数据库本身）
alembic upgrade head       # 重新创建所有表（含 pgvector 扩展）
```

生成新 migration：

```bash
cd server
alembic revision --autogenerate -m "描述变更内容"
alembic upgrade head
```

### 4.3 手动触发种子脚本

不重启服务的情况下，手工跑种子逻辑：

```bash
cd server
python -c "
import asyncio
from db.engine import create_engine
from db.seed import seed_default_user, seed_hub_data
from config import settings

async def main():
    engine, sf = create_engine(settings.database_url)
    await seed_hub_data(sf)
    uid = await seed_default_user(sf)
    print(f'Seed complete. Default user: {uid}')
    await engine.dispose()

asyncio.run(main())
"
```

**关键点：**

| 项目 | 说明 |
|------|------|
| 种子函数位置 | `server/db/seed.py` — `seed_default_user()` / `seed_hub_data()` |
| 触发时机 | FastAPI lifespan 启动时自动调用 |
| 幂等性 | 默认用户按 ID 查找去重；Hub 数据按 `count == 0` 判断是否插入 |
| 强制重写 | 手动 `TRUNCATE` 目标表后再启动服务 |
| 数据库连接串 | `server/config.py` + `server/alembic.ini`，两处需保持一致 |

---

<a id="5-记忆模块"></a>

## 5. 记忆模块

### 5.1 架构概览

记忆模块管理用户的长期记忆和强制性规则，跨 session 共享，生命周期独立于会话。

核心设计：
- **记忆（Memories）**：四种类型（user / feedback / project / reference），AI 自动写入 + 用户可手动编辑
- **规则（Rules）**：用户手动定义的强制性约束，AI 只读不可写
- **存储**：PostgreSQL，服务端管理，复用现有仓储模式
- **注入**：Rules **全文常驻** system prompt（强制性约束，必须始终可见）；Memories 索引常驻 + `load_memory` 按需加载完整内容，与 Skill 二级加载模式一致

```
┌─ 记忆模块 (用户级, 跨 session) ────────────────────────────┐
│                                                             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
│   │  user    │  │ feedback │  │ project  │  │ reference │  │
│   │  用户偏好  │  │ 行为准则  │  │ 项目上下文 │  │ 外部资源   │  │
│   └──────────┘  └──────────┘  └──────────┘  └───────────┘  │
│         ↑ DB: memories (user_id, name, type, content)       │
│                                                             │
│   ┌──────────┐                                              │
│   │  rules   │  ← 用户手动定义，AI 只读                       │
│   └──────────┘                                              │
│         ↑ DB: rules (user_id, name, content, priority)      │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  LLM 工具: load_memory / write_memory / delete_memory│  │
│   │  全部为服务端工具，直接读写 DB                          │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  注入: system prompt 末尾                             │  │
│   │  <rules> → <available_memories>                      │  │
│   │  MEMORY.md 索引从 DB 动态拼接                         │  │
│   └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 数据模型

`memories` 表 — 一条记录 = 一条记忆（如 `user_role`）：

```sql
CREATE TABLE memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),

    name        VARCHAR(200) NOT NULL,          -- 记忆名称，如 "user_role"
    description VARCHAR(500) NOT NULL,           -- 一行描述，用于 MEMORY.md 索引

    type        VARCHAR(20) NOT NULL             -- user | feedback | project | reference
                CHECK (type IN ('user','feedback','project','reference')),

    content     TEXT NOT NULL,                    -- 记忆正文（Markdown）

    protected   BOOLEAN NOT NULL DEFAULT false,  -- true 时 AI 不可修改或删除

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, name)
);

CREATE INDEX idx_memories_user ON memories(user_id, updated_at DESC);
CREATE INDEX idx_memories_type ON memories(user_id, type);
```

**四种记忆类型：**

| type | 用途 | 保存时机 | 示例 |
|------|------|---------|------|
| `user` | 用户角色、偏好、技能水平 | 了解到用户背景、偏好时 | `user_role` — "Senior backend developer, prefers Python" |
| `feedback` | 用户纠正或确认的做法 | 用户说"不要这样做"或"这样刚好"时 | `feedback_testing` — "Always write integration tests, no mocks for DB" |
| `project` | 项目目标、截止日期、决策 | 了解到项目动机、约束、决策时 | `project_migration` — "Auth rewrite driven by compliance requirements" |
| `reference` | 外部系统信息 | 了解到外部资源地址时 | `reference_bugs` — "Pipeline bugs tracked in Linear project INGEST" |

**什么不保存：**
- 可从代码推导的信息（文件路径、架构、Git 历史）
- 已在 CLAUDE.md 中的内容
- 临时状态、当前会话上下文
- 工具调用的技术细节

### 5.3 MEMORY.md 索引生成

`MEMORY.md` 不是一条 DB 记录，而是每次从整表动态拼接的索引字符串。

```python
async def build_memory_index(user_id: UUID) -> str:
    """从 DB 组装 MEMORY.md 索引，按更新时间倒序，截断至 200 行"""
    rows = await db.fetch(
        """SELECT name, description FROM memories
           WHERE user_id = $1
           ORDER BY updated_at DESC
           LIMIT 200""",
        user_id
    )
    if not rows:
        return "暂无记忆。"
    lines = []
    for r in rows:
        lines.append(f"- {r['name']} — {r['description']}")
    return "\n".join(lines)
```

**拼接结果示例：**

```markdown
- user_role — Senior backend developer, prefers Python
- feedback_testing — Always write integration tests, no mocks for DB
- project_migration — Auth middleware rewrite driven by compliance
```

每条约 50 tokens，200 行上限约 10000 tokens。超出部分 DB 保留但索引中不展示。

### 5.4 Rules 表

Rules 与 Memory 分开，因为行为语义不同——Rules 用户手动定义，AI 只读。

```sql
CREATE TABLE rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),

    name        VARCHAR(200) NOT NULL,          -- 规则名称，如 "always-typescript"
    description VARCHAR(500) NOT NULL,           -- 索引行摘要

    content     TEXT NOT NULL,                    -- 规则正文（Markdown）
    priority    INT NOT NULL DEFAULT 0,          -- 越高越靠前注入

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE(user_id, name)
);

CREATE INDEX idx_rules_user ON rules(user_id, priority DESC);
```

**与 Memory 的核心区别：**

| | Memory | Rules |
|--|--------|-------|
| 写入者 | AI 自动 + 用户手动 | **仅用户手动** |
| 性质 | 经验、偏好、上下文（参考） | 强制性约束（必须遵守） |
| AI 可写 | 是（受 `protected` 限制） | **否** |
| 可用工具 | `load_memory` / `write_memory` / `delete_memory` | 无（全文注入） |
| 注入格式 | `<available_memories>` | `<rules>`（前置，优先级更高，全文） |

### 5.5 工具定义

三个 Memory 工具，全部为**服务端工具**（不走 `client.tool_request`）。Rules 无工具，直接全文注入 system prompt：

```python
MEMORY_TOOLS = [
    {
        "name": "load_memory",
        "description": (
            "加载指定记忆的完整内容。"
            "当对话涉及用户偏好、历史决策、项目背景时调用此工具获取上下文。"
            "记忆名必须从 system prompt 的 <available_memories> 索引中选取，不要凭空编造。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "记忆名称，从 <available_memories> 索引中选取，如 'user_role'"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "write_memory",
        "description": (
            "写入或更新一条记忆。在了解到以下信息时应主动保存：\n"
            "- user 类：用户角色、偏好、技能水平\n"
            "- feedback 类：用户纠正或确认了某种做法\n"
            "- project 类：项目目标、截止日期、架构决策\n"
            "- reference 类：外部系统信息\n"
            "同名记忆存在时自动更新；保存前检查内容是否已有且一致，避免重复写入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "记忆名称，如 'feedback_testing'"},
                "description": {"type": "string", "description": "一行描述，用于 MEMORY.md 索引"},
                "type": {"type": "string", "enum": ["user", "feedback", "project", "reference"]},
                "content": {"type": "string", "description": "记忆正文（Markdown）"}
            },
            "required": ["name", "description", "type", "content"]
        }
    },
    {
        "name": "delete_memory",
        "description": "删除一条记忆。当发现记忆内容已过时、错误，或用户明确要求删除时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要删除的记忆名称"}
            },
            "required": ["name"]
        }
    }
]
```

```python
# ToolDispatcher.classify():
SERVER_MEMORY_TOOLS = {"load_memory", "write_memory", "delete_memory"}
# → _execute_tool_chunk() 走 SERVER 分支，直接读写 DB
```

#### 5.5.1 工具请求/返回

**load_memory**

```json
// 请求
{ "name": "user_role" }

// 返回 — 成功
{ "status": "ok", "name": "user_role", "content": "# User Role\n\nSenior backend developer..." }

// 返回 — 不存在
{ "status": "not_found", "name": "unknown" }
```

**write_memory**

```json
// 请求
{
  "name": "feedback_testing",
  "description": "Always write integration tests",
  "type": "feedback",
  "content": "# Feedback: Testing\n\n..."
}

// 返回 — 新建
{ "status": "created", "name": "feedback_testing" }

// 返回 — 更新（同名 upsert）
{ "status": "updated", "name": "feedback_testing" }

// 返回 — 冷却期内跳过
{ "status": "skipped", "name": "feedback_testing", "reason": "1 分钟内已更新，跳过" }
```

**delete_memory**

```json
// 请求
{ "name": "obsolete_note" }

// 返回 — 成功
{ "status": "deleted", "name": "obsolete_note" }

// 返回 — 不存在
{ "status": "not_found", "name": "unknown" }

// 返回 — 受保护
{ "status": "rejected", "name": "immutable", "reason": "该记忆被标记为 protected，AI 不可删除" }
```

### 5.6 Query Loop 注入

#### 5.6.1 System Prompt 注入位置

在 `ContextManager.build()` 中，`<available_skills>` 之后附加 Rules 和 Memories：

```
System Prompt 结构（尾部）:
  ...
  <available_skills>...</available_skills>     ← 已有

  <rules>                                       ← 新增（前置，全文）
  以下规则由用户设定，具有最高优先级，必须严格遵守：

  ## always-typescript
  始终使用 TypeScript，禁止 any 类型
  （规则正文完整内容……）
  </rules>

  <available_memories>                          ← 新增
  以下是已保存的记忆，可调用 load_memory(name) 加载完整内容，调用 write_memory / delete_memory 管理：
  - user_role — Senior backend developer, prefers Python
  - feedback_testing — Always write integration tests
  </available_memories>
```

#### 5.6.2 LLM 使用流程

```
1. 收到用户消息 → system prompt 中已有 <rules>（全文） + <available_memories> 索引
2. LLM 自行判断
     → 需要记忆上下文 → load_memory("user_role")
3. 工具执行（服务端 DB 查询）→ 返回完整 content
4. LLM 获得完整上下文，继续推理
5. LLM 发现值得保存的信息 → write_memory(name, description, type, content)
     → 服务端 upsert，下次对话即刻生效
6. LLM 发现过时或错误的记忆 → delete_memory(name)
```

#### 5.6.3 Per-Message Loop 集成点

```python
# context.build() — 每次构建上下文时动态拼接索引
ctx = await context_manager.build(session_id, turn, mode)
# → ctx.system_prompt 末尾已包含 <rules>（全文） + <available_memories>

# 工具注册
ctx.server_tools = [
    *SKILL_TOOLS,
    *MEMORY_TOOLS,       # load_memory / write_memory / delete_memory
]

# 执行 — ToolDispatcher.classify() 归入 SERVER → 直接读写 DB
```

### 5.7 记忆生命周期

#### 5.7.1 写入

LLM 调用 `write_memory(name, description, type, content)`，服务端 upsert + 冷却保护：

```python
async def write_memory(user_id, name, description, type, content):
    """同名记忆 upsert，1 分钟内不允许再次更新"""
    existing = await db.fetchrow(
        "SELECT id, updated_at FROM memories WHERE user_id=$1 AND name=$2",
        user_id, name
    )
    if existing:
        elapsed = (datetime.utcnow() - existing["updated_at"]).total_seconds()
        if elapsed < 60:
            return {"status": "skipped", "reason": "1 分钟内已更新，跳过"}
        await db.execute(
            """UPDATE memories SET description=$1, type=$2, content=$3,
               updated_at=now() WHERE id=$4""",
            description, type, content, existing["id"]
        )
        return {"status": "updated", "name": name}
    else:
        await db.execute(
            """INSERT INTO memories (user_id, name, description, type, content)
               VALUES ($1,$2,$3,$4,$5)""",
            user_id, name, description, type, content
        )
        return {"status": "created", "name": name}
```

#### 5.7.2 更新

| 入口 | 方式 |
|------|------|
| AI 自动 | `write_memory(...)` → upsert → 冷却保护 |
| 用户手动 | 客户端面板编辑 → `PATCH /memories/{id}` |

#### 5.7.3 删除

| 入口 | 方式 | 限制 |
|------|------|------|
| AI 自动 | `delete_memory(name)` | `protected=true` 时拒绝 |
| 用户手动 | 客户端面板 → `DELETE /memories/{id}` | 无限制 |

```python
async def delete_memory(user_id, name):
    mem = await db.fetchrow(
        "SELECT id, protected FROM memories WHERE user_id=$1 AND name=$2",
        user_id, name
    )
    if not mem:
        return {"status": "not_found", "name": name}
    if mem["protected"]:
        return {"status": "rejected", "reason": "该记忆被标记为 protected，AI 不可删除"}
    await db.execute("DELETE FROM memories WHERE id=$1", mem["id"])
    return {"status": "deleted", "name": name}
```

#### 5.7.4 维护策略

| 策略 | 说明 |
|------|------|
| **索引截断** | `build_memory_index()` LIMIT 200，超出 DB 保留但索引不展示 |
| **冷却保护** | 同条记忆 1 分钟内不允许再次 AI 更新 |
| **protected 标记** | 用户可在 UI 中标记，AI 不可修改或删除 |

### 5.8 异常处理

| 场景 | 处理 |
|------|------|
| `write_memory` 1 分钟内重复 | 返回 `skipped` |
| `delete_memory` 目标是 protected | 返回 `rejected`，告知原因 |
| `load_memory` 文件不存在 | 返回 "记忆 '{name}' 不存在" |
| 同名记忆冲突 | `write_memory` 自动 upsert（冷却期内跳过） |
| 索引为空 | 返回 "暂无记忆。" |

### 5.9 Rules 管理 API

**GET /rules** — 获取当前用户所有规则，按 `priority DESC` 排序

```json
// 请求：无

// 返回
{
  "rules": [
    {
      "id": "uuid",
      "name": "always-typescript",
      "description": "始终使用 TypeScript，禁止 any 类型",
      "content": "# Rule\n\n始终使用 TypeScript...",
      "priority": 10,
      "created_at": "2026-07-01T08:00:00Z",
      "updated_at": "2026-07-15T12:30:00Z"
    }
  ]
}
```

**GET /rules/{id}** — 获取单条规则详情

```json
// 请求：无

// 返回
{
  "id": "uuid",
  "name": "always-typescript",
  "description": "始终使用 TypeScript，禁止 any 类型",
  "content": "# Rule\n\n始终使用 TypeScript...",
  "priority": 10,
  "created_at": "2026-07-01T08:00:00Z",
  "updated_at": "2026-07-15T12:30:00Z"
}
```

**POST /rules** — 新建 / 编辑规则（按 `name` upsert：同名则更新，不同名则新建）

```json
// 请求
{
  "name": "always-typescript",
  "description": "始终使用 TypeScript，禁止 any 类型",
  "content": "# Rule\n\n始终使用 TypeScript...",
  "priority": 10
}

// 返回 — 新建
{ "id": "uuid", "name": "always-typescript", "created_at": "2026-07-27T10:00:00Z" }

// 返回 — 更新（同名已存在）
{ "id": "uuid", "name": "always-typescript", "updated_at": "2026-07-27T11:00:00Z" }
```

**DELETE /rules/{id}** — 删除规则

```json
// 请求：无

// 返回
{ "status": "deleted", "id": "uuid" }
```

**客户端"记忆配置"面板分两个 Tab：**

| Tab | 操作 | 接口 |
|-----|------|------|
| **Rules** | 新建 / 编辑 / 删除 / 拖拽排序(priority) | `POST/DELETE /rules` |
| **记忆** | 查看 / 编辑 / 删除 / 标记 protected | `POST/DELETE /memories` |

### 5.10 Memory 管理 API

**GET /memories** — 获取当前用户所有记忆，按 `updated_at DESC` 排序，支持 `?type=user|feedback|project|reference` 过滤

```json
// 请求：无（可选查询参数 ?type=feedback）

// 返回
{
  "memories": [
    {
      "id": "uuid",
      "name": "user_role",
      "description": "Senior backend developer, prefers Python",
      "type": "user",
      "content": "# User Role\n\n...",
      "protected": false,
      "created_at": "2026-07-01T08:00:00Z",
      "updated_at": "2026-07-20T14:00:00Z"
    }
  ]
}
```

**GET /memories/{id}** — 获取单条记忆详情

```json
// 请求：无

// 返回
{
  "id": "uuid",
  "name": "user_role",
  "description": "Senior backend developer, prefers Python",
  "type": "user",
  "content": "# User Role\n\n...",
  "protected": false,
  "created_at": "2026-07-01T08:00:00Z",
  "updated_at": "2026-07-20T14:00:00Z"
}
```

**POST /memories** — 新建 / 编辑记忆（按 `name` upsert：同名则更新，不同名则新建），`protected` 标记在此设置

```json
// 请求
{
  "name": "user_role",
  "description": "Senior backend developer, prefers Python",
  "type": "user",
  "content": "# User Role\n\n...",
  "protected": false
}

// 返回 — 新建
{ "id": "uuid", "name": "user_role", "created_at": "2026-07-27T12:00:00Z" }

// 返回 — 更新（同名已存在）
{ "id": "uuid", "name": "user_role", "updated_at": "2026-07-27T12:00:00Z" }
```

**DELETE /memories/{id}** — 删除记忆（用户手动，不受 protected 限制）

```json
// 请求：无

// 返回
{ "status": "deleted", "id": "uuid" }
```


### 5.11 与 Skill 模块的设计对比

| | Skill | Memory |
|--|-------|--------|
| **工具数量** | 1 个（`skill`） | 3 个（load/write/delete） |
| **工具类型** | CLIENT（Client 读取本地 SKILL.md） | SERVER（服务端直接读 DB） |
| **索引格式** | `<available_skills>` XML | `<available_memories>` Markdown 列表 / `<rules>` 全文 |
| **写入者** | 用户安装/卸载 | AI 自动 + 用户手动 |
| **生命周期** | 安装态（跨 session） | 同，跨 session |
| **存储** | skill-hub.json + 客户端文件系统 | PostgreSQL（服务端单一来源） |

---

<a id="6-遗留问题"></a>

## 6. 遗留问题

### 6.1 工具目录膨胀导致 LLM 稳定性下降

#### 问题描述

内置工具、Skills、MCP 工具、Memory、Rules 最终都以"功能目录"的形式合并到 LLM 上下文——本质是告诉模型"以下是你可以调用的东西，请根据需求选择合适的"。当用户安装了大量 Skills 和 MCP 服务后，这个目录可能膨胀到几十甚至上百项，带来以下风险：

| 风险 | 表现 |
|------|------|
| **注意力稀释** | 工具列表过长时，LLM 更容易忽略真正该用的工具，或选择相似但不合适的替代品 |
| **幻觉工具名** | 模型容易"捏造"不存在的工具（把两个工具名拼接、张冠李戴），且随工具数量增加而恶化 |
| **上下文窗口挤占** | 工具定义 + Skill prompt 消耗的 token 挤占对话历史空间，多轮对话质量下降 |
| **选择延迟** | 在大量候选项中"犹豫"，推理时间变长，用户体验变差 |
| **竞合冲突** | 多个 Skill/MCP 工具功能重叠时，LLM 无法可靠地选择最优路径 |

#### 潜在解法（待评估优先级）

| 策略 | 做法 | 代价 |
|------|------|------|
| **按需注入** | 用户消息先过一层轻量分类（coding / office / 通用），只注入相关工具子集 | 增加一次分类调用延迟；分类器本身可能误判 |
| **Skill 懒加载** | 用户没用 `/skill-name` 则不注入该 Skill 的完整 prompt，仅留一行简短摘要 | `/` 触发机制依赖用户主动调用，通用场景下可能漏用 |
| **工具描述精炼** | `description` 控制在一句话以内，`input_schema` 只保留必填参数 | 信息丢失可能导致 LLM 调用时参数不完整 |
| **分层注入** | System prompt 只放高频核心工具（bash, read, write, edit），MCP/Skill 放次要层，Memory/Rules 放参考层 | 次要层工具可能被 LLM 完全忽略 |
| **RAG 选工具** | 用 embedding 把工具描述向量化，根据用户意图检索 top-K 相关工具注入 | 增加 pgvector 依赖和检索开销；检索质量受 embedding 模型影响 |
| **工具数量硬限制** | 每个会话级限制启用工具总数（如 ≤30），超出时提示用户精简 | 限制用户体验灵活性 |
| **LLM 路由层** | 引入专门的 tool-router，由独立的小模型（非主 LLM）负责工具选择，主 LLM 只负责执行 | 增加架构复杂度；路由层自身的准确性需保障 |

#### 当前状态

v0.1 阶段不做选择，先全量注入。待实际使用中收集以下数据后再决策：

- 用户平均启用的工具/Skill/MCP 数量
- 幻觉工具名的发生频率
- 工具目录占上下文窗口的比例
- 用户对工具选择准确率的满意度

**关键观察指标：** 当单条消息的 tool 定义总 token 超过上下文的 30%，或连续 3 次出现幻觉工具名时，本问题优先级提升至 P0。

### 6.2 单 Turn 内工具串行执行

#### 问题描述

当前 per-message loop 中，LLM 流式返回的 `tool_use` 数据块是逐个到达、逐个 `await` 执行的：

```
LLM 返回: tool_use("read_file", "a.ts") → await 执行 → 结果注入
          tool_use("read_file", "b.ts") → await 执行 → 结果注入
          tool_use("read_file", "c.ts") → await 执行 → 结果注入
```

三个读文件操作彼此完全独立，但总耗时 = 三次 I/O 之和。当 LLM 在一个 turn 内返回多个无依赖关系的工具调用时，串行执行造成了不必要的延迟。

#### 影响范围

| 场景 | 典型工具组合 | 串行耗时 | 并发后耗时 |
|------|-------------|---------|-----------|
| 读多个文件 | `read_file` × N | N × 单次耗时 | max(单次耗时) |
| 独立搜索 | `grep` + `glob` | 两者之和 | max(两者) |
| 多 MCP 查询 | `mcp_weather` + `mcp_news` | 两者之和 | max(两者) |
| 编辑 + 校验 | `edit_file` → `bash(test)` | 必须串行（有依赖） | 不变 |
| **读写同一文件** | `read_file("x.ts")` + `edit_file("x.ts")` | 不确定（依赖 LLM 意图） | **并发有竞态** |

#### 核心难点

| 难点 | 说明 |
|------|------|
| **依赖检测** | LLM 不显式声明工具间的依赖关系，需要自动判断哪些调用可以并发。简单规则（同类型可并发 / 不同文件可并发）覆盖不全，真正可靠的方案需要 LLM 显式标注或语义分析 |
| **读写同一文件竞态** | 典型场景：LLM 在同一 turn 发出 `read_file("config.ts")` + `edit_file("config.ts")`。如果并发，read 拿到编辑前还是编辑后的内容完全随机，LLM 后续推理基于不确定状态。即使按"先写后读"串行，也需要保证顺序与 LLM 输出顺序一致 |
| **Build 模式冲突** | Build 模式每步暂停等用户确认，并发执行会让确认 UI 混乱——多个工具同时弹出确认卡片，用户难以决策 |
| **Client 工具并发** | 客户端执行 `bash` + `write_file` 时，如果并发可能产生文件系统竞争（bash 依赖 write_file 刚写完的文件） |
| **上下文注入顺序** | 并发执行完毕后，工具结果以什么顺序注入上下文？顺序不当可能让 LLM 产生错误推理（LLM 的输出顺序暗示了它的依赖假设） |
| **错误隔离** | 并发中某个工具失败，是取消其他工具还是继续？部分失败的结果如何呈现给 LLM？ |

#### 潜在解法（待评估优先级）

| 策略 | 做法 | 代价 |
|------|------|------|
| **LLM 显式标注依赖** | 扩展 tool_use 协议，要求 LLM 在输出中标注 `depends_on` 字段，无依赖的调用自动并发 | 依赖 LLM 遵守协议，实际中可能不稳定 |
| **流式缓冲再分析** | 先缓冲整个 turn 的所有 tool_use，分析后对无依赖组并发执行，有依赖的按序执行 | 增加一次缓冲延迟；依赖分析规则难以完备 |
| **同类型合并** | 仅对同一工具类型、不同参数的调用并发（如多个 `read_file`），跨类型保持串行 | 覆盖场景有限 |
| **Build 模式例外** | 只在 Ask/Plan 的自动执行阶段开启并发，Build 模式保持串行（因为需要用户逐步确认） | Build 模式无法享受加速 |
| **仅服务端工具并发** | Client 工具串行（涉及文件系统可能有竞争），服务端 MCP 工具可并发 | 区分执行位置的复杂度 |

#### 当前状态

v0.1 保持串行执行。待观察以下指标后评估优先级：

- 单 turn 内平均工具调用数量
- 串行执行导致的总延迟在用户体验中的感知程度
- 多工具调用中彼此独立的占比

**触发升级条件：** 单 turn 平均 tool_use ≥ 3 且用户感知延迟（工具执行总耗时）超过 10 秒时，本问题优先级提升至 P1。

### 6.3 Skill 维护管理：版本升级、测试与质量保障

#### 问题描述

当前 Skill 体系以文件夹 + SKILL.md 为核心，安装/卸载流程已定义（见 3.8 节），但缺少以下关键维护能力：

| 缺口 | 表现 |
|------|------|
| **版本升级** | Hub 中 Skill 发布新版本后，已安装该 Skill 的用户无感知——没有升级通知、没有 changelog 展示、没有一键升级机制。用户的 SKILL.md 停留在安装时的版本，与 Hub 最新版脱节 |
| **Skill 测试** | 没有 Skill 的自动化测试框架。SKILL.md 作为 prompt 注入 LLM 后，其行为是否正确、是否引入幻觉、是否与 description 描述一致，全靠人工验证 |
| **质量审查** | 用户自定义 Skill 无审核流程。恶意或低质量的 SKILL.md（如含 prompt injection 攻击载荷）可被客户端直接加载执行，存在安全风险 |
| **依赖声明** | 部分 Skill 隐式依赖特定 MCP 工具或系统能力（如 `bash`、`read_file`），但缺少显式依赖声明。用户安装后才发现缺少前置依赖，体验受损 |
| **废弃与迁移** | Skill 停止维护或改名后，已安装用户无提示；无平滑迁移路径（如从旧版 Skill 迁移到新版替代 Skill），用户可能长期使用过期指令 |
| **冲突检测** | 多个 Skill 功能重叠时（如两个"代码审查"Skill），LLM 无法区分优劣，可能随机选择；Skill 之间互相矛盾的指令也无人提醒 |

#### 潜在解法（待评估优先级）

| 策略 | 做法 | 代价 |
|------|------|------|
| **版本比对 + 升级提醒** | Skill 安装时记录 `version` 字段；Hub 接口返回最新版本号；客户端定期比对并提示"可升级"徽标，升级后覆盖本地 SKILL.md | 需要客户端轮询或 WebSocket 推送；版本号依赖 Hub 维护者手工递增 |
| **Skill 测试框架** | 提供标准测试夹具：给定输入 + 期望行为描述，用评估 LLM 判断 Skill 加载后的行为是否符合预期。测试用例可随 Skill 一起发布（`tests/` 目录），服务端在 CI 中运行 | 评估 LLM 的判定本身不 100% 可靠；增加 Skill 作者的负担 |
| **SKILL.md 沙箱验证** | 自定义 Skill 创建时，服务端运行基本安全检查：长度限制、禁止注入 `system:` / `<system>` 等可覆盖系统 prompt 的模式、敏感关键词过滤 | 检查规则的完备性难以保证；可能误拦合法 Skill |
| **显式依赖声明** | 在 `skill-hub.json` 条目中添加 `requires` 字段，声明 Skill 依赖的工具（如 `["bash", "read_file"]`）和 MCP 服务（如 `["mh6"]`）。安装时校验依赖是否可用，不可用时警示用户 | 需要维护完整的系统能力清单；依赖版本管理增加复杂度 |
| **废弃标记 + 替代提示** | 在 Hub 条目中增加 `status: "deprecated"` 和 `replaced_by` 字段。客户端对已安装但已废弃的 Skill 展示警告，引导用户迁移到替代 Skill | 需要客户端 UI 配合；Hub 维护者需主动标记 |
| **Skill 评分/评价体系** | Hub 中引入社区评分和评价，帮助用户识别高质量 Skill，同时为 LLM 工具选择提供参考信号 | 需要用户系统和后端存储；存在刷分风险 |

#### 当前状态

v0.1 阶段 Skill 数量有限（预计 < 20），维护压力尚可承受，暂不建立正式体系。但以下底线措施从 Day 1 开始：

- **SKILL.md 最小验证**：自定义 Skill 创建时校验内容非空、长度 ≤ 50KB、不含 `system:` / `<system>` 等可能覆盖系统 prompt 的高危模式
- **版本字段预留**：`SkillDefinition.version` 字段已在数据结构中定义（默认 `"1.0.0"`），后续接入升级逻辑时无需 schema 迁移
- **Hub 审计日志**：服务端记录所有 install/uninstall 操作，便于追踪 Skill 使用情况和定位问题

**触发升级条件：** Hub Skill 数量 ≥ 20 或单个 Skill 安装量 ≥ 100 时，版本升级提醒和测试框架优先级提升至 P1。

### 6.4 会话终止 / 系统异常时的内部状态收尾

#### 问题描述

agent 工作过程中存在两类非正常结束路径：**客户端主动终止**（`POST /sessions/{id}/cancel`，见 1.9.2）和**系统异常**（1.8 四级策略的第 4 级终止、服务器崩溃等）。两条路径都会让正处于 `PROCESSING` / `WAITING_SYNC` 的 turn 中途中断。此时系统内部有大量"进行中"状态，文档对它们的收尾规则基本空白：

| 状态类别 | 具体状态 | 现状缺口 |
|------|------|------|
| 引擎协程 | per-message loop 正在 await 的 LLM 流 / `sync_waiter.wait()` / 工具执行 | cancel 只定义了 Plan 拒绝 / Build 终止的语义（1.9.2），未定义底层协程如何取消（`asyncio.TaskGroup` / `CancelledError`）与资源释放 |
| 消息与队列 | `processing` 中的当前消息、`pending` 待处理消息 | 队列移除（1.7）仅支持 `pending` → `cancelled`，`processing` 中不可移除；终止后当前消息落什么状态（`cancelled` / `error`）未定义 |
| 会话 | `sessions.status` 仅 `active`/`archived`，无 `terminated`/`interrupted` | 终止后会话是否仍保持 `active`；`current_message_id` 是否清空；何时归档 |
| plan 内存态 | `plan_confirmed`、`plan_text_buffer`、`build_step` | 终止时内存态无收尾定义，且无持久化 plan 状态 |
| 上下文 | 压缩 / 卸载的中间产物（raw→compressed→meta-summary、`offloaded_blocks` 索引） | 每轮内存计算，终止时是否留下脏数据 / 半写入索引未定义 |
| 工具副作用 | 已执行的 `edit_file` 等文件改动、已 spawn 的子 Agent task（第 9 章） | 终止是否回滚 / 级联取消未定义；MCP 进行中调用如何释放 |
| 流式推送 | NDJSON buffer（断线缓冲回放，1.8.5） | 终止时 buffer 冲刷还是丢弃；前端如何收到最终状态 |
| 崩溃恢复 | 重启后 `processing` → `pending` 重置（1.8.6） | 若走重置路径，用户主动取消的消息可能被当异常重跑 |

#### 潜在解法（待评估优先级）

| 策略 | 做法 | 代价 |
|------|------|------|
| **统一取消协议** | 给 per-message loop 定义统一 cancel 入口：用 `asyncio.TaskGroup` 把 LLM 流、`sync_waiter`、工具执行包进可取消作用域，收到 cancel 后抛 `CancelledError` 走收尾 | 需重构 1.3 主循环协程组织；取消点需在各 await 处显式设计 |
| **终止收尾清单** | 定义固定收尾流程：消息落 `cancelled`/`error` → 清 `current_message_id` → 内存态重置 → buffer 丢弃并推最终 chunk → 子 task 级联取消 | 收尾需覆盖所有进行中资源，遗漏即泄漏 |
| **显式会话终止态** | `sessions.status` 增加 `terminated`/`interrupted`（1.8.2 的 `TERMINAL` 目前未纳入正式状态表），或定义终止即归档 | 需 DB schema 变更与迁移；需与 1.2 状态机图同步 |
| **工具副作用记录与提示** | 终止前记录已执行工具列表（尤其文件改动、子 Agent），在最终 chunk 告知"已执行 X，未回滚" | 需维护副作用清单；回滚本身可能引入半回滚新风险 |
| **子 Agent 级联终止** | 父会话终止 → 子 task 会话级联取消（复用 9.7 父子归档级联）；进行中 MCP 调用走 2.7.2 第 3 级释放 | 级联时序需防竞态（子会话可能恰好完成） |
| **终止与恢复路径区分** | 明确"用户主动终止"与"系统异常重启恢复（1.8.6）"是两条不同路径，避免用户主动取消的消息被恢复逻辑重跑 | 需为 cancel 落一个区别于 `processing` 的状态（如 `cancelled`），恢复查询排除之 |

#### 当前状态

v0.1 已覆盖：cancel 接口的 Plan/Build 语义（1.9.2）、1.8 四级策略（第 4 级终止 → `message.error(fatal=true)`，引擎回 IDLE/归档）、1.8.5 断线缓冲回放、1.8.6 重启恢复（`processing→pending` 重置）、队列移除仅限 `pending`（1.7）。但 **`PROCESSING` 中途终止的底层实现、各内部状态收尾、工具副作用与子 Agent 级联**均未定义。

**触发升级条件：** 客户端主动终止 / 系统异常后，出现以下任一可复现现象——已取消的消息被重跑、进行中的任务残留、终止后再次发送消息状态异常——本问题优先级提升至 P1；上线前至少完成「统一取消协议」+「终止收尾清单」两项。

---

<a id="7-可观测性"></a>

## 7. 可观测性

Agent 系统的可观测性设计需要兼顾两个视角：**用户视角**（过程透明、可信度、成本感知）和**运维审计视角**（排障溯源、性能监控、合规审计）。

技术选型：**OpenTelemetry** 作为统一可观测性框架，覆盖 Tracing / Metrics / Logging 三大信号，Grafana 体系（Tempo + Prometheus + Loki + Grafana）作为后端存储与可视化。

核心设计原则：
- **三条管道，各走各路**：Tracing / Metrics 走 OTel API 直调（`with tracer.start_as_current_span(...)` / `counter.add(...)`），Logging 走 structlog 直调（`logger.info(...)`），Stream / Audit 走 EventBus。不在 Tracing、Metrics、Logging 上叠床架屋
- **trace_id 贯穿全链路**：从入队 → LLM 调用 → 工具执行 → 响应推送，一条 trace 串联
- **渐进接入**：先建 EventBus（仅 Stream + Audit）+ OTel 配置 + structlog 配置，再逐个接入引擎

```
                    ┌── OTel Tracer ────────→ Tempo
                    │   (with tracer.start_as_current_span(...))
QueryLoopEngine ────┼── OTel Meter ─────────→ Prometheus → Grafana
  (直接调用         │   (counter.add / histogram.record)
   OTel + structlog │
   + EventBus)      ├── structlog ──────────→ Loki
                    │   (logger.info / logger.error)
                    │
                    └── EventBus ──┬── Audit Logger ──→ PostgreSQL
                       (仅 Stream  │
                        + Audit)   └── NDJSON Stream ──→ Client UI
```

### 7.1 架构概览

- [7.1.1 三种可观测性管道](#711-三种可观测性管道)
- [7.1.2 AgentEvent 类型定义](#712-agentevent-类型定义仅-stream--audit-使用)
- [7.1.3 EventBus 接口](#713-eventbus-接口)
- [7.1.4 引擎侧用法](#714-引擎侧用法)
- [7.1.5 系统启动时的订阅注册](#715-系统启动时的订阅注册)
- [7.1.6 两层通道](#716-两层通道)
- [7.1.7 存储方案](#717-存储方案)

#### 7.1.1 三种可观测性管道

可观测性系统的五个信号走两种路径，不强求统一抽象：

| 信号 | 方式 | 引擎代码 | 理由 |
|------|------|---------|------|
| **Tracing** | OTel API 直调 | `with tracer.start_as_current_span("llm_call", ...)` | Span 生命周期由 context manager 管理，比手动匹配 start/end 事件更简洁且不易出错 |
| **Metrics** | OTel Meter 直调 | `llm_token_counter.add(n, attrs)` | 一行调用，无事可抽象 |
| **Logging** | structlog 直调 | `logger.info("tool_executed", ...)` | 日志本身就是接口层，需要散落在代码各处，走 EventBus 反而丢失灵活性 |
| **NDJSON Stream** | EventBus | `event_bus.emit(ToolExecuted(...))` | 需要结构化的生命周期事件推送给前端 UI，天然适合事件订阅模式 |
| **Audit Logger** | EventBus | `event_bus.emit(ToolExecuted(...))` | 审计动作稀疏且需要集中化的 action 映射，不适合散落在引擎各处 |

**EventBus 只负责两个消费者**：NDJSON Stream（推送前端 UI 实时事件流）和 Audit Logger（写入 PostgreSQL 审计记录）。这两个消费者的共同特点是——它们需要的是**结构化的生命周期事件**，而不是零散的日志/指标点。

```
engine/query_loop.py                           observability/
     │                                               │
     │  # Tracing: OTel context manager              │
     │  with tracer.start_as_current_span(...)  ────►│ OTLP → Tempo
     │                                               │
     │  # Metrics: OTel meter direct                 │
     │  llm_token_counter.add(n, {...})        ────►│ OTLP → Prometheus
     │                                               │
     │  # Logging: structlog direct                  │
     │  logger.info("llm_call_completed", ...) ────►│ → Loki
     │                                               │
     │  # Stream + Audit: EventBus (仅此两个)        │
     │  event_bus.emit(ToolExecuted(...))      ────►│ StreamPusher  → NDJSON → Client UI
     │                                               │ AuditLogger   → PG audit_logs
```

#### 7.1.2 AgentEvent 类型定义（仅 Stream + Audit 使用）

以下事件类型只服务于 NDJSON Stream（推送前端）和 Audit Log（审计落库）。Tracing / Metrics / Logging 不走 EventBus，各自用原生 API。

```python
# server/observability/events.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID
from enum import StrEnum


class AgentEventType(StrEnum):
    # === 消息生命周期 ===
    QUEUE_ENQUEUED = "queue.enqueued"
    MESSAGE_START = "message.start"
    MESSAGE_COMPLETE = "message.complete"
    MESSAGE_ERROR = "message.error"

    # === LLM 调用 ===
    LLM_CALL_STARTED = "llm.call_started"
    LLM_FIRST_TOKEN = "llm.first_token"
    LLM_CALL_COMPLETED = "llm.call_completed"
    LLM_CALL_FAILED = "llm.call_failed"
    TOKEN_USAGE = "token.usage"

    # === 工具执行 ===
    TOOL_DISPATCHED = "tool.dispatched"
    TOOL_EXECUTED = "tool.executed"
    TOOL_FAILED = "tool.failed"
    TOOL_PERMISSION_DENIED = "tool.permission_denied"

    # === Plan 交互 ===
    PLAN_GENERATED = "plan.generated"
    PLAN_QUESTION = "plan.question"
    PLAN_INTERACTION = "plan.interaction"

    # === 上下文 ===
    CONTEXT_BUILT = "context.built"

    # === 系统 ===
    SYSTEM_STATUS = "system.status"

@dataclass(slots=True)
class AgentEvent:
    type: AgentEventType
    timestamp: datetime
    session_id: UUID
    user_id: UUID
    message_id: UUID
    data: dict[str, Any] = field(default_factory=dict)
    # 对于 chunk 同名事件，data 结构与第一章 StreamChunk 定义完全一致
    # StreamSubscriber 直接将 data 序列化为 NDJSON 行，零翻译
```

#### 7.1.2.1 与 NDJSON Chunk 的关系

AgentEvent 按消费方分为两类，和枚举中的分组正交：

**chunk 同名事件**：类型值与第一章 `StreamChunk.type` 一致。`data` 结构直接沿用 `StreamChunk` 定义，`StreamSubscriber` 收到后**零翻译**——直接将 `data` 序列化为 NDJSON 行推送前端：

```
queue.enqueued  message.start  message.complete  message.error
token.usage     plan.generated  plan.question     system.status
```

**内部事件**：仅 Audit 和 Hooks 消费，`StreamSubscriber` 忽略：

```
llm.call_started   llm.first_token   llm.call_completed  llm.call_failed
tool.dispatched    tool.executed     tool.failed         tool.permission_denied
plan.interaction   context.built
```

```
Engine                          StreamSubscriber                 Client
  │                                    │                           │
  │ emit(AgentEvent{                  │                           │
  │   type=MESSAGE_COMPLETE,          │                           │
  │   data={type:"message.complete",  │  send({type:"message.complete", ...})
  │     seq, message_id, summary})    ├──────────────────────────►│
  ├──────────────────────────────────►│  (data 直接就是 chunk，   │
  │                                    │   不需要翻译)              │
  │                                    │                           │
  │ emit(AgentEvent{                  │                           │
  │   type=LLM_CALL_COMPLETED,        │  (跳过，不推前端)          │
  │   data={turn, tokens_in, ...})    │                           │
  ├──────────────────────────────────►│                           │
```

引擎中 chunk 同名事件和内部事件并列 emit：

```python
# 推前端的 chunk 同名事件 —— data 结构就是 NDJSON chunk
await event_bus.emit(AgentEvent(
    type=AgentEventType.MESSAGE_COMPLETE,
    data={"type": "message.complete", "seq": next_seq(), "message_id": str(msg.id),
          "turn": turn, "tokens": {...}, "status": "ok"},
))

# 仅 Audit 的内部事件
await event_bus.emit(AgentEvent(
    type=AgentEventType.LLM_CALL_COMPLETED,
    data={"turn": turn, "model": msg.model, "tokens_in": tokens_in,
          "tokens_out": tokens_out, "duration_ms": elapsed_ms},
))
```

#### 7.1.2.2 与 Hooks 的关系

用户 Hook 不走 EventBus。EventBus 是单向广播（fire-and-forget，无返回值），而 Hook 需要 **engine 等待结果**，前一个 hook 的修改对后一个可见，任一 hook 可以阻止后续动作。因此 Hook 采用责任链模式，独立于 EventBus。

详见 [第 8 章 Hooks 系统](#8-hooks-系统)。

#### 7.1.3 EventBus 接口

```python
# server/observability/event_bus.py
from collections.abc import Callable, Awaitable

EventHandler = Callable[[AgentEvent], Awaitable[None]]


class EventBus:
    """轻量级进程内事件总线。仅用于 Stream 推送（前端实时事件流）和 Audit 记录（审计落库）。
    Tracing / Metrics / Logging 不走 EventBus，各自用 OTel / structlog 原生 API。"""

    def __init__(self):
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """注册事件处理器（Stream / Audit / Hooks）"""
        self._handlers.append(handler)

    async def emit(self, event: AgentEvent) -> None:
        """发射事件给所有订阅者。各 handler 独立执行，单个异常不影响其他。"""
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                # 某消费者的异常不应影响引擎或其他消费者
                import logging
                logging.getLogger("observability").exception(
                    "EventHandler %s failed for event %s", handler.__name__, event.type)
```

#### 7.1.4 引擎侧用法

QueryLoopEngine 在关键节点**同时**调用 OTel（tracing + metrics）、structlog（logging）、EventBus（stream + audit）。三种管道各司其职，不强求统一：

```python
# server/engine/query_loop.py
from opentelemetry import trace
from observability.metrics import (
    llm_call_duration, llm_token_usage, llm_cost, tool_call_total, message_total
)
from observability.logging import logger
from observability.events import AgentEvent, AgentEventType

tracer = trace.get_tracer("iwork.engine")


class QueryLoopEngine:
    def __init__(self, session_id, user_id, event_bus: EventBus):
        self.event_bus = event_bus  # 仅用于 Stream + Audit
        ...

    async def _run_message_loop(self, msg: Message):
        # ── Logging: structlog 直调 ──
        logger.info("message_started", session_id=..., message_id=msg.id, mode=msg.mode)

        # ── Stream + Audit: EventBus emit ──
        await self.event_bus.emit(AgentEvent(
            type=AgentEventType.MESSAGE_START, ...
        ))

        for turn in range(max_turns):
            # ── Tracing: OTel context manager ──
            with tracer.start_as_current_span(
                "llm_call",
                attributes={
                    "llm.provider": settings.llm_provider,
                    "llm.model": msg.model,
                    "agent.turn": turn,
                },
            ) as span:
                response = await self._call_llm(...)

                # Span events + attributes
                span.add_event("first_token", {"ttft_ms": ttft_ms})
                span.add_event("tokens", {
                    "input": tokens_in, "output": tokens_out,
                    "cache_read": cache_read, "cache_write": cache_write,
                })
                span.set_attribute("llm.stop_reason", stop_reason)

            # ── Metrics: OTel meter 直调 ──
            llm_call_duration.record(elapsed_ms / 1000, attributes={
                "model": msg.model, "provider": settings.llm_provider, "status": "ok",
            })
            llm_token_usage.add(tokens_in, attributes={"model": msg.model, "token_type": "input"})
            llm_token_usage.add(tokens_out, attributes={"model": msg.model, "token_type": "output"})

            # ── Logging: structlog 直调 ──
            logger.info("llm_call_completed", turn=turn, model=msg.model,
                        tokens_in=tokens_in, tokens_out=tokens_out, duration_ms=elapsed_ms)

            # ── Stream + Audit: EventBus (仅这两个消费者) ──
            await self.event_bus.emit(AgentEvent(
                type=AgentEventType.LLM_CALL_COMPLETED,
                ...,
                data={"turn": turn, "tokens_in": tokens_in, ...},
            ))

            # ── 工具执行 ──
            with tracer.start_as_current_span("tool_execute", attributes={...}):
                result = await self._execute_tool(...)

            tool_call_total.add(1, attributes={"tool_name": name, "status": "success"})
            logger.info("tool_executed", tool_name=name, duration_ms=dur, success=True)

            if self._needs_audit(name):  # 文件读/写、shell 执行等敏感操作
                await self.event_bus.emit(AgentEvent(
                    type=AgentEventType.TOOL_EXECUTED,
                    data={
                        "tool_name": name,
                        "location": location,
                        "duration_ms": dur,
                        "success": ok,
                        "input": tool_call.args,           # 工具入参（文件路径、命令等）
                        "output": str(result)[:500],       # 工具出参，截断防止撑爆
                        "exit_code": getattr(result, "exit_code", None),
                    },
                ))
```

#### 7.1.5 系统启动时的订阅注册

```python
# server/main.py
from observability.event_bus import EventBus
from observability.stream import StreamSubscriber
from observability.audit import AuditSubscriber
from observability.tracing import init_tracing
from observability.metrics import init_meter

# OTel Tracing + Metrics 初始化（全局 TracerProvider / MeterProvider）
init_tracing(service_name="iwork-agent", endpoint=settings.otel_exporter_endpoint)
init_meter()

event_bus = EventBus()

# 仅 Stream + Audit 走 EventBus
event_bus.subscribe(StreamSubscriber().handle)
event_bus.subscribe(AuditSubscriber(db_session_factory).handle)
# session 创建时可为该 session 注册专属 stream 消费者
```

#### 7.1.6 两层通道

| 层 | 通道 | 存储 | 消费方 |
|----|------|------|--------|
| **Layer 1: 用户实时** | NDJSON 流（现有） | `stream_events` 表（新增） | 前端 UI |
| **Layer 2: 运维分析** | OTLP gRPC | Tempo + Prometheus + Loki | Grafana / 管理员 |

**与现有基础设施的关系：**

| 现有设施 | 变化 |
|---------|------|
| `log.warning(...)` | 全部替换为 `logger.warning(...)` structlog 结构化调用 |
| `system.status` 推送 | **保留**，仍负责用户实时通知 |
| `StreamBuffer` | **保留**，用作重连回放；新增 `stream_events` 表持久化 seq 数据块 |
| 异常处理四级策略 | **保留**，第 4 级致命错误增加写入 `audit_logs`（action=`system.error_fatal`） |
| `token.usage` 推送 | **保留**，同时通过 OTel Metrics 记录，用于成本聚合 |

#### 7.1.7 存储方案

**理想态：Grafana 体系**

Tracing / Metrics / Logging 三大信号通过 OTLP 协议统一导出到 Grafana 可观测性套件：

| 信号 | 存储后端 | 说明 |
|------|---------|------|
| **Tracing** | **Tempo** | OTel Span 经 OTLP exporter 写入 Tempo，支持按 `trace_id` 全链路检索 |
| **Metrics** | **Prometheus** | Meter instrument 暴露 `/metrics` 端点，Prometheus 定时抓取，Grafana 可视化 |
| **Logging** | **Loki** | structlog 输出 JSON 行，Loki 采集并索引，与 Tempo trace 通过 `trace_id` 关联 |
| **NDJSON Stream** | **PostgreSQL** `stream_events` 表 | 前端实时推送 + 持久化 seq 数据块，支持重连回放和会话回放 |
| **Audit** | **PostgreSQL** `audit_logs` 表 | 不走 OTel，独立于 Grafana 体系，保证长期保留和不可篡改 |

```
                    ┌── OTel Tracer ── OTLP ──→ Tempo ──────┐
QueryLoopEngine ────┼── OTel Meter ── /metrics → Prometheus ─┼──→ Grafana
    (引擎直调)      │                                        │
                    ├── structlog ── JSON ──→ Loki ──────────┘
                    │
                    └── EventBus ────→ PostgreSQL (audit_logs + stream_events)
```

**起步方案：零外部依赖**

单进程 agent server 不需要一上来就部署全套 Grafana 套件。以下方案可以在无外部服务的情况下获得足够的生产级可观测性：

| 信号 | 起步方案 | 说明 |
|------|---------|------|
| **Tracing** | structlog 注入 `trace_id`，写入日志行 | 同一条消息的所有日志通过 `trace_id` 关联，`grep` 即可还原调用链。不需要单独部署 Tempo |
| **Metrics** | `prometheus-client` 暴露 `/metrics` 端点 | 本地 `curl localhost:9090/metrics` 直接查看；生产环境再对接 Prometheus |
| **Logging** | structlog JSON → stdout/stderr | `docker logs` / `kubectl logs` 直接消费，日志采集器（如 Grafana Alloy、Fluent Bit）可转发到 Loki |
| **NDJSON Stream** | PostgreSQL `stream_events` 表 | 起步就是 PG，前端实时推送通过现有 NDJSON 流直出，持久化落 `stream_events` 表用于回放 |
| **Audit** | PostgreSQL `audit_logs` 表 | 起步就是 PG，没有更轻量的替代 |

**文件兜底：本地持久化**

Tracing / Metrics / Logging 默认流式输出，进程重启即丢失累积数据。在无外部服务的情况下，可通过本地文件获得最低限度的持久化：

```python
# server/observability/logging.py — 双输出：stdout + 按天轮转文件
import logging
from logging.handlers import TimedRotatingFileHandler

file_handler = TimedRotatingFileHandler(
    "logs/agent.log", when="midnight", backupCount=30, encoding="utf-8",
)
file_handler.setFormatter(structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(),
))

structlog.configure(
    processors=[...],
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logging.getLogger("iwork").addHandler(file_handler)
```

```python
# server/observability/metrics.py — 定期写 metrics snapshot
import json, asyncio
from prometheus_client import generate_latest, CollectorRegistry

async def periodic_metrics_dump(path="logs/metrics_snapshot.json", interval=60):
    while True:
        metrics_text = generate_latest().decode()
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics_text,
        }
        with open(path, "w") as f:
            json.dump(snapshot, f, default=str)
        await asyncio.sleep(interval)

# main.py startup 时 asyncio.create_task(periodic_metrics_dump())
```

```python
# Trace 不单独写文件 — trace_id 已随日志落盘，通过 grep trace_id 即可还原调用链
```

| 信号 | 文件持久化方式 | 保留策略 |
|------|--------------|---------|
| **Tracing** | 不独立写文件，`trace_id` 已随 structlog 日志行落盘 | 随日志文件轮转，默认 30 天 |
| **Metrics** | 每 60 秒写一份 JSON snapshot 到 `logs/metrics_snapshot.json` | 单文件覆盖，保留最新快照 |
| **Logging** | `TimedRotatingFileHandler` 按天轮转，JSON 格式 | `logs/agent.log`，保留 30 天 |

升级条件：

- **上 Tempo**：拆分为多服务，需要跨进程串联调用链时
- **上 Prometheus**：有 Grafana 看板需求，或需要告警规则时
- **上 Loki**：日志量超过 `grep` 能处理的规模，需要全文检索时
- Stream 和 Audit 始终走 PostgreSQL，无需升级

#### 7.1.8 事件发布与订阅

**发布**：引擎在生命周期节点直接 `emit`，调用方不关心谁在消费：

```
Engine 生命周期                               emit 的事件
──────────────────────────────────────────────────────────────────
enqueue(msg)                               → QUEUE_ENQUEUED
_run_message_loop(msg) 开始                 → MESSAGE_START
  ├─ _build_context()                      → CONTEXT_BUILT
  ├─ _call_llm() 开始                      → LLM_CALL_STARTED
  │   ├─ 首个 token 到达                   → LLM_FIRST_TOKEN
  │   ├─ 调用完成                          → LLM_CALL_COMPLETED
  │   │   └─ 同时 emit                     → TOKEN_USAGE
  │   └─ 调用失败                          → LLM_CALL_FAILED
  ├─ _execute_tool()                       → TOOL_DISPATCHED
  │   ├─ 执行成功                          → TOOL_EXECUTED
  │   ├─ 执行失败                          → TOOL_FAILED
  │   └─ 权限拒绝                          → TOOL_PERMISSION_DENIED
  ├─ Plan 生成 / 追问                      → PLAN_GENERATED / PLAN_QUESTION
  └─ 用户确认 / 拒绝 / 编辑计划            → PLAN_INTERACTION
消息处理完成                               → MESSAGE_COMPLETE
消息异常终止                               → MESSAGE_ERROR
```

以 `enqueue` 为例，一次 emit 的完整链路：

```python
# server/engine/query_loop.py
class QueryLoopEngine:
    async def enqueue(self, user_message: str, mode: str, workspace: str) -> Message:
        msg = Message(id=uuid4(), content=user_message, mode=mode)
        next_pos = await self._queue.push(msg)

        # 一行 emit，不关心谁来消费
        await self.event_bus.emit(AgentEvent(
            type=AgentEventType.QUEUE_ENQUEUED,
            timestamp=datetime.now(timezone.utc),
            session_id=self.session_id, user_id=self.user_id,
            message_id=msg.id,
            data={"type": "queue.enqueued", "seq": next_seq(),
                  "position": next_pos, "queue_length": self._queue.size,
                  "mode": mode, "workspace": workspace},
        ))
        return msg
```

这一行 `emit` 出去后，EventBus 广播给所有 subscriber（Hook 不走 EventBus，但同时也在 `enqueue` 节点执行责任链）：

| 消费者 | 收到后 | 说明 |
|-----------|--------|------|
| **StreamSubscriber** | `QUEUE_ENQUEUED` 在 `CHUNK_TYPES` 中 → `send_ndjson(data)` 推前端 | EventBus，chunk 同名零翻译 |
| **AuditSubscriber** | `QUEUE_ENQUEUED` 不在 `AUDIT_TYPES` 中 → 跳过 | EventBus，入队不需要审计 |
| **Hook Chain** | `message.before` 拦截点触发 → 遍历已注册 hook，串行执行 | 责任链，不走 EventBus，见第 8 章 |

**订阅**：启动时注册，各 subscriber 自行过滤。EventBus 只负责广播，不路由：

```python
# StreamSubscriber —— 只转发 chunk 同名事件
CHUNK_TYPES = {
    AgentEventType.QUEUE_ENQUEUED, AgentEventType.MESSAGE_START,
    AgentEventType.MESSAGE_COMPLETE, AgentEventType.MESSAGE_ERROR,
    AgentEventType.TOKEN_USAGE,
    AgentEventType.PLAN_GENERATED, AgentEventType.PLAN_QUESTION,
    AgentEventType.SYSTEM_STATUS,
}

class StreamSubscriber:
    async def handle(self, event: AgentEvent) -> None:
        if event.type not in CHUNK_TYPES:
            return                          # 内部事件，跳过
        await self._send_ndjson(event.data)  # data 就是 chunk，零翻译


# AuditSubscriber —— 只记录需要审计的事件
AUDIT_TYPES = {
    AgentEventType.TOOL_EXECUTED,
    AgentEventType.TOOL_PERMISSION_DENIED,
    AgentEventType.MESSAGE_ERROR,
    AgentEventType.PLAN_INTERACTION,
}

class AuditSubscriber:
    async def handle(self, event: AgentEvent) -> None:
        if event.type not in AUDIT_TYPES:
            return
        await self._insert_audit_log(event)

```

生命周期事件的三个消费者：

| 消费者 | 机制 | 事件范围 | 输出 |
|-----------|------|------|------|
| **StreamSubscriber** | EventBus | chunk 同名事件（8 个） | NDJSON 原样推送前端 |
| **AuditSubscriber** | EventBus | 敏感操作事件（4 个） | `audit_logs` 表 |
| **Hook Chain** | 责任链 | 用户配置的拦截点（message/llm/tool 的 before/after） | 用户脚本，可修改/阻止行为 |

> Stream 和 Audit 是 EventBus 的两个内置 subscriber；Hook 不走 EventBus，采用责任链独立运行，详见 [第 8 章 Hooks 系统](#8-hooks-系统)。

### 7.2 链路追踪 (Tracing)

#### 7.2.1 Trace 结构

Trace 结构直接映射 Query Loop 层级，根节点为单条消息：

```
Trace: message/{message_id}  (root: session_id, user_id, mode, model)
│
├── Span: enqueue
│   └── attrs: queue_position, workspace, scene_mode, file_count
│
├── Span: message_loop [turn=0]
│   ├── Span: build_context
│   │   └── attrs: estimated_tokens, message_count, tool_def_count
│   │
│   ├── Span: llm_call
│   │   └── attrs: provider, model, thinking_budget, stream=true
│   │   ├── Event: first_token_at_ms        (TTFT)
│   │   ├── Event: chunk_count=N
│   │   ├── Event: stop_reason
│   │   └── Event: tokens {input, output, cache_read, cache_write}
│   │
│   ├── Span: tool_dispatch
│   │   └── attrs: tool_name, location=client|server
│   │   ├── Span: tool_execute
│   │   │   └── attrs: duration_ms, success, error_type
│   │   └── Event: tool_result (截断 ≤ 500 字符)
│   │
│   └── Span: plan_interaction  (仅 Plan 模式)
│       └── attrs: type=question|confirm|edit
│
├── Span: message_loop [turn=1]
│   └── ...  (同上结构)
│
└── Span: message_complete
    └── attrs: total_turns, total_tokens_in, total_tokens_out,
              total_tool_calls, total_cost_dollars, status
```

#### 7.2.2 Span 属性定义

每个 Span 自动注入以下通用属性：

| 属性 | 来源 | 说明 |
|------|------|------|
| `session.id` | HTTP header | 会话 ID |
| `user.id` | JWT token | 操作者 |
| `message.id` | Message 入队时生成 | 单条消息 |
| `trace.id` | OTel 自动生成 | 全链路 |
| `mode` | session.mode | ask / plan / build |
| `model` | message.model | LLM 模型名 |

各 Span 类型特有属性：

| Span | 属性 | 类型 |
|------|------|------|
| **enqueue** | `queue.position`, `agent.workspace`, `session.scene_mode`, `message.file_count`, `message.skill_ids` | Int/String |
| **build_context** | `context.estimated_tokens`, `context.message_count`, `context.tool_def_count` | Int |
| **llm_call** | `llm.provider`, `llm.model`, `llm.thinking_budget`, `llm.stream`, `agent.turn` | String/Int/Bool |
| **tool_dispatch** | `tool.name`, `tool.location` | String |
| **tool_execute** | `tool.success`, `error.type` (if failed), `tool.duration_ms` | Bool/String/Int |
| **plan_interaction** | `plan.interaction_type`, `plan.user_response` (truncated) | String |
| **message_complete** | `message.total_turns`, `message.total_tokens_in`, `message.total_tokens_out`, `message.total_tool_calls`, `message.total_cost_dollars`, `message.status` | Int/Float/String |

#### 7.2.3 TracerProvider 初始化

```python
# server/observability/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter


def init_tracing(service_name: str, endpoint: str) -> None:
    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
```

引擎中直接使用 OTel context manager，不通过事件总线中转：

```python
# server/engine/query_loop.py
from opentelemetry import trace

tracer = trace.get_tracer("iwork.engine")


class QueryLoopEngine:
    async def _run_message_loop(self, msg: Message):
        # 根 Span
        with tracer.start_as_current_span(
            "message",
            attributes={
                "session.id": str(self.session_id),
                "user.id": str(self.user_id),
                "message.id": str(msg.id),
                "mode": msg.mode,
                "model": msg.model,
            },
        ) as root:
            for turn in range(max_turns):
                # Turn Span
                with tracer.start_as_current_span(
                    "message_loop",
                    attributes={"agent.turn": turn},
                ):
                    # Context 构建
                    with tracer.start_as_current_span(
                        "build_context",
                        attributes={
                            "context.estimated_tokens": est_tokens,
                            "context.message_count": msg_count,
                        },
                    ):
                        ctx = await self._build_context(msg)

                    # LLM 调用
                    with tracer.start_as_current_span(
                        "llm_call",
                        attributes={
                            "llm.provider": settings.llm_provider,
                            "llm.model": msg.model,
                            "llm.stream": True,
                        },
                    ) as llm_span:
                        response = await self._call_llm(ctx)
                        llm_span.add_event("first_token", {"ttft_ms": ttft})
                        llm_span.add_event("tokens", {
                            "input": tokens_in, "output": tokens_out,
                            "cache_read": cache_read, "cache_write": cache_write,
                        })
                        llm_span.set_attribute("llm.stop_reason", stop_reason)

                    # 工具执行
                    for tool_call in response.tool_calls:
                        with tracer.start_as_current_span(
                            "tool_dispatch",
                            attributes={
                                "tool.name": tool_call.name,
                                "tool.location": tool_call.location,
                            },
                        ):
                            # 嵌套 tool_execute span
                            with tracer.start_as_current_span("tool_execute"):
                                result = await self._execute_tool(tool_call)
                                # span 结束后自动记录 duration

            # 根 Span 结束前设置聚合属性
            root.set_attributes({
                "message.total_turns": total_turns,
                "message.total_tokens_in": total_tokens_in,
                "message.total_tokens_out": total_tokens_out,
                "message.total_cost_dollars": total_cost,
                "message.status": status,
            })
```

> **为什么不由事件驱动**：OTel 的 context manager 自动管理 Span 的 start/end 生命周期和父子嵌套关系，比手动维护 `dict[str, Span]` 匹配事件更可靠。trace context 通过 `contextvars` 在 asyncio 协程间自动传播，无需显式传递。

#### 7.2.4 Context 传播

trace_id 通过 HTTP 请求头 `traceparent` 进入系统，在 AsyncIO 协程间自动传递：

```python
# server/observability/middleware.py
from opentelemetry.propagate import extract
from opentelemetry import context

@app.middleware("http")
async def otel_middleware(request: Request, call_next):
    ctx = extract(request.headers)
    token = context.attach(ctx)
    try:
        response = await call_next(request)
    finally:
        context.detach(token)
    return response
```

`enqueue()` 作为 HTTP handler 调用链的一部分，自动继承 trace context → 经由 `asyncio` 传递到 `run_message()` → 所有子 Span 自动归属同一 trace。

### 7.3 指标 (Metrics)

#### 7.3.1 指标清单

**LLM 调用指标：**

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `agent_llm_call_duration_seconds` | Histogram | `model`, `provider`, `status` | LLM 调用耗时，buckets: [0.5, 1, 2, 5, 10, 20, 30, 60, 120] |
| `agent_llm_token_usage_total` | Counter | `model`, `token_type` | Token 用量累计，token_type: input/output/cache_read/cache_write |
| `agent_llm_cost_dollars_total` | Counter | `model` | 调用成本（token 用量 × 模型单价） |

**工具执行指标：**

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `agent_tool_call_total` | Counter | `tool_name`, `location`, `status` | 工具调用次数 |
| `agent_tool_call_duration_seconds` | Histogram | `tool_name`, `location` | 工具执行耗时，buckets: [0.1, 0.5, 1, 5, 10, 30, 60, 120] |
| `agent_tool_permission_denied_total` | Counter | `tool_name` | 权限拒绝次数，安全告警用 |

**消息 / 队列指标：**

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `agent_message_total` | Counter | `mode`, `status` | 消息处理总数，status: success/error/timeout/cancelled |
| `agent_message_duration_seconds` | Histogram | `mode` | 消息从入队到终态总耗时，buckets: [5, 15, 30, 60, 120, 300] |
| `agent_message_turns_total` | Histogram | — | 每条消息消耗的 turn 数分布，buckets: [1, 3, 5, 10, 15, 25] |
| `agent_queue_depth` | Gauge | `session_id` | 当前排队消息数 |

**会话 / 系统指标：**

| 指标名 | 类型 | 标签 | 说明 |
|--------|------|------|------|
| `agent_session_active` | Gauge | — | 当前 PROCESSING 状态的会话数 |
| `agent_stream_buffer_size` | Gauge | `session_id` | StreamBuffer 缓存条目数，断线异常检测 |

#### 7.3.2 Meter 定义与引擎直调

Instrument 定义集中在 `observability/metrics.py`，引擎直接 import 调用——不比 emit 事件多写任何代码：

```python
# server/observability/metrics.py
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.prometheus import PrometheusMetricReader


def init_meter() -> None:
    reader = PrometheusMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)


meter = metrics.get_meter("iwork.agent")

# LLM 调用指标
llm_call_duration = meter.create_histogram(
    "agent_llm_call_duration_seconds", description="LLM 调用耗时", unit="s")
llm_token_usage = meter.create_counter(
    "agent_llm_token_usage_total", description="Token 用量累计", unit="tokens")
llm_cost = meter.create_counter(
    "agent_llm_cost_dollars_total", description="LLM 调用成本", unit="dollars")

# 工具执行指标
tool_call_total = meter.create_counter(
    "agent_tool_call_total", description="工具调用次数")
tool_call_duration = meter.create_histogram(
    "agent_tool_call_duration_seconds", description="工具执行耗时", unit="s")

# 消息 / 队列指标
message_total = meter.create_counter(
    "agent_message_total", description="消息处理总数")
queue_depth = meter.create_up_down_counter(
    "agent_queue_depth", description="当前排队消息数")
```

引擎侧直接一行调用，无需定义 eVent 类型、写 handler、注册订阅者：

```python
# server/engine/query_loop.py
from observability.metrics import (
    llm_call_duration, llm_token_usage, llm_cost,
    tool_call_total, message_total, queue_depth,
)

# LLM 调用完成时
llm_call_duration.record(elapsed_ms / 1000, attributes={
    "model": msg.model, "provider": provider, "status": "ok",
})
llm_token_usage.add(tokens_in, attributes={"model": msg.model, "token_type": "input"})
llm_token_usage.add(tokens_out, attributes={"model": msg.model, "token_type": "output"})
if cache_read:
    llm_token_usage.add(cache_read, attributes={"model": msg.model, "token_type": "cache_read"})
llm_cost.add(cost, attributes={"model": msg.model})

# 工具执行后
tool_call_total.add(1, attributes={
    "tool_name": name, "location": location,
    "status": "success" if ok else "failed",
})

# 消息入队 / 完成
queue_depth.add(1, attributes={"session_id": str(sid)})
message_total.add(1, attributes={"mode": mode, "status": status})
```

> 指标记录直接嵌在引擎代码中，与 tracing span、structlog 并列。三者各自一行，没有中间层。

#### 7.3.3 Grafana 告警规则

| 告警 | PromQL 条件 | 严重度 |
|------|------------|--------|
| LLM 错误率 > 5% | `rate(agent_llm_call_duration_seconds_count{status="error"}[5m]) / rate(agent_llm_call_duration_seconds_count[5m]) > 0.05` | Warning |
| LLM 错误率 > 10% | 同上 > 0.10 | Critical |
| 工具权限拒绝异常 | `increase(agent_tool_permission_denied_total[10m]) > 3` | Warning |
| 平均 turn 数过高 | `histogram_quantile(0.5, rate(agent_message_turns_total_bucket[30m])) > 20` | Warning |
| 队列堆积 | `agent_queue_depth > 5` | Warning |

### 7.4 结构化日志

从现有 `log.warning(...)` 迁移到 structlog，自动关联 trace_id：

```python
# server/observability/logging.py
import structlog
from opentelemetry import trace

def add_otel_context(logger, method_name, event_dict):
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        add_otel_context,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()
```

**日志级别约定：**

| 级别 | 内容 | 示例 |
|------|------|------|
| **INFO** | 正常流程节点 | `llm_call_started`, `tool_executed`, `message_completed` |
| **WARNING** | 降级/重试/资源紧张 | `context_compressed`, `llm_retrying`, `queue_near_full` |
| **ERROR** | 调用失败/超时/异常终止 | `llm_call_failed`, `tool_timeout`, `message_error` |

#### 7.4.1 引擎直调

structlog 本身就是接口层，无需再包一层。各模块直接 `logger.info(...)` / `logger.error(...)`，上下文通过 `structlog.contextvars` 绑定：

```python
# server/engine/query_loop.py
from observability.logging import logger

class QueryLoopEngine:
    async def _run_message_loop(self, msg: Message):
        # bind 会话级上下文，整个 message loop 自动携带
        log = logger.bind(
            session_id=str(self.session_id),
            user_id=str(self.user_id),
            message_id=str(msg.id),
        )

        log.info("message_started", mode=msg.mode)

        for turn in range(max_turns):
            log.info("llm_call_started", turn=turn, model=msg.model)

            try:
                response = await self._call_llm(...)
            except Exception:
                log.error("llm_call_failed", turn=turn, error_type=...)
                raise

            log.info("llm_call_completed", turn=turn,
                     tokens_in=tokens_in, tokens_out=tokens_out,
                     cache_hit=cache_read, duration_ms=elapsed_ms,
                     stop_reason=stop_reason)

            log.info("tool_executed", tool_name=name, location=location,
                     duration_ms=dur, success=ok)

        log.info("message_completed", total_turns=turn,
                 total_tokens_in=..., total_tokens_out=...,
                 total_cost=..., duration_s=..., status=status)
```

> 日志散落在引擎各处，不是只存在于"生命周期关键节点"——比如内部判断分支、重试逻辑、异常捕获处都可能需要 `logger.debug(...)`。这些位置天然不适合定义 EventType 然后走 EventBus。

### 7.5 审计日志

审计日志记录 **谁** 在 **什么时间** 对 AI Agent 做了 **什么事情** 以及 **结果如何**，是安全合规和事后追溯的核心依据。独立写入 PostgreSQL，与日志系统分离，保证长期保留和不可篡改性。

**记录的行为包括：**

- **用户操作**：发送消息、修改配置、安装 Skill、连接 MCP 服务、计划确认/拒绝
- **AI 操作**：读文件、写文件、执行 Shell 命令
- **系统事件**：致命错误（第 4 级）

每条记录通过 `user_id`（谁） + `created_at`（什么时间） + `action`（做了什么） + `detail`（结果/详情） 完整还原操作全貌。

**写入方式分两路：**

- **EventBus 通路**：Agent 循环内产生的事件（工具执行、致命错误、计划交互），由 `AuditSubscriber` 订阅 EventBus 自动写入，覆盖 AI 操作和系统事件
- **`audit_log()` 直写通路**：API 层用户操作（发送消息、改配置、安装 Skill、连接 MCP），在对应路由中直接调用 `audit_log()` 写入，不走 EventBus

**审计记录原则：存元数据，不存本体**

审计表不是数据镜像——`detail` 字段记录**关键参数和摘要**即可，完整内容由 `resource` 关联到业务表获取：

- `user.message_sent` 存 `{mode, model, file_count, content_preview}`，完整消息体通过 `message_id` 去 `messages` 表查
- `tool.file_write` 存 `{path, size, operation}`，完整文件内容通过 `file/{path}` 获取
- `tool.shell_exec` 存 `{command, cwd, exit_code}`，完整输出不落审计

如需直观可读性，可在 `detail` 中加 `content_preview`（截断前 100 字符）：

```python
# 消息发送 API —— audit_log() 直写示例
await audit_log(
    db,
    action="user.message_sent",
    user_id=current_user.id,
    session_id=session_id,
    message_id=msg.id,
    resource=f"message/{msg.id}",
    detail={
        "mode": body.mode,
        "model": body.model,
        "file_count": len(body.files or []),
        "content_preview": body.content[:100],     # 截断摘要，审计可读
        "skill_ids": body.skill_ids or [],
    },
)
```

#### 7.5.1 数据模型

```sql
CREATE TABLE audit_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- 审计记录唯一 ID
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),          -- 记录创建时间
    user_id     UUID NOT NULL REFERENCES users(id),          -- 操作者用户 ID
    session_id  UUID,                                        -- 所属会话 ID（可为空，如登录事件不绑定会话）
    message_id  UUID,                                        -- 所属消息 ID（可为空，如会话级事件不绑定消息）
    action      VARCHAR(50)  NOT NULL,                       -- 操作类型，如 tool.file_write / session.create / auth.login
    resource    VARCHAR(100),                                -- 操作目标资源，如文件路径 / 会话 ID / 工具名
    detail      JSONB NOT NULL,                              -- 操作详情（JSON），包含变更摘要、关键参数等
    client_ip   VARCHAR(45),                                 -- 客户端 IP（支持 IPv6）
    user_agent  TEXT                                         -- 客户端 User-Agent
);

CREATE INDEX idx_audit_user    ON audit_logs(user_id, created_at);
CREATE INDEX idx_audit_action  ON audit_logs(action, created_at);
CREATE INDEX idx_audit_session ON audit_logs(session_id, created_at);
```

```python
# server/observability/audit.py
async def audit_log(
    db: AsyncSession,
    action: str,
    user_id: UUID,
    detail: dict,
    *,
    session_id: UUID | None = None,
    message_id: UUID | None = None,
    resource: str | None = None,
):
    await db.execute(
        text("""
            INSERT INTO audit_logs (user_id, session_id, message_id, action, resource, detail)
            VALUES (:uid, :sid, :mid, :act, :res, :det)
        """),
        {"uid": user_id, "sid": session_id, "mid": message_id,
         "act": action, "res": resource, "det": json.dumps(detail, default=str)},
    )
```

#### 7.5.2 审计动作类型

| action | resource | 触发时机 | 写入方式 | 记录结果 | detail 示例 |
|--------|----------|---------|---------|---------|-------------|
| `user.message_sent` | `message/{id}` | 用户发送消息 | `audit_log()` 直写 | 记录消息模式、模型、上下文文件数 | `{mode, model, workspace, file_count}` |
| `tool.file_read` | `file/{path}` | AI 读文件 | EventBus → AuditSubscriber | 记录文件路径、大小、是否在 workspace 内 | `{path, size, within_workspace}` |
| `tool.file_write` | `file/{path}` | AI 写文件 | EventBus → AuditSubscriber | 记录文件路径、大小、操作类型（create/update/delete） | `{path, size, operation}` |
| `tool.shell_exec` | `shell` | AI 执行命令 | EventBus → AuditSubscriber | 记录命令内容、工作目录、退出码 | `{command, cwd, exit_code, truncated}` |
| `tool.permission_denied` | `tool/{name}` | 工具调用被拒绝 | EventBus → AuditSubscriber | 记录被拒绝的工具和原因 | `{tool_name, reason}` |
| `user.config_changed` | `session/{id}` | 用户改配置 | `audit_log()` 直写 | 记录变更字段、新旧值 | `{field, old_value, new_value}` |
| `user.skill_installed` | `skill/{id}` | 安装 Skill | `audit_log()` 直写 | 记录 Skill 名称和来源 | `{skill_name, source}` |
| `user.mcp_connected` | `mcp/{name}` | 连接 MCP 服务 | `audit_log()` 直写 | 记录服务名和端点 | `{server_name, endpoint}` |
| `system.error_fatal` | — | 第 4 级致命错误 | EventBus → AuditSubscriber | 记录错误类型、消息、trace_id 用于关联日志 | `{error_type, message, trace_id}` |
| `user.plan_action` | `message/{id}` | 计划确认/拒绝/编辑 | EventBus → AuditSubscriber | 记录用户对计划的操作和快照 | `{action, plan_text_snippet}` |

#### 7.5.2.1 写入实现

**方案 A：EventBus 通路**（Agent 循环内事件）

审计日志消费者订阅 EventBus，将 Agent 循环内产生的事件自动写入 PostgreSQL：

```python
# server/observability/audit.py

AUDIT_EVENT_TYPES = {
    AgentEventType.TOOL_EXECUTED,         # → tool.file_read / tool.file_write / tool.shell_exec
    AgentEventType.TOOL_PERMISSION_DENIED, # → tool.permission_denied
    AgentEventType.MESSAGE_ERROR,          # → system.error_fatal
    AgentEventType.PLAN_INTERACTION,       # → user.plan_action
}


class AuditSubscriber:
    """订阅需要审计的 AgentEvent，写入 audit_logs 表。"""

    def __init__(self, db_session_factory):
        self.db_factory = db_session_factory

    async def handle(self, event: AgentEvent) -> None:
        if event.type not in AUDIT_EVENT_TYPES:
            return

        action = self._map_action(event)
        async with self.db_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO audit_logs (user_id, session_id, message_id, action, resource, detail)
                    VALUES (:uid, :sid, :mid, :act, :res, :det)
                """),
                {"uid": event.user_id, "sid": event.session_id,
                 "mid": event.message_id, "act": action,
                 "res": self._map_resource(event),
                 "det": json.dumps(event.data, default=str)},
            )

    def _map_action(self, event: AgentEvent) -> str:
        if event.type == AgentEventType.TOOL_EXECUTED:
            tool_name = event.data.get("tool_name", "")
            if tool_name in ("read_file", "glob", "grep"):
                return "tool.file_read"
            if tool_name in ("write_file", "edit_file"):
                return "tool.file_write"
            if tool_name == "bash":
                return "tool.shell_exec"
            return f"tool.{tool_name}"
        if event.type == AgentEventType.TOOL_PERMISSION_DENIED:
            return "tool.permission_denied"
        if event.type == AgentEventType.MESSAGE_ERROR:
            return "system.error_fatal"
        if event.type == AgentEventType.PLAN_INTERACTION:
            return "user.plan_action"
        return "unknown"

    def _map_resource(self, event: AgentEvent) -> str | None:
        d = event.data
        if event.type == AgentEventType.TOOL_EXECUTED:
            tool = d.get("tool_name", "")
            inp = d.get("input", {})
            if tool in ("read_file", "glob", "grep"):
                return inp.get("file_path") or inp.get("pattern")
            if tool in ("write_file", "edit_file"):
                return inp.get("file_path")
            if tool == "bash":
                return inp.get("command")
            return tool
        if event.type == AgentEventType.MESSAGE_ERROR:
            return d.get("error_type")
        return None
```
	    
**方案 B：`audit_log()` 直写**（API 层用户操作）

API 层的用户操作不在 Agent 循环内，直接在对应路由中调用 `audit_log()` 写入，无需经过 EventBus：

```python
# server/observability/audit.py
async def audit_log(
    db: AsyncSession,
    action: str,
    user_id: UUID,
    detail: dict,
    *,
    session_id: UUID | None = None,
    message_id: UUID | None = None,
    resource: str | None = None,
):
    """独立审计写入函数，供非 EventBus 场景直接调用。"""
    await db.execute(
        text("""
            INSERT INTO audit_logs (user_id, session_id, message_id, action, resource, detail)
            VALUES (:uid, :sid, :mid, :act, :res, :det)
        """),
        {"uid": user_id, "sid": session_id, "mid": message_id,
         "act": action, "res": resource, "det": json.dumps(detail, default=str)},
    )
```

| 调用位置 | action | 说明 |
|---------|--------|------|
| 消息发送 API | `user.message_sent` | 用户发送新消息时 |
| 配置修改 API | `user.config_changed` | 用户修改会话配置时 |
| Skill 安装 API | `user.skill_installed` | 用户安装 Skill 时 |
| MCP 连接 API | `user.mcp_connected` | 用户连接 MCP 服务时 |

> 审计写入分两路的原因：Agent 循环内的事件适合走 EventBus（集中映射 action、解耦），API 层的用户操作适合直接调用 `audit_log()`（简单直接，调用点已在请求上下文中持有 db session）。

#### 7.5.3 审计查询 API

```
GET /admin/audit?user_id=X&action=tool.shell_exec&from=2026-07-01&to=2026-07-27&page=1
→ { items: [...], total: 42, page: 1, page_size: 50 }
```

仅管理员角色可访问。

### 7.6 用户侧可观测性

用户侧数据通过现有 NDJSON 流实时推送，不经过 OTel 通道。

#### 7.6.1 实时执行步骤

在现有 `agent.thinking`、`agent.text`、`client.tool_request` 基础上，新增步骤进度事件：

```typescript
// 步骤开始（Plan/Build 模式）
{
  type: "agent.step",
  seq: number,
  message_id: string,
  step: 3,
  total_steps: 7,
  description: "正在编写爬虫主逻辑...",
  status: "running"      // pending | running | done | failed
}

// 步骤结束
{
  type: "agent.step_completed",
  seq: number,
  message_id: string,
  step: 3,
  status: "done",        // done | failed | skipped
  summary: "已创建 crawler.py，包含请求头和重试逻辑"
}
```

#### 7.6.2 Token 用量与成本追踪

```typescript
// 每条消息完成时推送
{
  type: "message.usage",
  seq: number,
  message_id: string,
  tokens: {
    input: 15200, output: 2350,
    cache_read: 4200, cache_write: 1800
  },
  cost: {
    input: 0.015,      // $0.015
    output: 0.007,     // $0.007
    total: 0.022,      // $0.022
    currency: "USD"
  },
  savings: {
    cache_discount: 0.004,
    effective_cost: 0.018
  }
}

// 会话累计（每次 message.usage 后更新）
{
  type: "session.usage",
  seq: number,
  session_id: string,
  tokens_total: { input: 45000, output: 6800 },
  cost_total: 0.067,
  message_count: 4
}
```

模型单价配置在服务端 `server/observability/pricing.json`：

```json
{
  "deepseek-v4-pro":   { "input": 0.00014, "output": 0.00028, "cache_read": 0.000014 },
  "claude-opus-4-7":   { "input": 0.015,   "output": 0.075,   "cache_read": 0.0015 },
  "claude-sonnet-4-6": { "input": 0.003,   "output": 0.015,   "cache_read": 0.0003 }
}
```

单位：美元 / 1K tokens。

#### 7.6.3 错误诊断卡片

将现有 `message.error` 增强为结构化诊断：

```typescript
{
  type: "error.diagnosis",
  seq: number,
  message_id: string,
  error_code: "llm_rate_limit_exhausted",   // 机器可读
  severity: "fatal",                          // info | warning | fatal
  title: "AI 服务暂时不可用",
  explanation: "短时间内请求过多，AI 服务已触发速率限制，3 次重试均失败。",
  suggestion: "请等待 1-2 分钟后再发送新消息，或切换到其他可用模型。",
  detail: {
    model: "deepseek-v4-pro",
    retry_count: 3,
    next_available_estimate: "60s"
  }
}
```

错误码清单：

| error_code | title | suggestion |
|-----------|-------|------------|
| `llm_rate_limit_exhausted` | AI 服务暂时不可用 | 等待 1-2 分钟或切换模型 |
| `llm_auth_failed` | API 密钥失效 | 联系管理员检查 API 配置 |
| `context_token_exceeded` | 对话上下文过长 | 开启新任务或删除部分历史消息 |
| `tool_permission_denied` | 操作被安全策略拦截 | 检查工作空间权限设置 |
| `tool_execution_timeout` | 工具执行超时 | 重试或检查网络连接 |
| `max_turns_reached` | 任务步骤过多 | 拆分任务为更小的子任务 |
| `loop_detected` | 检测到重复循环 | AI 已自动终止，请重新描述需求 |
| `client_disconnected` | 客户端连接中断 | AI 在后台继续，刷新页面恢复 |
| `queue_full` | 消息队列已满 | 等待当前任务完成后发送 |
| `session_archived` | 任务已归档 | 无法发送新消息，请创建新任务 |

#### 7.6.4 会话回放

**持久化**：扩展现有 `StreamBuffer`，所有带 `seq` 的数据块同时写入 `stream_events` 表：

```sql
CREATE TABLE stream_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL,
    message_id  UUID NOT NULL,
    seq         INT NOT NULL,
    chunk       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_stream_session_seq ON stream_events(session_id, seq);
```

**回放 API**：

```
GET /sessions/{id}/replay?message_id=xxx
→ NDJSON 流，按 seq 顺序逐条推送该消息的所有历史数据块
  （格式与实时流完全一致，前端可复用同一渲染逻辑）
```

**前端回放播放器**：

```
┌─ 会话回放 ───────────────────────────────────────┐
│                                                    │
│  [消息1]  [消息2]  [消息3]   ← 消息时间线选择器     │
│                                                     │
│  ┌──────────────────────────────────────────────┐  │
│  │  AI 回复 (回放中)                             │  │
│  │  ▼ 思考与工具调用                             │  │
│  │    ┌ Thinking ───────────────────────────┐  │  │
│  │    │ 我需要先读取文件内容，然后分析结构... │  │  │
│  │    └──────────────────────────────────────┘  │  │
│  │    ┌ Tool: read_file ✓ ──────────────────┐  │  │
│  │    │ src/utils.ts (120 行)               │  │  │
│  │    └──────────────────────────────────────┘  │  │
│  │  ▲ 最终回复                                  │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  ▶ 播放   ⏸ 暂停   ⏩ 加速                          │
└─────────────────────────────────────────────────────┘
```

支持：实时速度播放、2x/4x 加速、step-by-step 逐条展开。

---

<a id="8-hooks-系统"></a>

## 8. Hooks 系统

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

## 9. 多 Agent 协作

> 状态：设计中。以下为方案讨论稿，尚未实现。

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
6. <available_memories> XML（继承父 agent）
7. 输出规范指令（<final_output> 标签要求，框架注入）
```

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

<a id="10-上下文管理"></a>

## 10. 上下文管理

Agent 多轮对话中，上下文窗口是稀缺资源。每轮对话都会累积 System Prompt、对话历史、工具调用记录、工具返回结果、RAG 文档等内容，几十轮后必然溢出。本章给出系统性的上下文管理方案——不是被动应急压缩，而是主动的信息生命周期管理。

### 10.1 问题定义

Agent 上下文窗口内的内容可归纳为 3 类：

| 类型 | 膨胀特征 | 可控性 |
|------|---------|--------|
| System Prompt | 固定大小，通常 500~2000 token | 完全可控 |
| Skill / Tool / Subagent / Memory 配置注入 | 各数百 token，可按需注入 | 可按需注入 |
| 对话历史（含推理步骤、工具调用及结果、RAG 检索文档、结构化状态等） | 线性增长，多步推理时爆炸，单次工具返回可能 5000+ token | 可压缩/裁剪/清理 |


整个上下文封装策略在两个底层机制上构建：

- **提示缓存（Prompt Caching）**：LLM 提供方（如 Anthropic）支持将请求中稳定的前缀部分标记为 cacheable，服务端缓存其 KV 状态，后续请求命中缓存时降低延迟和成本。设计上下文布局时，将不变的内容（核心约束、配置索引）与变化的内容（对话历史）明确分界——分界线之前是缓存前缀，之后是动态区。缓存命中率直接受分界线位置影响。
- **注意力分布**：LLM 对上下文不同位置的注意力权重不均匀——开头和结尾获得更多关注，中间位置容易被忽略（Lost in the Middle 现象）。因此，关键指令（核心约束）应放在头部注意力热区，当前任务上下文放在尾部焦点位置，中间适合放"知道有、需要时可追溯"的参考信息（摘要层）。上下文拼装顺序不是简单的拼接，而是有意识的位置设计。

### 10.2 上下文三分类处理总览

10.1 将上下文窗口内的内容归纳为 3 类，每一类对应不同的处理策略：

| 类别 | 内容 | 膨胀特征 | 处理策略 |
|------|------|---------|---------|
| System Prompt | 任务边界、安全规则、输出格式等 | 固定 500~2000 token | 抽核心约束钉死在窗口头部，其余可压缩 |
| 配置注入 | Skill / Tool / Subagent / Memory | 各数百 token，数量越多越长 | 按需注入，不用的不进窗口 |
| 对话历史 | 推理步骤、工具调用及结果、RAG 检索、用户交互 | 线性增长，多步推理时爆炸 | 分层压缩 + 外部记忆卸载与召回 |

```
上下文窗口
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │ 第一类：System Prompt                              │    │
│  │  ├── 核心约束（钉死，永不压缩，~500 token）          │    │
│  │  └── 说明性内容（首次压缩时可丢弃）                  │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ 第二类：配置注入                                    │    │
│  │  按任务匹配，按需注入                                │    │
│  │  Skill / Tool / Subagent / Memory                  │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ 第三类：对话历史                                    │    │
│  │  ┌───────────────────────────────────────────┐      │    │
│  │  │ 滑动窗口    ← 最近 N 轮完整保留             │      │    │
│  │  ├───────────────────────────────────────────┤      │    │
│  │  │ 单块压缩    ← 粒度 1：单块压缩（规则）       │      │    │
│  │  ├───────────────────────────────────────────┤      │    │
│  │  │ 多块压缩    ← 粒度 2：多块压缩（LLM）         │      │    │
│  │  └───────────────────────────────────────────┘      │    │
│  │  │ 卸载 ↓（原文 / 旧摘要移出窗口）  ↑ 召回           │    │
│  ├─────────────────────────────────────────────────┤    │
│  │ 当前用户输入                                        │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
└─────────────────────────────────────────────────────────┘
                    ↓ 卸载              ↑ 召回
                    │
┌───────────────────┴─────────────────────────────────────┐
│ 外部记忆（不在窗口内）                                    │
│  └── 向量库（pgvector）：卸载的对话原文 + 旧摘要块        │
└─────────────────────────────────────────────────────────┘
```

**运行时流程**：

```
每轮对话开始：

1. 拼装前准备
   ├── 第一类（核心约束）钉死窗口头部，不参与压缩 / 阈值计算
   └── 第二类（配置注入）按当前任务匹配，只注入用到的 Skill / Tool / Subagent / Memory

2. 窗口用量判断
   ├── < 80% → 正常追加本轮 block，跳到步骤 4
   └── ≥ 80% → 进入步骤 3

3. 第三类（对话历史）分层处理
   ├── 滑动窗口：最近 N 轮（通常 10 轮）原文完整保留，不压缩
   ├── 压缩（10.5.2）——对超出滑动窗口的历史：
   │     ├── 粒度 1 单块压缩：单块压缩（LLM 摘要）
   │     └── 粒度 2 多块压缩：按 artifact 归组合并（LLM，标注精度标签）
   └── 压缩后重新估算：
         ├── ≤ 75% → 结束，跳到步骤 4
         └── > 75% → 卸载（10.5.3）：
               1. 对窗口内每个 block 跑 retention_score 四维评分排序
               2. 从低分到高分卸载，直到窗口降到 45% 以下
               3. 卸载块写入向量库
               4. 窗口内为已卸载块留一行索引

4. 拼装上下文 + 被动召回（build_context 固化）
   ├── 拼装顺序：核心约束 → 配置注入 → 摘要层 → 滑动窗口原文 → 用户输入
   └── 被动召回：TF-IDF 速查 → 向量库检索 → 合并去重注入

5. 发送请求；Agent 推理中若觉缺上下文，可主动调 recall 工具做针对性召回
```

**设计理念：缓存友好 + 注意力感知**：

上述拼装顺序并非随意排列，而是同时服务于两个目标——最大化提示缓存命中率，以及适配 LLM 的注意力分布特性。

**缓存友好的上下文布局**：

```
请求体
┌─────────────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────┐               │
│  │ 缓存前缀（cacheable，跨轮复用）         │               │
│  │  核心约束 ..................... 500 tok│               │
│  │  配置索引（Skill/Tool/Subagent 列表）   │               │
│  │  匹配的 Memory 索引                   │               │
│  ├──────────────────────────────────────┤ ← 缓存断点     │
│  │ 动态区（每轮变化，不缓存）              │               │
│  │  选中注入的 SKILL.md / AGENT.md 全文   │               │
│  │  摘要层（压缩后的旧历史）               │               │
│  │  滑动窗口原文（最近 N 轮）              │               │
│  │  当前用户输入                         │               │
│  └──────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

核心约束和配置索引在会话内几乎不变，天然适合作为缓存前缀。选中的 Skill/Subagent 完整内容虽然也在 System Prompt 区域，但每轮可能变化（不同任务激活不同 Skill），放在断点之后更安全。对话历史及其摘要每轮必然变化，放在动态区。

实际的缓存命中率取决于前缀稳定性——如果每轮选中的 Skill 组合不同导致前缀变化，缓存会失效。折中策略是将最常用的配置索引固定在前缀中，按需注入的完整内容统一放断点之后。

**注意力感知的位置策略**：

LLM 的注意力分布有两个热区——**开头（首因效应）和结尾（近因效应）**，中间位置注意力权重最低（Lost in the Middle）。这直接指导了拼装顺序：

```
注意力热度
  ↑  ████                                    ████
  │  ████                                    ████
  │  ████                                    ████
  │  ████   (中间区域注意力衰减)               ████
  │  ████  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ████
  │  ████  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  ████
  └──████──░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░──████──→ 上下文位置
    头部                                   尾部
  核心约束                                当前输入
  (必须记住)                              (正在处理)
         ↑                                  ↑
    关键指令放这里                    最新信息放这里
```

拼装顺序的对应关系：

| 位置 | 内容 | 注意力角色 |
|------|------|-----------|
| 头部 | 核心约束 + 配置索引 | 锚点——Agent 每次推理都先"看到"任务边界和安全规则 |
| 中前部 | 选中的 SKILL.md / AGENT.md 全文 | 参考——需要时查阅，不是每轮都用到 |
| 中部 | 摘要层 | 背景——知道有这些历史，需要时可追溯 |
| 中后部 | 滑动窗口原文 | 上下文——最近对话的完整记录 |
| 尾部 | 当前用户输入 | 焦点——Agent 正在处理的问题，获得最强近因注意力 |

关键设计决策：**核心约束放在头部而非仅靠 System Prompt 位置**——如果只依赖模型训练时的 System Prompt 处理机制，在超长上下文中约束可能被稀释。将其显式放在上下文开头，利用首因效应确保约束始终处于注意力热区。

### 10.3 第一类：System Prompt — 核心约束钉死，其余可压缩

**做什么**：从 System Prompt 中抽出一份不可变约束清单，钉在窗口最前面，不参与任何压缩。System Prompt 中硬约束钉死，其余说明性内容（如工具使用指南、背景介绍）同样保留在窗口中，不进入压缩或丢弃流程。

**核心约束通常包括**：

| 约束类型 | 示例 |
|---------|------|
| 任务边界 | "不要修改生产数据库"、"只读权限" |
| 输出格式契约 | 必须返回的 JSON schema、字段规范 |
| 安全规则 | 隐私处理、敏感信息脱敏 |

**实现方式**：不是靠 prompt 里写"这条很重要"——模型在长上下文中仍然可能忽略。工程上在拼装上下文的代码层做硬拼接：

```python
def assemble_context(core_constraints, config_injection, summary, recent_msgs, user_input):
    """每次拼装上下文时，Core Constraints 永远在最前面，配置注入紧随其后"""
    parts = [
        core_constraints,             # 固定，不可变（第一类）
        config_injection,             # 按需注入（第二类）
        summary,                      # 递归摘要（第三类）
        recent_msgs,                  # 滑动窗口（第三类）
        user_input,                   # 当前输入
    ]
    return "\n\n".join(p for p in parts if p)
```

Core Constraints 的 token 预算固定（建议 500 token 以内），不随对话增长，不参与压缩计算，因此也不计入压缩阈值判断。

### 10.4 第二类：配置注入 — 轻量索引常驻，完整内容按需加载

配置注入分两层：**轻量索引**（name + description）常驻窗口用于匹配决策，**完整内容**（SKILL.md / AGENT.md）选中后才注入。

**轻量索引（常驻）**：

Skill、Tool、Subagent 的 name 和 description 以紧凑列表形式始终在窗口中，token 开销极小（每条数十 token）。作用类似"菜单"——Agent 知道有哪些能力可用，但不占用大量空间。

```
可用的 Skill（轻量索引）：
  - superpowers:brainstorming — 创意工作前的需求探索和方案设计
  - superpowers:systematic-debugging — 遇到 bug 时先分析再修复
  - superpowers:test-driven-development — TDD 开发流程
  ...

可用的 Subagent：
  - Explore — 快速只读搜索，定位代码
  - Plan — 软件架构设计，输出实现计划
  - general-purpose — 通用多步任务执行
  ...
```

**完整内容（按需注入）**：

当 Agent 决定使用某个 Skill 或派发某个 Subagent 时，对应的 SKILL.md / AGENT.md 完整指令才会注入上下文。注入位置在 System Prompt 区域——本质上是 System Prompt 的动态扩展，放在核心约束之后：

```
核心约束（固定）
    ↓
Skill 完整指令（选中后注入，位于 System Prompt 扩展区）
    ↓
配置注入索引（name + description 常驻列表）
    ↓
对话历史 ...
```

**四类注入策略**：

| 类型 | 索引（常驻） | 完整内容（按需） | 注入位置 |
|------|------------|-----------------|---------|
| Skill | name + description 列表 | SKILL.md 全文 | System Prompt 扩展区 |
| Subagent | name + description 列表 | AGENT.md 全文 | System Prompt 扩展区 |
| Tool | 常用工具 name + description 常驻，冷门不常驻 | — | — |
| Memory | MEMORY.md 索引始终注入 | 匹配的具体记忆文件 | System Prompt 扩展区 |

**关键原则**：索引是"菜单"，完整内容是"菜品"。菜单常驻窗口让 Agent 知道有什么可用，选中后才把对应内容注入 System Prompt 扩展区。这能减少数百到数千 token 的固定开销。

### 10.5 第三类：对话历史 — 分层压缩 + 外部记忆

对话历史是上下文膨胀的主要来源。处理策略分三层：核心锚点 + 滑动窗口（基础层，不压缩）、递归摘要（压缩旧历史）、外部记忆（卸载 + 召回）。

### 对话历史的组成

对话历史是一轮轮消息交织而成，消息角色只有三种，其中 assistant 的内容又由三类 chunk 组成：

```
user          用户输入
assistant     助手回复，内容由三类 chunk 组成：
                · thinking   推理过程（Extended Thinking）
                · text       正文回复
                · tool_call  工具调用
tool_result   工具返回（紧跟 tool_call，角色为 tool）
```

三种 chunk 的 token 占比和信息密度差异很大，因此压缩策略不同（见「双粒度压缩」的分工表）。block 是压缩和卸载的最小操作单元，后文的「三层栈」「双粒度压缩」「卸载」都作用在这个单元上。

每个 block 关联一组元信息，供压缩和卸载使用：

| 属性 | 含义 | 用途 |
|------|------|------|
| `artifact` | 操作的产出物标识（文件/表/查询结果） | 多块压缩的分组依据（粒度 2） |
| `precision` | 精度标签（CONFIRMED / DERIVED / OBSOLETE / PENDING），压缩时由 LLM 标注 | 卸载评分权重（见「精度标签设计」） |
| `turn` | 所属轮次 | 距离当前轮的远近，分层与卸载依据 |
| `token_count` | token 数 | 卸载评分的成本维度 |

#### 10.5.1 核心锚点 + 滑动窗口

两者共同构成上下文窗口的"不压缩基础层"——保证 Agent 始终拥有任务边界意识（锚点）和短期完整上下文（滑动窗口）。

**核心锚点**：即 10.3 中的核心约束，从 System Prompt 中抽出，钉死在窗口最前面。不参与压缩计算、不计入压缩阈值。保证 Agent 无论对话多长都不会丢失任务边界、安全规则和输出格式契约。

**滑动窗口**：最近 N 轮对话完整保留在窗口中，不做任何压缩。窗口大小通常取 10 轮——平衡上下文新鲜度和 token 消耗。窗口随对话推进向前滑动，超出窗口边界的旧对话进入递归摘要流程。

**两者的关系**：

```
锚点（固定） + 窗口内原文（滑动）
     ↑                ↑
  永不压缩         完整保留
     └──────┬───────┘
        基础工作区
        （Agent 直接可用的完整信息）
              │
    ──────────┼──────────  超出窗口边界 →
              │
       单块压缩 → 多块压缩 → 卸载
       （旧历史的处理流水线）
```

两者都**不进入压缩和卸载流程**——只有超出滑动窗口边界的历史对话才会依次经历单块压缩 → 多块压缩 → 卸载。区别只在一个固定不动、一个随对话推进。10.3 已详述锚点的设计，下面讲滑动窗口的边界规则。

#### 10.5.2 带精度标签的两阶段压缩

**做什么**：对超出滑动窗口的历史对话分两个粒度逐层压缩——先对单个 block 压缩（截断），再将同类 block 合并为摘要。每条信息标注精度标签，窗口内形成 raw → compressed → meta-summary 三层栈结构。

**在上下文管理中的位置**：压缩是两阶段流水线的第一步。上下文超过 80% 阈值时，先压缩；压缩后若仍超 75%，才进入 10.5.3 的外部记忆卸载。压缩不改窗口结构，只缩短内容；卸载才把数据移出窗口。

---

### 三层栈结构

窗口内的信息按"距离当前轮的远近"自然形成三层，越远压得越狠：

```
[meta-summary]   ← 10+ 轮前：多个 compressed 再合并，递归深度 ≤2 层
[compressed]     ← 4-10 轮前：同类 block 合并为摘要，单块压缩
[raw block]      ← 最近 3 轮：完全不动，原样保留
[raw block]
[raw block]      ← 当前轮
```

| 层 | 范围 | 操作 | 示例 |
|---|------|------|------|
| raw | 最近 3 轮 | 不碰 | 当前正在执行的 tool_call 和返回结果 |
| compressed | 4-10 轮前 | 双粒度压缩 | 报表已生成，脚本从 v1 迭代到 v3 |
| meta-summary | 10+ 轮前 | 多个 compressed 合并 | "数据探索 → 生成报表 → 导出 CSV" |

**递归深度不超过 2 层**——compressed 已经丢失了大量细节，meta-summary 再压一次就是极限，继续往上压会丢失可供召回的关键信息，不如直接卸载到外部存储。

---

### artifact 打标签

在双粒度压缩之前，先给每个 block 打上 `artifact` 标签——它记录 block 操作的是哪个产出物。这个标签主要给**粒度 2 多块压缩**用：合并时按归一化后的 artifact 分组，把操作同一产出物的 block 归到一起交给 LLM 合并。

**artifact 的本质是「工具的操作对象」**：一条 tool_call 真正产出或作用的那个东西（一个文档，通常是文件）。压缩合并只认这一个条件——**操作对象归一化后一致 → 归同一组 → 交给 LLM 判断哪些冗余 / 被覆盖再合并**。

**打标签的时机**：block 创建时就打好（消息解析成 InfoBlock 的那一刻），发生在压缩之前，是纯规则操作、**不调用 LLM**。三层栈结构决定「哪些 block 何时压缩」，artifact 标签决定「压缩时哪些 block 归为同一产出物」。

#### 要打哪些内容

**唯一来源是 tool_call**：整段对话中只有 tool_call 真正「产生」产出物，其余 chunk 都不产生 artifact，只继承。artifact 打标签以「一条 assistant 消息」为单位整体解析，thinking / text / tool_result 的 artifact 只取决于**同消息内 tool_call 数量（0 / 1 / ≥2）**，与流式 chunk 先后顺序无关：

| 同消息 tool_call 数 | tool_call | tool_result | thinking / text |
|---------------------|-----------|-------------|-----------------|
| **0 个** | — | — | 继承 `pending_artifact`（上一条 tool_call，跨消息；若自上次 user 重置后还没有任何 tool_call，则 None） |
| **1 个** | 提取自己的 artifact | 继承这条 tool_call（靠 `tool_call_id` 归位） | 继承这条 tool_call 的 artifact |
| **≥2 个** | 各自独立提取，互不覆盖、互不共享 | 靠 `tool_call_id` 归位到对应那一条 | None（对应不到单一产出物） |

两条不变前提：**tool_call 是 artifact 唯一产出方**，其余 chunk 只继承、不产生；**user 始终 None**，并在 user 消息处把 `pending_artifact` 重置为 None。

`pending_artifact` 是跨消息传递的变量，**只在两处被改变**——遇到 user 消息重置为 None，遇到 tool_call 重新赋值为该 tool_call 的 artifact。它只服务于「同消息 0 个 tool_call」的 thinking / text：纯 thinking / 纯 text 是对上一条工具调用的思考或总结，所以跨消息继承 `pending_artifact`；若自上次 user 重置后还没出现过任何 tool_call，则自然为 None。

```
user:  "帮我看看 schema"                 ← user 消息，pending_artifact 重置为 None
assistant: [text "好的，我来看看"]        ← 同消息 0 个 tool_call，且尚无 tool_call → None
assistant: [thinking "先读 schema...", tool_call read_file("schema.sql")]
                                         ← 同消息 1 个 tool_call，thinking 继承 "schema.sql"
tool_result: ...                          ← 靠 tool_call_id 继承 "schema.sql"
assistant: [text "schema 里有 users 表"]   ← 同消息 0 个 tool_call，继承 pending_artifact = "schema.sql"
```

注意：thinking / text 与 tool_call 同在一条消息时，继承这条 tool_call 的 artifact（无论流式里谁先谁后）；只有同消息没有 tool_call 的纯 thinking / text 才跨消息继承 `pending_artifact`。

**同消息 ≥2 个 tool_call**：一条 assistant 消息的 `tool_calls` 是数组、可含多个调用，对应上表「≥2 个」那一行——每个 tool_call 独立提取自己的 artifact（互不覆盖、互不共享），tool_result 靠 `tool_call_id` 精确归位到对应那一条，同消息的 thinking / text 对应不到单一产出物故为 None（thinking 是对整批调用的推理、text 是总起，不参与多块压缩，同 user）：

```
assistant tool_calls: [
    read_file("a.sql"),     # tool_call_id=id1 → artifact="a.sql"
    read_file("b.sql"),     # tool_call_id=id2 → artifact="b.sql"
]
tool_result(tool_call_id=id1): a.sql 内容   → 继承 "a.sql"
tool_result(tool_call_id=id2): b.sql 内容   → 继承 "b.sql"
```

#### 怎么打 artifact

**提取逻辑**（tool_call → 文档名），按顺序两步：

1. **提取文档路径**：从工具参数里取文档路径——文件类工具（`read_file` / `write_file` / `edit_file`）取路径参数；`bash` 取 `-f` 后面的文件。取不到 → artifact = None。
2. **取文档名**：取路径的 basename（如 `src/schema.sql` → `schema.sql`）。

**为什么只提取文档名**：tool_call 的操作对象类型其实很多——文件/文档（`read_file`/`write_file`/`edit_file`）、数据库表、URL / 网络资源、内存查询结果（裸 SQL）、MCP / skill 脚本产出的抽象资源……真要给每种类型建一套「类型 → 取哪个参数」的映射，工具太多太灵活，穷举不完也维护不动。

但实际工作里，**文档操作占了绝大多数**：iWork 的产出物基本都是 `.sql` / `.md` 这类文档文件，读写改文件是最主流动作；数据库表操作大多也通过 `psql -f xxx.sql` 落到文件上，URL 和裸 SQL 只是少数边角。所以只做「提取文档路径 → 取文档名」这一条规则就够了——它覆盖主干场景，取不到文档路径的边角类型直接落 None，不必为它们硬造映射。

**None 兜底是安全的**：没有文档路径的工具（裸 SQL、URL 等）→ artifact = None，不参与多块压缩——artifact 本来就可选（thinking / text / user 都是 None），「不知道操作了什么文档」就标 None，不用硬算。

两步示例：
- `write_file("top50_v1.sql")` → ① 路径 `top50_v1.sql` ② 文档名 `top50_v1.sql` → 归一 `top50`
- `bash("psql -f top50_v3.sql")` → ① 路径 `top50_v3.sql`（取 `-f`）② 文档名 `top50_v3.sql` → 归一 `top50`
- `read_file("schema.sql")` → ① 路径 `schema.sql` ② 文档名 `schema.sql` → 归一 `schema`

artifact 比较不是简单的字符串相等，而是**归一化后匹配**——`top50_v1.sql`、`top50_v3.sql`、`schema.sql` 等，取文档名后去掉扩展名、版本后缀，归一到裸名再比较（`top50_v1` / `top50_v3` → `top50`）：

```python
import re

def normalize(name: str) -> str:
    # "top50_v3.sql" → "top50"
    # "top50"        → "top50"
    # "schema.sql"   → "schema"
    name = name.split("/")[-1]          # 去路径（保险，提取时通常已取 basename）
    name = name.rsplit(".", 1)[0]       # 去扩展名
    name = re.sub(r"_v\d+$", "", name)  # 去版本后缀，v1/v2/v3 归为同一文档
    return name
```

从 `top50_v1.sql` → `top50_v3.sql` → `psql -f top50_v3.sql`，归一化后全是 `top50`，判定为同一 artifact。

---

### 双粒度压缩

压缩操作不是对所有 block 统一处理，而是分两个粒度先后执行。两种粒度对不同 chunk 类型的分工如下：

| Chunk 类型 | 粒度 1：单块压缩（LLM 摘要） | 粒度 2：多块压缩（LLM） |
|-----------|----------------------------------|---------------------|
| **thinking** | 同消息有 text 直接丢弃；无 text 且 artifact=None（同消息多个 tool_call）时 LLM 单块摘要 | 无 text 且 artifact≠None 时随该组一起合并 |
| **text** | 不参与（信息密度高，留给粒度 2 轻度压缩） | 继承 artifact（同 thinking 的 0/1/≥2 规则），合并时轻度压缩（去寒暄/重复/示例） |
| **tool_call** | 不参与 | 提取 artifact 作分组键，合并时语义保留（留工具名 + 关键参数，去 JSON schema） |
| **tool_result** | 按大小分流：超短不压；中等 LLM 摘要；超大留待卸载 | 继承 artifact，参与同组合并，被覆盖版本压成一行 |
| **user** | 不参与 | 不参与（artifact = None，作为任务边界保留） |

分工要点：粒度 1 是「单块操作、命中冗余才压」的层，tool_result 按大小分流（超短不压 / 中等 LLM 摘要 / 超大留待卸载），「无 text 且 artifact=None（同消息多个 tool_call）」的 thinking 走 LLM 单块摘要；粒度 2 是「按 artifact 分组、交给 LLM 判断冗余并合并」的 LLM 层，text / tool_call 的压缩在这里随合并一起完成，「无 text 且 artifact≠None」的 thinking 也在此随组合并。

#### 粒度 1：单块压缩（独立操作）

只操作一个 block 自身，不做多块压缩。它处理 tool_result 和「无 text 且 artifact=None」的 thinking 两类；text / tool_call 信息密度高，统一留给粒度 2 的 LLM（见上方分工表）。目标是把"一句话能说清却占了大段空间"的冗余内容缩短。

**tool_result 的三级分流**（按大小）：
- 超短返回（如 `file written`）→ 不压，原样保留；
- 中等返回 → LLM 单块摘要成几句关键信息；
- 超大返回（大文件正文）→ 不压，原样留到 10.5.3 卸载阶段由 retention_score 推出窗口。

三档按 token 数切分（block 自带 `token_count`）：

| 档 | token 数 | 处理 |
|------|---------|------|
| 超短 | < 200 | 不压（压了省不了几个 token，还要付一次 LLM 调用） |
| 中等 | 200 ~ 10000 | LLM 单块摘要 |
| 超大 | > 10000 | 不压，留给卸载 |

超大块不在压缩阶段单独外部化，而是统一进入 10.5.3 卸载：token 成本维（>10000 得 0.1）+ 一次性数据惩罚维（`is_one_shot`→0）会共同压低它的 retention_score，把它卸载到向量库或直接丢弃；若只是要提取几个字段的大 JSON 结果，仍走 LLM 摘要（200~10000 档）。

**thinking 的分流**：thinking 的冗余是语义性的（结论 vs 中间推导 vs 转折点无表面标记），故按同消息有无 text 分流：有 text 直接丢弃；无 text 时 artifact≠None 进粒度 2 随组合并，artifact=None（≥2 个 tool_call，或 0 个 tool_call 且无 `pending_artifact` 可继承）则多块压缩按 artifact 归组也归不到它，改在此处用 LLM 单块摘要。

**粒度 1 的 LLM 摘要 Prompt**（两处，均只输出短文本、不标精度；精度 / 引用 / phase_status 留给粒度 2 归组时统一产出）：

thinking（artifact=None）：

```
把下面这段 thinking 压缩成 1-2 句：只保留最终推理结论和关键决策点，丢弃中间推导、反复尝试和转折过程。直接输出摘要文本，不要 JSON。
```

tool_result（中等返回）：

```
把下面这段工具返回压缩成几句关键信息：只保留结果要点（状态、金额、ID 等关键字段），丢弃冗余 JSON 结构、日志噪声、列表详情。直接输出摘要文本，不要 JSON。
```

#### 粒度 2：多块压缩（操作同一产出物的 block 合并为一个摘要）

把操作同一产出物的 block 归为一组，调用 LLM 将每组总结为一个摘要块。归组只依赖一个条件——**产出物相同**（artifact 归一化后一致）；组内「哪些是冗余、哪些是被覆盖的旧版本」不再用版本号等规则前置判定，而是全部交给 LLM 在总结时识别并输出 `[OBSOLETE]` 标签。

**归组条件 — 产出物相同**：这些 block 操作的是同一个产出物（同一个文档）。

**最小规模门槛**：不是每个组都值得调 LLM——组内 block 数 < 2 或 token 总量低于阈值（如 < 500 token）的组直接跳过、原样保留，避免为「单块」或「两次独立小操作」浪费 LLM 调用。

**如何判断产出物相同**：block 创建时已打好 `artifact` 标签（见「artifact 打标签」），分组时对窗口内 block 做归一化后的 artifact 比较，相同者归入同一组，不要求物理相邻（并行 tool_call 会交错打断，但不影响归组）。

**分组流程**——不一次扫描整个会话，而是在单块压缩完成后、对窗口内 block 做一次归组：

```
窗口内 block 序列（已单块压缩、artifact 已打好标签）：

  b1  tool_call   read_file("schema.sql")    → "schema.sql"
  b2  tool_call   write_file("top50_v1.sql") → "top50_v1.sql"   ← top50 第 1 次
  b3  tool_result schema DDL                  → "schema.sql"
  b4  tool_call   bash("SELECT count(*)")     → None            ← 裸 SQL，无文档路径
  b5  tool_result top50_v1 written            → "top50_v1.sql"   ← top50 第 2 次（被 b3/b4 打断）
  b6  user        "改成按城市分组"             → None            ← 任务边界
  b7  tool_call   write_file("top50_v2.sql")  → "top50_v2.sql"   ← top50 第 3 次（跨 user 消息）
  b8  tool_result top50_v2 written            → "top50_v2.sql"

归一化后归组（不要求相邻）：

  Group "schema":      b1, b3
  Group "top50":       b2, b5, b7, b8        ← 三处 top50 被打散，仍归同一组
  (b4 裸 SQL → None、b6 user → None，跳过)
```

三处 top50 中间隔着 schema（b3）、裸 SQL（b4）、user 消息（b6），仍归入同一组：

- **不同 artifact 交错不影响**：b2 与 b5 中间隔着 b3/b4，因为归组只看 artifact 相同、不看物理相邻。
- **user 消息不切断分组**：b5 与 b7 中间隔着 user。user 只重置 `pending_artifact`（影响后面「0 个 tool_call」的 thinking/text），不改变 tool_call/tool_result 自己的 artifact，所以 b7 仍归一化成 `top50` 归入同组。
- **归一化**把 `top50_v1` / `top50_v2` 都归一成 `top50`，算同一产出物，一起交给 LLM 识别 v1 被 v2 覆盖输出 `[OBSOLETE]`。

**边界是窗口，不是连续**：归组在压缩窗口内进行，同一个 artifact 跨窗口不会归到同一组（例如 b7/b8 若出现在 100 轮后的下一个窗口，就自成一组 top50）。

**合并示例**：

```
合并前（7 个 block，~1200 tokens）：
  turn 7  assistant: write_file(top50_v1.sql)
  turn 8  tool:       file written           ← 被覆盖(v1 被 v3 取代)
  turn 9  assistant: join 写错了。write_file(top50_v2.sql)
  turn 10 tool:       file written           ← 被覆盖(v2 被 v3 取代)
  turn 11 assistant: bash("psql -f top50_v3.sql")
  turn 12 tool:       | 张三 | 158000 | 230单 |
                      | 李四 | 142000 | 198单 |  ← 50 行结果
  turn 13 assistant: 报表完毕，共 50 条，张三排第一。

合并后（3 个 block，~200 tokens）：
  [compressed A] turn 7-10:
    "Top50 脚本经历 v1(v2) → v3: v1 v2 因 join 错误废弃，最终用 v3"
  [compressed B] turn 11-12:
    "psql 执行 v3 成功，结果: 张三/158000/230单, 李四/142000/198单, ...(共50条)"
  [compressed C] turn 13:
    "报表完毕，共 50 条。张三排第一。"
```

**不归组的例子**（产出物不同）：

```
turn 1-5   产出物 = schema.sql / 数据分布查询    → 一个主题
turn 7-13  产出物 = top50.sql / 报表结果         → 另一个主题
这两个段产出物不同，不会跨段归组。各自内部产出物相同的 block 归组后，由 LLM 判断是否合并。
```

---

### 精度标签设计

压缩产出的每条信息必须标注精度，供 Agent 引用时判断可信度，也作为卸载评分函数的核心输入（见 10.5.3）。

| 标签 | 含义 | 示例 | 递归行为 | 卸载权重 |
|------|------|------|---------|---------|
| `[CONFIRMED]` | 用户明确确认或工具返回的确凿结果 | "用户确认预算上限 5000 元" | 不降级，一路透传 | 1.0（绝不卸载） |
| `[DERIVED]` | Agent 推理得出的结论，未被直接确认 | "根据消费习惯推测偏好 A 方案" | 未被后续验证的，在元摘要中丢弃 | 0.5（可卸载） |
| `[OBSOLETE]` | 已被后续操作覆盖的旧信息 | "v1 废弃，v3 已生效" | 丢弃，不进入元摘要 | 0.0（最优先卸载） |
| `[PENDING]` | 用户提了但还没处理的问题 | "是否包含上月对比尚未确认" | 已解决的移除，未解决的保留 | 0.9（高保留） |

Agent 在引用摘要内容时，看到 `[DERIVED]` 就知道需要验证而非直接当事实用。

> 注：`[OBSOLETE]` 是压缩**产出**的精度标签，由 LLM 在总结时标注。「被覆盖 / 已废弃」的判定完全来自 LLM——合并阶段不再有版本号等规则前置判断。

---

### 压缩 Prompt 模板

```
请对以下对话块做结构化压缩，输出 JSON：

要求：
1. 为每个信息点标注精度：CONFIRMED / DERIVED / PENDING / OBSOLETE
2. 工具返回只保留关键字段（金额、状态、ID），丢弃冗余 JSON
3. 无 text 的 thinking 块只保留推理结论与关键决策，压缩为 1-2 句摘要
4. 错误尝试只保留最终成功的方法，失败路径可丢弃，标记为 OBSOLETE
5. 如果这些块描述的任务阶段已达成或已放弃，标记 phase_status: "closed"；
   仍在进行中则标记 "ongoing"
6. 标注每个 key_fact 引用了哪些旧信息块（block_id），无引用则为空数组
7. 为这组块生成一个 5-10 字的主题词 title，用作卸载后的索引指针
8. 输出格式：
{
  "summary": "结构化摘要文本",
  "title": "几个字的主题词",
  "phase_status": "closed | ongoing",
  "key_facts": [
    {
      "fact": "...",
      "precision": "CONFIRMED",
      "source_round": 5,
      "references": ["block_id_xxx"]
    }
  ],
  "decisions": ["..."],
  "pending_items": ["..."],
  "compression_ratio": 0.15
}
```

**输出字段用途**：`phase_status` 用于辅助判断这些块是否可以安全卸载（closed 的块卸载权重更低）；`references` 用于维护跨块的引用关系图，支撑 10.5.3 的引用热度计算；`precision` 直接映射为卸载评分函数的精度权重；`title` 是这组块的主题词，卸载后作为窗口索引的指针 label，不进入正文。

#### 10.5.3 外部记忆卸载与召回

**做什么**：10.5.2 压缩后若上下文仍超 75% 窗口，进入卸载——把低留存价值的 InfoBlock 移出窗口存入外部存储，需要时按需召回。压缩和卸载是同一份 session 上下文上的两阶段流水线：压缩在原地缩短内容，卸载把数据移出窗口。

核心原则——"窗口里永远是最需要的信息，但需要的信息永远不会丢，只是不在窗口里，在外部记忆里等着被召回。"

---

### 卸载决策：retention_score 四维评分

每个 InfoBlock 独立计算 0~1 的留存价值分，按分排序，低分优先卸载。

```python
def retention_score(block: InfoBlock, current_turn: int) -> float:
    """
    返回 0~1:
      ≥ 0.60 → 保留在窗口
      0.30~0.60 → 卸载到向量库，窗口保留一行索引
      < 0.30 → 卸载，窗口不留（废弃数据直接丢弃）
    """

    # 1. 精度权重 (0.45) — 由 LLM 压缩时标注，最可靠
    precision_weight = {
        "CONFIRMED": 1.0,   # 用户确认的决策/结果，绝不卸载
        "PENDING":   0.9,   # 未解决的待办，高保留
        "DERIVED":   0.5,   # 推导出的中间结论，可卸载
        "OBSOLETE":  0.0,   # 已被覆盖，直接卸载
    }
    score = precision_weight[block.precision] * 0.45

    # 2. 引用热度 (0.30) — 越近被引用越热
    #    last_referenced_turn 由下方「引用次数如何计算」的语法层检测得到
    if block.last_referenced_turn < 0:
        turns_since_ref = 999                        # 从未被引用 → 热度 0
    else:
        turns_since_ref = max(0, current_turn - block.last_referenced_turn)
    heat = max(0.0, 1.0 - turns_since_ref / 10.0)    # 线性衰减，10 轮后归零
    score += heat * 0.30

    # 3. 一次性数据惩罚 (0.15)
    if block.is_one_shot and turns_since_ref > 2:
        score += 0.0 * 0.15       # 工具返回的冗余数据，2 轮内未被引用 → 卸
    elif block.is_one_shot:
        score += 0.3 * 0.15
    else:
        score += 1.0 * 0.15

    # 4. Token 成本 (0.10) — 越小越倾向于保留
    if block.token_count < 500:
        score += 1.0 * 0.10
    elif block.token_count < 2000:
        score += 0.5 * 0.10
    else:
        score += 0.1 * 0.10

    return score
```

四维中**精度权重最高 (0.45)**，因为它来自 LLM 对信息本身价值的判断，比规则更准。引用热度次之 (0.30)，反映实际使用模式。

#### 四维评分逐项原理与例子

> 说明：以下按**实际代码实现**（`server/engine/offload.py` 的 `retention_score`）描述。上文代码块除「引用热度」外，其余 3 维仍是设计初稿口径，与实现有出入，差异见本节末尾。

**1. 精度权重 (0.45) —— 最可靠的一维**

- **原理**：由 LLM 在压缩时给每块信息标注精度等级，反映信息本身的价值，比任何规则都准，所以权重最高。用户拍板确认的结论绝不卸载，被后续操作覆盖的旧信息直接淘汰。
- **分级（实现）**：CONFIRMED=1.0，DERIVED=0.6，PENDING=0.4，OBSOLETE=0.0（未知默认 0.3）。
- **例子**：工具返回「文件已成功创建」且用户确认「对，就用这个」→ CONFIRMED=1.0，几乎必保留；Agent 自己推导出的中间结论 → DERIVED=0.6；用户提了还没解决的问题 → PENDING=0.4；已被 v3 覆盖的 v1 方案 → OBSOLETE=0.0，直接卸载。

**2. 引用热度 (0.30) —— 反映实际使用模式**

- **原理**：越近被后续块引用越热，热度随时间线性衰减，10 轮没被引用就归零，从未被引用则为 0。权重第二高，因为它是真实使用模式的信号。
- **例子**：见下方「引用次数如何计算」的 4 块走查——A、B 因 `/src/utils.py` 在最新一轮仍被引用而热度 1.0；C（内容无 UUID/路径，提取不到标识符）、D（最新块没人引用它）热度 0。

##### 引用次数如何计算

`last_referenced_turn` 表示「该块最近一次被后续块引用的轮次」，初始为 -1（从未被引用）。

引用热度分两步得出：**先**由语法层确定 `last_referenced_turn`（本小节），**再**按 `heat = max(0, 1 − turns_since_ref/10)` 线性衰减换算成 0~1 的热度值，乘 0.30 权重（见上文四维评分代码）。

**判定规则（当前仅实现语法层）**

一次「引用」= 块 A 的 `extracted_ids` 与某个**更晚**的块 B 的 `extracted_ids` 存在交集（精确字符串匹配）：

1. `_extract_identifiers()` 用正则从文本提取稳定标识符，只认两类：
   - UUID：`[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}`
   - 文件路径：`/a/b.py`
2. `annotate_references()`（O(n²) 遍历窗口）对每个块，只在 `blocks[i+1:]` 里找交集，命中则 `last_referenced_turn = max(..., later.created_turn)`。
   - 只记**最新**引用轮次，不记次数；同轮被多个块引用只算一次。
   - 没提取到任何标识符的块永远保持 -1（热度恒 0）。
3. 引用方向单向：只能被**更晚**的块引用，早的块里出现晚的块的标识符不算。

**一个具体例子**

假设连续几轮产生 4 个 InfoBlock：

| 块 | created_turn | content（摘录） | `extracted_ids` |
|----|:---:|---|---|
| A | 1 | 工具返回「已创建 `/src/utils.py`，UUID `a1b2c3d4-...-f12`」 | `{"/src/utils.py", "a1b2c3d4-...-f12"}` |
| B | 2 | 助手「读取 `/src/utils.py`，修复了 bug」 | `{"/src/utils.py"}` |
| C | 3 | 助手「确定对外接口方案」 | `{}`（空） |
| D | 5 | 助手「重构 `/src/utils.py`」 | `{"/src/utils.py"}` |

`annotate_references()` 跑一遍：

- 块 A：后面 B(turn2)、D(turn5) 都含 `/src/utils.py` → `last_referenced_turn = max(2, 5) = 5`
- 块 B：后面 D(turn5) 含 `/src/utils.py` → `last_referenced_turn = 5`
- 块 C：`extracted_ids` 为空 → 无交集 → 保持 `-1`
- 块 D：是最后一块，无更晚的块 → `-1`

换算热度（`current_turn = 5`）：

| 块 | last_referenced_turn | gap | heat | 说明 |
|----|:---:|:---:|:---:|---|
| A | 5 | 0 | 1.0 | 刚被 D 引用，最热 |
| B | 5 | 0 | 1.0 | 同上 |
| C | -1 | — | 0.0 | 没提取到标识符，从未被引用 |
| D | -1 | — | 0.0 | 最新块，还没人引用它 |

这个例子暴露的三个坑：

1. **没标识符的块天然是冷的**：C 内容里没有 UUID/文件路径 → `extracted_ids` 空 → 永远 -1，热度恒 0，只能靠精度等其他维度保留。
2. **最新块天然是冷的**：D 刚产生，没有后续块引用它，heat=0，只能靠其他维度保留。
3. **同一路径反复出现 = 一路热**：`/src/utils.py` 从第 1 轮活跃到第 5 轮，A、B 一直被引用，热度恒 1.0，正符合「越近被引用越热」的设计意图。

**尚未实现的两层（设计预留）**

| 层 | 方法 | 状态 |
|---|------|------|
| 语法层 | 正则提取 ID → 精确字符串匹配 | ✅ 已实现 |
| 语义层 | embedding 余弦相似度（阈值 0.78） | ❌ 未实现 |
| LLM 推理 | 压缩 Prompt 的 `references` 字段 | ❌ 未实现（`references` 仅出现在 Prompt 输出 schema 示例，无代码消费） |

当前覆盖率约 60-70%（仅能捕获同一 UUID/文件路径在后续轮次再现的情况）。语义层与 LLM 层落地后可提升至 95%+。

**3. 一次性数据惩罚 (0.15) —— 用完即弃**

- **原理**：工具 dump 出来的冗余大结果属于一次性数据，Agent 取完关键结论后不会再回头看，占着窗口纯浪费，直接给 0 分；非一次性数据给 1 分。
- **例子**：某工具返回 8000 token 完整日志，Agent 只提取一行「错误码 404」进结论，日志块标 is_one_shot=True → 0 分，倾向卸载；持续在用的 `/src/utils.py` 内容块不是一次性 → 1 分。

**4. Token 成本 (0.10) —— 越小越值得留**

- **原理**：块越小，留在窗口里的代价越低，越倾向保留。按 token 分四档：≤200 得 1.0，≤2000 得 0.7，≤10000 得 0.4，超过 1 万得 0.1。
- **例子**：80 token 的结论块得 1.0（留）；5000 token 的工具结果得 0.4（可卸）；2 万 token 的 dump 得 0.1（强烈倾向卸）。

**与上文代码块的差异（其余 3 维仍未同步）**

| 维度 | 代码块（设计初稿） | 实际代码 |
|------|------------------|---------|
| 精度 | PENDING=0.9 / DERIVED=0.5 | DERIVED=0.6 / PENDING=0.4 |
| 一次性 | 依赖间隔轮次的三档 0 / 0.3 / 1.0 | 二值：is_one_shot→0，否则 1 |
| Token | 三档 <500 / <2000 / else | 四档 ≤200 / ≤2000 / ≤10000 / else |

### 卸载流程

```
上下文超 80% → 执行 10.5.2 压缩
  ↓
压缩后重新估算
  ├── ≤ 75% → 结束（压缩就够了）
  └── > 75% → 进入卸载:
        1. 对窗口内每个 block 跑 retention_score 排序
        2. 从低分到高分依次卸载，直到窗口降至 45% 以下
        3. 卸载的块写入向量库
        4. 窗口内为已卸载的块留一行索引
```

### 向量库写入规则

```
├── 向量库（pgvector）
│   └── 卸载的对话原文和摘要块
│       索引粒度：以 InfoBlock 为单位 + 关联的 artifact 标识
│       元信息：artifact、turn_range、precision、created_at
│       SQL: WHERE session_id=$1 AND embedding <=> $2 < 0.6
```

### 索引生成

卸载把内容移出窗口后，窗口只留一行指针。指针不是摘要的副本，而是一个更短的主题词 `label`——索引区由 `build_context()` 每轮从「已卸载清单」动态拼接，不落成固定记录。

**label 来源**（均不额外调 LLM）：

| 块的来源 | label 怎么来 | 例子 |
|---|---|---|
| 粒度 2 压缩块 | 压缩 Prompt 的 `title` 字段 | `"数据分布统计"` |
| 粒度 1 摘要块 | 摘要文本本身已够短，直接作 label | "users 表 3 字段，orders 4 字段" |
| 未压缩块 | 有 artifact → 归一化 artifact 名；无 artifact → 截正文前 N 字符或块类型 | `top50_v2.sql` → `top50` |

**登记**：卸载每个块时，除写入向量库外，还往 session 级 `offloaded_blocks` 登记一行指针元数据：

```sql
CREATE TABLE offloaded_blocks (
    session_id   UUID NOT NULL,
    block_id     UUID NOT NULL,
    turn         INT  NOT NULL,
    label        VARCHAR(100) NOT NULL,   -- 来自上表
    storage      VARCHAR(20)  NOT NULL,   -- pgvector
    offloaded_at TIMESTAMP DEFAULT now()
);
```

**拼接**：`build_context()` 每轮查清单拼成 `[可用外部记忆]` 区块，插在摘要层之后、滑动窗口原文之前：

```python
async def build_offload_index(session_id: UUID) -> str:
    rows = await db.fetch(
        """SELECT turn, label, storage FROM offloaded_blocks
           WHERE session_id = $1 ORDER BY turn LIMIT 100""",
        session_id
    )
    if not rows:
        return ""                      # 无卸载块 → 不插，避免空头
    lines = ["[可用外部记忆]"]
    for r in rows:
        lines.append(f"· {r['label']} (turn{r['turn']}) — {r['storage']}")
    return "\n".join(lines)
```

- **空则跳过**：无卸载块返回空串，不出现空的 `[可用外部记忆]` 头。
- **截断上限**：`LIMIT 100`，防止索引区自身膨胀。
- **顺序**：按 `turn` 升序，与窗口正文时间序一致。

---

### 卸载前后对比示例

**卸载前** — 上下文 ~8500 tokens，85%：

```
[system prompt]
  你是 iWork AI 助手...

  turn 1  user:   帮我分析 users 表和 orders 表的关系
  turn 2  assistant: read_file(schema.sql)
  turn 3  tool:   126 行 DDL ...        ← 已压缩为 2 行
  turn 4  assistant: users→orders 一对多
  turn 5  tool:   80 行查询统计 ...     ← 已压缩为 1 行
  turn 7  write_file(top50_v1.sql)      ← OBSOLETE
  turn 9  write_file(top50_v2.sql)      ← OBSOLETE
  turn 11 bash("psql -f top50_v3.sql")
  turn 12 tool:   50 行报表结果 ...     ← 已压缩为 2 行
  turn 13 assistant: 报表完毕，张三排第一
  turn 14 user:   导出成 CSV
  turn 15 bash("COPY ... TO CSV")
  turn 16 tool:   COPY 50 + 5KB 日志    ← 已压缩为 1 行
  turn 17 assistant: 已导出到 /tmp/top50.csv
  turn 18 user:   张三的退货率是多少     ← 当前轮
```

**评分排序**（低→高）：

```
  OBSOLETE + 冷           turn 7-10  v1 v2 废弃     → 0.05  ┐
  DERIVED  + 一次性 + 冷  turn 5    查询统计         → 0.28  │
  DERIVED  + 冷           turn 1    用户输入         → 0.32  │ 卸载线
  DERIVED  + 冷           turn 3    压缩 DDL         → 0.35  │ (目标45%)
  DERIVED  + 一次性 + 冷  turn 12   报表结果          → 0.30  │
  DERIVED  + 较冷         turn 13   报表完毕          → 0.33  │
  DERIVED  + 较冷         turn 14   导出 CSV         → 0.42  ┘
  DERIVED  + 温           turn 15   bash COPY        → 0.52  ← 保留
  DERIVED  + 温 + 一次性  turn 16   COPY 50          → 0.48  ← 保留
  DERIVED  + 温           turn 17   已导出            → 0.55  ← 保留
  当前轮                  turn 18   退货率            → 0.95  ← 保留
```

**卸载后** — ~1800 tokens，18%：

```
[system prompt]
  你是 iWork AI 助手...

[可用外部记忆]                              ← 新增索引区
  · DDL (turn3) — pgvector
  · 数据分布统计 (turn5) — pgvector
  · Top50 报表结果 (turn12) — pgvector
  · v1 v2 废弃代码 (turn7-10) — pgvector    ← OBSOLETE，可召回但大概率不需要
  · 早期对话 (turn1,13,14) — pgvector

[窗口正文]
  turn 15 assistant: bash("COPY ... TO CSV")
  turn 16 tool:      COPY 50
  turn 17 assistant: 已导出，50条记录
  turn 18 user:      张三的退货率是多少     ← 当前轮
```

---

### 召回机制

两种召回的本质区别在于**谁触发**：

| | 被动召回 | 主动召回 |
|---|---|---|
| 触发者 | 系统程序写死 | Agent 自己判断 |
| 触发时机 | 每轮 `build_context` 自动执行 | Agent 在推理过程中调用 `recall` 工具 |
| Agent 感知 | 无感知，结果静默注入上下文 | 有感知，Agent 主动决策"我需要查一下" |
| 适用场景 | 全覆盖兜底，确保不遗漏 | 针对性检索，Agent 知道自己缺什么 |

#### 被动召回（程序固定流程）

系统在每轮拼装上下文时无条件执行，Agent 不参与决策——检索本身不占 LLM 调用，只是向量库一次查询：

```
build_context() 流程中固化的步骤：
  1. 拿用户消息做 TF-IDF 快速检查 → 当前窗口已有答案？→ 跳过检索
  2. 拿用户消息 embedding → 查 pgvector（阈值 0.6，top 5）
  3. 合并检索结果，注入上下文（去重：窗口已有的不注入）
  4. Agent 收到上下文时已经包含了召回内容，对检索过程无感知
```

**召回去重**：已存在于当前窗口的 block 不重复注入。

**降级策略**：向量检索无结果时返回 `{"status": "searched_nothing_found"}`，明确告知 Agent 检索已完成但无结果，避免 Agent 误以为系统忘了检索。

#### 主动召回（Agent 调用 recall 工具）

Agent 在推理过程中发现自己缺上下文——比如生成报表时意识到不知道用户的格式偏好——主动调用 `recall` 工具去外部存储查询。这是 Agent 自己的判断，和调用 read_file、bash 一样，是一次工具调用：

```
Agent 推理过程中:
  "用户要我生成报表，但我不知道他偏好什么格式..."
  → 调用 recall(query="用户 报表 格式偏好", source="vector_db")
  → 系统查向量库，返回 "[CONFIRMED] 用户偏好 CSV 格式"
  → Agent 用这个信息继续生成，输出 CSV
```

`recall` 工具定义：

```json
{
  "name": "recall",
  "description": "从外部记忆存储中召回已卸载的历史信息。当你觉得当前上下文缺少某个信息时主动调用。",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "检索关键词，越具体越好。如 '张三 订单金额'、'报表 格式偏好'"
      },
      "source": {
        "type": "string",
        "enum": ["auto", "vector_db"],
        "description": "auto 自动选源；vector_db 语义检索",
        "default": "auto"
      }
    },
    "required": ["query"]
  }
}
```

两种召回互补——被动兜底确保不遗漏，主动提供 Agent 自主判断的灵活性。大部分情况下被动召回就够了，只有 Agent 在推理中明确意识到"我缺了 X"时才会主动调 `recall`。

---

### 实现要点

- **卸载时机**：10.5.2 压缩后仍超 75% 窗口时触发。
- **评分输入来源**：精度标记 (precision) 和引用关系 (references) 由图 10.5.2 的压缩 Prompt 产出，引用热度的详细检测机制见上文「引用次数如何计算」。
- **召回去重**：已存在于当前窗口的信息不再重复召回。
- **降级策略**：向量检索无结果时返回明确信号而非空，让 Agent 知道检索已完成。
- **会话结束**：卸载到 pgvector 的数据可选择性地写入第 5 章的记忆模块（MEMORY.md），作为长期记忆的素材。

#### 与压缩的关系总结

```
                    ┌─────────────┐
上下文超 80% 阈值 → │ 10.5.2 压缩  │ → 两个粒度：单块压缩 + 多块压缩
                    └──────┬──────┘
                           ↓ 估算 token
                    ┌─────────────┐
           仍超 75%?│ 10.5.3 卸载  │ → retention_score 排序 → 低分卸到外部存储
                    └─────────────┘

压缩不改窗口结构，只缩短内容原地保留；卸载把数据移出窗口，窗口只留索引。
压缩产出的 precision + references → 直接作为卸载评分的输入。
```

### 10.6 与 Query Loop 引擎的集成点

#### 上下文用量监控

在 Query Loop 的 Per-Message 循环中（参见 [1.3 节](#13-per-message-核心循环)），每次拼装上下文前检查 token 用量：

```python
# 在 build_context() 之前
usage_ratio = estimated_tokens / model_context_limit

if usage_ratio >= 0.80:
    # 第一阶段：压缩（10.5.2）
    blocks = compress_blocks(window_blocks)   # 双粒度：单块压缩 + 多块压缩
    usage_ratio = estimate(blocks) / model_context_limit

    if usage_ratio > 0.75:
        # 第二阶段：卸载（10.5.3）
        blocks = offload_low_score_blocks(blocks)  # retention_score 排序卸载
```

#### 压缩触发策略

| 触发类型 | 策略 | 参数 |
|---------|------|------|
| **被动触发（阈值）** | 达到 80% 窗口时自动压缩 | 压缩后保留 30%-50% token |
| **主动触发（轮次周期）** | 每 10-15 轮后周期性检查，重锚定目标 | 周期性重锚定，配合压缩后 token 估算决定是否进入卸载 |

任务类型影响阈值——调试任务设 90%+，简单问答可 50-60% 激进压缩。

#### 与记忆模块（第 5 章）的关系

本章与 [第 5 章 记忆模块](#5-记忆模块) 互补而非替代：

| 维度 | 记忆模块（第 5 章） | 上下文管理（本章） |
|------|-------------------|-------------------|
| 范围 | 跨 session 的长期信息 | 单个 session 内的窗口管理 |
| 存储 | MEMORY.md + Rules 表 | 向量库 |
| 时效 | 持久化，跨会话 | 会话级，会话结束可清理 |
| 触发 | 会话开始时注入 | 会话进行中实时触发 |

压缩后的摘要块和卸载的对话历史，在会话结束后可选择性地写入记忆模块，作为长期记忆的素材来源。

---

<a id="11-成本控制"></a>

## 11. 成本控制

成本控制不是把账单打给你看，而是让系统在预算内完成尽可能多的有效工作。本章把成本拆成「构成 → 杠杆 → 估算 → 记账 → 限额 → 降级」一条链，回答三个问题：**钱花在哪、怎么少花、花超了怎么办**。

### 11.1 问题定义

#### 成本从哪来

iWork Agent 的每一轮对话都是一次或多轮 LLM 调用，单次调用的成本公式：

```
单次 LLM 调用成本 = input_tokens × input单价 + output_tokens × output单价
                    + cache_read_tokens × cache_read单价 + cache_write_tokens × cache_write单价
```

单价见 `server/observability/pricing.json`，单位 美元 / 1K tokens。

成本不是线性增长的，存在四个放大器：

| 放大器 | 机制 | 示例 |
|--------|------|------|
| Turn 循环 | 单条消息内多次 LLM 调用，每次重复发送上下文前缀 | 10 步工具调用 ≈ 10 次 input 计费 |
| 失败重试 | 工具失败、content_filter、超时导致整轮重跑 | 一次失败 = 双倍成本 |
| RAG / 工具结果注入 | 检索文档、大文件工具结果直接进上下文 | 单次注入可达 5000+ token |
| 多 Agent 子会话 | task 工具生成子会话，父会话还要接收回传 | 成本叠加且常被忽视 |

#### 三个目标

1. **看得清**：每轮、每条消息、每个会话花多少钱，有账可查（记账引擎，11.4）。
2. **控得住**：设定额度，超限先告警、再降级、最后阻断（预算模型，11.5/11.6）。
3. **省得下**：用工程手段降低单轮成本，主要是压上下文、提缓存命中率（复用第 10 章杠杆）。

#### 现状与缺口

| 现状 | 缺口 |
|------|------|
| `pricing.json` 已定义 6 个模型单价 | 无代码加载它，美元成本未真正计算 |
| `agent_llm_cost_dollars_total` 指标已定义（`metrics.py:48`） | `query_loop.py` 只导入未 `.add()`，指标从未写入 |
| LLM 客户端已抽取 `input_tokens/output_tokens`（`client.py:102`） | `cache_read/cache_write` 未追踪（`events.py:188` 的 schema 已有字段） |
| `TokenCounter`（`token_counter.py`）可估算上下文 token 并校准 | 未与价格结合成成本估算器 |
| — | 预算、限额、降级机制完全空白 |

本章的设计补齐上述缺口，形成「估算 → 记账 → 限额 → 降级」的完整闭环。

---

### 11.2 成本构成与三大方向

#### 成本公式：量 × 单价

```
成本 = Σ(输入量 × 输入单价 + 输出量 × 输出单价)
```

输入再分两档：

| 档位 | 条件 | opus 单价（$/1K） |
|------|------|------------------|
| 缓存（cache_read） | 前缀命中缓存 | $0.0015（全价的 1/10） |
| 非缓存 | 每轮变化的部分 | $0.015（全价） |

所以省钱的抓手只有两个，正好对应公式的两个因子：

| 方向 | 省什么 | 手段 |
|------|--------|------|
| **方向一：降量** | 省被计费的 token 总数 | 压缩/卸载降输入、限输出、少调用、少子 Agent |
| **方向二：降单价** | 省每个 token 的价格 | 输入缓存（`cache_read`）、换便宜模型 |
| **方向三：免调用** | 省掉整次调用 | 结果缓存、规则替代、工具结果缓存、本地模型、检索优先 |

三个方向正交、可叠加——降量不改变单价，降单价不改变 token 数，正好各管公式的一个因子；免调用则更彻底，直接省掉一整轮的「量 × 单价」。

#### 方向一：降量（省 token 数）

对应公式里的"量"。输入量、输出量、调用次数，都是量；其中 input 占成本 70%~90%，压输入是降量的大头。

| 子策略 | 机制 | 影响 | 改动面 | 风险 |
|--------|------|------|--------|------|
| 压输入 | 上下文压缩/卸载（第 10 章）：旧历史压成摘要、低价值块卸载出窗口、RAG 注入前裁剪 | 高（input 占成本大头） | 中（第 10 章已实现，调阈值即可） | 低~中（摘要损失精度） |
| 压输出 | `thinking_budget` 限制 reasoning 输出、限制回复长度 | 中 | 极低（单参数） | 低 |
| 少调用 | turn 上限、失败重试限制 | 中（防失控 / 防翻倍） | 低 | 低 |
| 少子 Agent | 控制子会话数量与深度（第 9 章） | 中 | 高 | 中 |
| 增量复用（后续扩展） | 同一消息内重复片段去重复用 | 中 | 高 | 中 |
| 输出风格压缩（后续扩展） | 预定义输出模板/风格，限制冗余表达 | 低~中 | 低 | 低 |

#### 方向二：降单价（省每个 token 的价格）

对应公式里的"单价"。缓存和换模型都不减少 token 数，只降低每个 token 的价格。

| 子策略 | 机制 | 影响 | 改动面 | 风险 |
|--------|------|------|--------|------|
| 输入缓存 | 固定部分（核心约束/配置索引）前置，前缀稳定命中 `cache_read` | 高（input 单价 → 1/10） | 中（第 10.2 节布局已就绪） | 低 |
| 换模型 | 简单任务路由到便宜模型（opus vs haiku 单价差 15 倍） | 高 | 高（需路由/评测） | 高（质量不确定） |
| 批量 API 折扣（后续扩展） | 批量提交按批量价计费（约 5 折） | 高（仅批量场景） | 高（需批处理架构） | 中 |
| 环境分流（后续扩展） | 不同场景/环境路由到不同模型档位 | 中 | 低 | 低 |

几点注意：

1. 缓存与换模型**可叠加**——缓存把已发送前缀按 1/10 价计，换模型把整轮价格降到新模型档位，便宜模型 + 缓存命中 = 双降；
2. 换模型同时影响 input 与 output 单价，而 **output 单价是 input 的 5 倍**（$0.075 vs $0.015）——虽然输出量通常远小于输入，但换模型对 output 的省钱效果更显著；
3. 换模型收益大、风险高，本期只讨论不落地（见 11.7）。

#### 方向三：免调用（省掉整次调用）

这一方向的目标不是省 token 数、也不是降单价，而是**把整次 LLM 调用省掉**——输入输出都不产生，成本归零。它针对的是「非必需的模型调用」：能缓存、能算、能查的，就不该问模型。

| 子策略 | 机制 | 影响 | 改动面 | 风险 |
|--------|------|------|--------|------|
| 结果缓存 | 相同/相似请求命中历史结果，跳过 LLM 调用（详写 ↓） | 高（整次调用归零） | 中（缓存层 + 失效策略） | 中（语义误命中、时效性） |
| 规则替代 | 确定性逻辑（if/else、正则、查表、模板）替代模型判断（详写 ↓） | 高（零 token） | 低~中（逐点替换） | 低（逻辑正确即无损） |
| 工具结果缓存（后续扩展） | 幂等工具调用结果缓存，重跑前先查 | 中 | 中 | 低~中（时效性） |
| 本地小模型（后续扩展） | 简单任务用本地/开源模型，零 API 成本 | 中 | 高（部署 + 路由） | 中（质量不确定） |
| 检索优先（后续扩展） | 先检索知识库，命中即答、不调模型 | 中 | 高 | 中（检索质量决定） |

##### 结果缓存（免调用）

把「输入 → 输出」的映射存下来，命中时直接返回历史结果，跳过整次 LLM 调用。两种形态：

- **精确缓存**：key = 系统提示 + 消息摘要，完全相同的输入才命中——适合固定问法、幂等请求；
- **语义缓存**：用 embedding 相似度做模糊命中，表述不同但语义相同的输入可复用——需要向量库。

与输入缓存（`cache_read`）的本质区别：输入缓存只是把已发送前缀按 1/10 价计，**token 照发、照样计算**；结果缓存是**整次调用归零**——它是「免调用」，不是「降单价」。

三个难点：
1. **语义相似度阈值难定**——过高漏命中、过低误命中，直接决定收益与准确率的取舍；
2. **时效性**——缓存的结果会过期（如状态查询），需要 TTL 或失效策略，状态类查询慎用；
3. **安全**——不缓存含敏感信息的结果。

落地建议：本期不建语义缓存库，可先做**精确缓存 + TTL**，覆盖工具结果这类强幂等场景。

##### 规则替代（免调用）

凡是「输入可枚举、输出可确定」的逻辑，用代码/规则（if/else、正则、查表、公式、模板）替代模型判断——规则运行在本地，**零 token**。

判断标准一句话：输入封闭、输出确定 → 规则；输入开放、需要理解 → 模型。

典型场景：格式校验、字段映射、日期计算、单位换算、关键词/正则分流、模板渲染。已有先例：`compression_model = "claude-haiku-4-5"` 这类静态「任务 → 模型」映射，就是规则替代的雏形——把「简单任务用便宜模型」用确定的配置固定下来。

与结果缓存互补：**规则替代治「确定性」（能算就不问），结果缓存治「重复性」（问过就记住）**——两者都是免调用的确定性收益。

#### 举例：一次工具调用循环的计价拆解

上面三个方向，用一条真实场景算一笔账：用户发一条消息让 Agent 改导出格式，Agent 内部跑了 **5 次 LLM 调用**（初始 + 4 轮工具调用循环）才完成。下表是每次调用都要完整发送的上下文，按 [10.2 的布局](#102-上下文三分类处理总览) 拆开，单价取 `pricing.json` 的 claude-opus-4-7（单位 $/1K tokens）：

| 上下文部分 | token | 跨轮稳定性 | 计价档位 | opus 单价 |
|---|---|---|---|---|
| 核心约束（钉死） | 800 | 完全稳定 | cache_read | $0.0015 |
| 配置索引（Skill/Tool 列表） | 1,200 | 会话内稳定 | cache_read | $0.0015 |
| 选中 SKILL.md 全文 | 2,000 | 每任务变化（缓存断点后） | input | $0.015 |
| 摘要层（压缩旧历史） | 2,000 | 每轮更新 | input | $0.015 |
| 滑动窗口原文 | 3,000 | 每轮追加 | input | $0.015 |
| 当前用户输入 | 200 | 一次性 | input | $0.015 |
| 合计 | 9,200 | | | |

**计价区别**：同一份上下文里，**位置决定价格**——前 2,000 token（核心约束 + 配置索引）在缓存前缀内，按 `cache_read` 的 **1/10 价**（$0.0015）计；断点之后的 7,200 token 每轮都变，按 input **全价**（$0.015）计。另外 output 单价 $0.075 是 input 的 **5 倍**——虽然更贵，但输出总量通常远小于输入。

**重复计算**：这 9,200 token 不是花一次，而是**每次调用都完整重发一次**，近似下：

```
5 次调用 × 9,200 ≈ 46,000 input token 被计费
其中只有最后 200 token 是新信息，其余 9,000 token 都是旧内容被重复计价
```

这就是 input 占成本 70%~90% 的原因——**绝大部分 input 是"重复计算"**（实际每轮还会追加 tool_result，上下文持续增长，总量更高）。

那么缓存前缀省多少？对账如下：

| 调用 | 无缓存（全部按 input） | 有缓存（前 2,000 稳定前缀按 cache_read） |
|---|---|---|
| 第 1 次 | 9,200 × 0.015 = $0.138 | 9,200 × 0.015 = $0.138 |
| 第 2~5 次（×4） | 9,200 × 0.015 = $0.138 ×4 | (2,000 × 0.0015 + 7,200 × 0.015) = $0.111 ×4 |
| input 小计 | $0.690 | $0.582 |
| output（5 次 × 400 × 0.075） | $0.150 | $0.150 |
| 合计 | $0.840 | $0.732 |

input 部分从 $0.690 降到 $0.582，**省约 16%**；计入 output 后总成本省 **$0.108（约 13%）**。而这是单条消息——多轮会话里缓存前缀占比稳定、动态区持续增长，摊到整个 session，缓存布局的收益更大。

两个对照收尾：

1. **模型单价对照**：同样 46,000 input，若换 `deepseek-chat`（input $0.00014）只要 46,000 × 0.00014 / 1000 = **$0.0064**，与 opus 差两个数量级——这正是 11.7 模型路由讨论的起点。
2. **cache_write 说明**：`pricing.json` 没有写价字段；实际供应商（如 Anthropic）对首次写入缓存的前缀按 input 的 1.25× 一次性收费。本例简化成「第 1 次按全价、第 2 次起按 cache_read」，首轮写价对多轮循环摊薄后影响很小，略去。

#### 结论：三大方向行动纲领

1. **方向一（降量）先做满**——确定性收益：压缩/卸载阈值调激进 → 限输出 → 设 turn 上限与重试策略；
2. **方向二（降单价）中，缓存先做、换模型后评**——缓存前缀布局（第 10.2 节）已就绪，直接收益；换模型收益大、风险高，需评测护航（见 11.7）；
3. **方向三（免调用）先易后难**——规则替代先做（确定性、零风险、逐点替换），结果缓存后评（需缓存层与失效策略）；工具结果缓存、本地小模型、检索优先列入后续扩展；
4. **优先级理由**：降量省 token 总数、降单价省单价，都仍要发 token；**免调用直接省掉整轮**——规则替代收益最确定，故排进本期行动。

---

### 11.3 成本估算器（Cost Estimator）

**做什么**：在每次 LLM 调用前估算将要发生的 token 与成本，供预算检查（11.6）和前置裁剪决策使用。

#### 复用 TokenCounter

`server/engine/token_counter.py` 已提供启发式估算 + 真实 usage 校准：

- `estimate_context(messages, system_prompt, tools)` — 估算整次调用的输入 token；
- `calibrate(text, actual_tokens)` — 用真实 usage 做 EMA 校准（alpha=0.3）；
- 按 provider 区分 chars/token（deepseek 2.5，anthropic 3.5），默认 1.15 安全系数。

估算器在其上扩展两点：**输出 token 估算**（按 max_tokens / 任务类型给基线）与**价格计算**。

#### 三个前置估算点

| 时机 | 决策 | 说明 |
|------|------|------|
| RAG 文档注入前 | 估算 `extra_docs` token，超阈值则截断 / 只取摘要 | 避免注入即爆预算 |
| 子 Agent 生成前（第 9 章） | 估算子会话成本并计入父会话预算 | 子会话可被预算检查拦截 |
| 工具结果注入前 | 估算工具返回 token，超限则截断 | 大文件结果先裁剪再进上下文 |

#### 事后对账

每次 LLM 调用结束，用真实 usage 与估算值比对，差值回写 `TokenCounter.calibrate()` 并更新输出基线。对账规则：连续低估 → 调大输出基线；连续高估 → 调小安全系数。

---

### 11.4 记账引擎（Cost Ledger）

**做什么**：把每一笔 LLM 调用真实花费落账，补齐当前「只记 token、不记钱」的缺口。

#### 缺口补齐①：缓存 token 追踪

当前 `client.py` 只抽取 `input_tokens/output_tokens`。扩展为补充 `cache_read` / `cache_write` 等缓存字段的抽取。

缓存 token 直接关系到成本——cache_read 单价只有 input 的 1/10，**漏记缓存 = 低估省钱效果，也掩盖缓存布局问题**。

#### 缺口补齐②：美元成本计算

新增定价加载与计价模块：按模型从配置文件读取 $/1K tokens 单价，计算 input / output / cache_read / cache_write 分项美元成本。

修复 `query_loop.py` 中 `llm_cost` 未写入的缺口——每次 LLM 调用结束后，按 token 与缓存用量计算美元成本并写入 `llm_cost` 指标。

---

### 11.5 预算模型（Budget Model）

**做什么**：给「花超了」设上限。三层额度，从最接近单个任务的会话级到全局级，逐层兜底。

#### 三层额度

| 层级 | 作用域 | 典型额度 | 谁配置 |
|------|--------|---------|--------|
| 会话级（session） | 单次对话 | $0.5 / 会话 | 用户侧发起时 |
| 用户级（user） | 单用户月度 | $10 / 月 | 管理员 / 套餐 |
| 全局级（global） | 整个部署 | $100 / 月 | 管理员 |

**归属规则**：子会话（第 9 章 task 生成）成本计入**父会话所属用户**，不单独占子会话预算。

#### 软 / 硬限额

| 类型 | 触发 | 行为 | 可恢复 |
|------|------|------|--------|
| 软限额 | 已用 ≥ quota × soft_ratio | 推送 `budget.warning` 事件 + 用户侧提示 | — |
| 硬限额 | 已用 ≥ quota × hard_ratio | 新 turn 前调用 `budget_check()`，返回 block 则拒绝 | 需管理员加额 / 周期重置 |

---

### 11.6 超限行为：告警、降级、阻断

**做什么**：定义超限后的分级响应，尽量「降级可用」而非「一刀切阻断」。

#### 三级响应

| 级别 | 触发条件 | 动作 | 对用户体验 |
|------|---------|------|-----------|
| 1. 告警 | 软限额 | 记账时推送 warning，不干预 | 无感 |
| 2. 降级 | 接近硬限额 | 启用降级策略（见下） | 响应变慢 / 变简，但可用 |
| 3. 阻断 | 硬限额 | 拒绝新 turn，返回 `budget_exceeded` | 明确告知，提供加额入口 |

#### 降级策略清单

按「影响小 → 影响大」排序，进入降级时逐级启用：

| 策略 | 动作 | 影响 |
|------|------|------|
| D1 缩短 thinking_budget | `thinking_budget` 4096 → 1024 | 推理深度下降，成本立降 |
| D2 激进压缩上下文 | 调第 10 章阈值（如滑动窗口 10 → 5 轮、压缩阈值 80% → 60%） | 历史记忆变粗 |
| D3 禁用重工具 | 关闭耗 token 的工具（大文件读取 / RAG 检索） | 能力受限 |
| D4 换便宜模型 | 当前模型 → 同能力簇便宜档（如 opus → sonnet） | 质量下降，见 11.7 |

**调用点**：`query_loop.py` 每次 turn 开始、`build_context()` 之后、发送 LLM 请求之前，用 11.3 的估算器先估再查。action 为 `degrade` 时按 `degrade_lvl` 应用上表降级策略，`block` 时直接结束消息并返回错误码。

---

### 11.7 模型路由（讨论，不落地）

> 本节为讨论与建议，**不设计接口、不落地伪代码**，标注为后续扩展。

#### 收益

`pricing.json` 中 input 单价：opus $0.015 vs haiku $0.001，**差 15 倍**。若 70% 的简单任务能走便宜模型，整体成本可下降一个数量级。

#### 风险

- **质量不确定**：同任务换模型输出可能降质，需评测集兜底；
- **路由误判**：简单任务被判复杂、复杂任务被判简单，都会出错；
- **行为漂移**：用户对「为什么这次用便宜模型」感知不透明，体验不一致。

#### 推荐策略（渐进式）

1. **起步：调 `thinking_budget`**（D1）——不动模型选择，先压推理开销，零风险；
2. **静态路由**：按任务类型静态映射（简单问答 → 便宜模型），可配置、可回滚；
3. **动态路由**：结合估算器（11.3）+ 工具复杂度打分，复杂任务自动升级模型；
4. **评测护航**：每档路由配一个小评测集，观测质量指标，漂移即回滚。

> 结论：路由是成本控制的「最后一公里」，收益最大、风险最高，故本期只讨论不落地。先做满 11.3~11.6 的确定性收益，再评估路由。

---

### 11.8 与现有模块的关系

| 模块 | 关系 |
|------|------|
| [第 7 章 可观测性](#7-可观测性) | 复用 `pricing.json`、`message.usage/session.usage` 事件、`llm_token_usage` 指标；补齐 `llm_cost` 接线与 cache 字段 |
| [第 8 章 Hooks](#8-hooks-系统) | `cost-tracker.py` 示例（llm.after）从「记录」升级为「预算检查点」——在 hook 内调用 budget_check |
| [第 9 章 多 Agent 协作](#9-多-agent-协作) | 子会话成本归集到父会话用户；子 Agent 生成前用估算器预检 |
| [第 10 章 上下文管理](#10-上下文管理) | 成本的最大杠杆；降级策略 D2 直接调整第 10 章压缩 / 滑动窗口阈值 |

**依赖方向**：本章依赖第 7 章的记账事件、第 10 章的上下文控制能力，但不反向侵入它们——通过新增模块（`pricing.py` / `cost_estimator.py` / `budget.py`）对接，而非修改既有核心。

---

### 11.9 实现路径

分五个阶段，每阶段独立可交付、可回滚：

| 阶段 | 内容 | 交付物 | 依赖 |
|------|------|--------|------|
| **P0 记账补齐** | 缓存 token 追踪 + `pricing.py` 加载 + `llm_cost` 接线 + session_cost 落库 | 账目可信：能回答「这个会话花了多少钱」 | client.py、metrics.py、events.py |
| **P1 估算器** | `cost_estimator.py`，三个前置估算点接入 | 每次调用前可知「将要花多少」 | 复用 TokenCounter |
| **P2 预算限额** | 三层额度 + 配置接口 + `budget_check()` 接线 | 超预算可阻断 | P0、P1 |
| **P3 降级策略** | D1~D4 降级动作接入 engine | 超限降级可用而非硬断 | P2 |
| **P4 模型路由** | 按 11.7 建议评估（静态 → 动态） | 后续扩展，本期不做 | 评测体系 |

**验收标准**：一个真实会话跑完后，`session_cost` 记录的成本与 `pricing.json` × 实际 usage 手算一致；把预算设到极小值，能观察到 告警 → 降级 → 阻断 的完整链路。

---

<a id="12-agent-系统异常处理全景"></a>

## 12. Agent 系统异常处理全景

> **本章为讨论稿**：不新增接口、不落地伪代码，只从系统级视角分析"异常处理"这件事本身。与前面各章在模块内部定义异常处理（1.8 引擎、2.7 MCP、3.7 Skill、5.8 记忆、11.6 成本）不同，本章采用**两个叙事视角**组织：先看**外部资源环境**——Agent 依赖的一切外部资源可能怎么坏、怎么恢复；再看**内部循环系统**——故障如何在推理-行动-观察的循环里发生、传播、被消化；最后用一组**横切机制**把安全、成本、用户、可观测这些贯穿两边的维度收拢。

### 12.1 问题定义：Agent 异常处理与传统软件的本质差异

传统软件的异常处理建立在"程序是可预测的"这一前提上：错误来自代码缺陷或非法输入，可以用 try/catch、断言、返回值校验穷举式地捕获，处理逻辑由开发者写死。Agent 系统打破了所有前提——**执行主体是概率性的 LLM，错误来源是长尾，且 LLM 自己就是错误处理器**。两者是根本不同的范式，不是同一套方法论的应用。

| 维度 | 传统软件 | Agent 系统 |
|------|---------|-----------|
| **错误来源** | 代码缺陷 / 非法输入，**可枚举** | LLM 概率性输出 + 外部工具 + 环境 + 用户意图，**长尾不可穷举** |
| **错误检测** | try/catch、断言、返回值校验 | 需要"验证层"判断输出是否*真的*正确——可能"表面成功实则错误" |
| **处理主体** | 开发者写死的逻辑分支 | **LLM 本身是错误处理器**（错误注入上下文，让其自我修正） |
| **成功/失败边界** | 明确二分 | 模糊：部分成功、静默失败、幻觉、半成品 |
| **可复现性** | 确定性、可复现 | 非确定性，同输入可能不同输出 |
| **重试语义** | 通常安全（纯函数） | 有副作用（写文件、外部调用），重试需幂等考虑 |
| **失败成本** | 重试近乎免费 | 每次失败消耗 token / 金钱 / 时间 |
| **失败影响面** | 单次请求 | 长生命周期，中间状态多（文件改动、子 agent、上下文产物） |
| **用户角色** | 被动接收错误 | 人类在环（HITL），可参与错误决策 |

由此导出三条贯穿全章的推论：

1. **异常处理的第一现场从"代码层"上移到了"LLM 层"**：大部分错误不会以 exception 的形式出现，而是表现为 LLM 输出了一个"不太对"的下一个动作。检测要靠护栏与验证，而不是 try/catch。
2. **异常处理的核心矛盾是"成本 × 自主性 × 正确性"三角**：让 LLM 无限自愈最省人工但费钱且可能越修越错；过早交给用户打断流程但保证正确。任何策略都是在这个三角上取平衡。
3. **异常处理必须是"可观测的闭环"**：因为错误不可穷举、不可稳定复现，唯一可靠的改进途径是把每次异常变成数据（见 12.5.4），沉淀为回归集。

> **两条兜底原则**贯穿所有异常处理：**永不静默**——任何影响结果的异常都必须有用户可见的反馈（对照 1.8.2 的"不推送 system.status 的场景"清单逐一复核）；**明确"做不到"**——宁可清晰失败并给替代方案（如 1.8 #11 空响应回退为"抱歉，我暂时无法回答"），也不含糊地"尽力了"。

### 12.2 全景视角：外部资源环境 × 内部循环系统

本章用**两个叙事视角**而非分类来组织异常——它们不是互斥的两个集合，而是同一个系统从两个角度观察：

- **外部资源环境（静态地图）**：回答"故障从哪来"。按 Agent 依赖的外部对象（LLM 连接、内置工具、MCP、Skill、记忆库与外部系统、多 Agent）逐类列故障与恢复手段。
- **内部循环系统（动态过程）**：回答"故障如何发生、传播、被消化"。按推理-行动-观察（ReAct）三阶段及循环边界（轮次/超时/终止）逐环讲异常。

两者的**交汇点**是本系统异常体系的关键：**外部故障从"行动 Act"阶段进入循环，经"观察 Observe"阶段反馈给 LLM，再由循环治理兜底或升级**。

```
         ┌───────────── 外部资源环境（故障从哪来）─────────────┐
         │  LLM连接  工具  MCP  Skill  记忆/外部系统  多Agent   │
         └───────────────────────┬───────────────────────────┘
                                 │ 调用（连接类故障在此注入）
                                 ▼
        ┌───────────────────────────────────────────────────┐
        │            内部循环系统（推理-行动-观察）            │
        │                                                   │
        │  推理Reason ──→ 行动Act ──→ 观察Observe ──┐        │
        │    │ 语义类故障      │ 权限/执行/外部      │ 结果解析/ │
        │    │  (12.4.2)      │ 故障(12.4.3)      │ 注入/污染 │
        │    ▼                 ▼                  │ (12.4.4) │
        │  错误注入上下文 ◄──── 自愈(12.4.3) ───────┘         │
        │                                                   │
        │  循环治理：死循环/空转检测 · 轮次 · 超时 · 暂停/终止   │
        │            (12.4.5)                               │
        └───────────────────────┬───────────────────────────┘
                                │ 终止 / 升级
                                ▼
                用户介入(HITL) / 终止收尾（12.4.6 / 12.5.3）
```

**统一应对策略谱系**：1.8.2 的四级（重试 / 降级自愈 / 暂停 / 终止），加上前置的护栏拦截与低危的静默容忍，构成六级谱系，作为两个视角的共享词汇：

| 统一策略 | 性质 | 主要使用场景 |
|---------|------|-------------|
| **0. 静默容忍** | 低危，只记日志 | 外部：Skill 加载失败（3.7 S1-S6）、记忆工具拒绝（5.8） |
| **1. 护栏拦截** | **前置** | 横切：权限（1.6）、Hooks（8）、内容安全 |
| **2. 重试 / 重连** | 瞬时故障 | 外部：LLM 连接、MCP 重连（12.3.1/12.3.3） |
| **3. 降级 / 自愈** | 可恢复 | 内部：错误注入上下文，LLM 自适应（12.4.3） |
| **4. 暂停（HITL）** | 需人 | 横切：Plan/Build 确认、Client 回传（12.5.3） |
| **5. 终止** | 不可恢复 | 内部：循环治理上限、认证失败（12.4.5/12.4.6） |

**处置原则：硬错误 vs 软错误**：六级谱系回答"有哪些手段"，这里再给一条贯穿两个视角的分配原则——**确定性故障用确定性手段，概率性故障用概率性手段**（即"连接层故障 / 语义层故障"的通俗叫法）：

| | 硬错误 | 软错误 |
|---|---|---|
| **形态** | 真异常，有状态码 / exception 可捕 | 无异常，LLM 输出"不太对" |
| **来源** | 框架 / 外部资源：网络超时、断连、认证失败、工具执行失败、进程崩 | LLM 语义层：幻觉工具名、格式错误、跑题、结果错误 |
| **检测** | try/catch + 状态码 | 护栏拦截 + 输出验证 |
| **处理** | 固定代码逻辑（查表式决策） | LLM 自行诊断（注入上下文自愈） |
| **对应** | 12.3 外部资源环境（连接层为主） | 12.4 内部循环（Reason / Observe 阶段） |

**硬错误 → 固定逻辑**：状态码分传输层（HTTP 429/502/503）、协议层（MCP JSON-RPC error）、业务层（工具执行失败码）三层；固定逻辑按状态码查表决策（重试 / 重连 / 熔断 / 终止 / 转人工），不依赖 LLM 判断——这正是 12.5.4 说"四级策略是少数确定性可测试的部分、值得故障注入回归"的原因。关键点：**固定逻辑决定"怎么做"，不决定"下一步做什么"**——重试耗尽后仍要把错误文本注入上下文，把"换方案"交给 LLM（12.4.3 衔接点）。

**软错误 → LLM 诊断，但有两道护栏**：
1. **验证层**（12.4.2 正确性类）：LLM 无法自我诊断"结果错了"——工具"成功"但读到的是错误数据，它看不出来，需验证工具（编译 / 测试 / schema 校验）或 HITL；
2. **自愈边界**（12.4.5）：同一错误反复、越修越错、空转时判定自愈失败，升级回人工。

**边界划在哪**：不是所有异常都值得走上面两个分支——先问四个问题，回答"是"的归固定逻辑，全部"否"的才交给 LLM 诊断：

| 判定问题 | 是 → | 否 → |
|---|---|---|
| 错误有明确状态码 / 可枚举？ | 固定逻辑（查表决策） | 才考虑 LLM |
| 涉及权限 / 安全 / 内容？ | 固定逻辑（拦截或升级人） | 才考虑 LLM |
| 涉及不可逆副作用（写操作）？ | 禁止自动重试，固定逻辑 + 人工 | 才交给 LLM |
| 应对需要理解任务上下文？ | **才给 LLM** | 固定逻辑 |

核心启发式：**"错误是什么"是固定可枚举的，"下一步做什么"才可能是开放的**——前者归代码，后者才有资格归 LLM。例如"MCP 断连"是固定的（重连 ≤3 → 服务级终止，12.3.3），但"断连后换什么方案"可能需 LLM 理解任务才知道。归错边界最常见的代价：把免费判断变成付费判断、把确定行为变成不可复现行为、把安全拦截变成可被绕过的路径。

两条处置链路：

```
硬错误 → 状态码 → 固定逻辑（重试/重连/熔断/终止）→ 处理后衔接给 LLM 换方案
软错误 → LLM 自行诊断（注入上下文自愈）→ 验证层 + 自愈边界兜底 → 升级回用户
```

### 12.3 视角一：外部资源环境（故障从哪来）

每类资源统一按模板展开：**① 定位 ② 典型故障 ③ 已有处理（引用章节）④ 应对策略体现**。恢复手段的汇总矩阵见 12.3.7。

#### 12.3.1 LLM 连接层

- **定位**：外部 LLM API 的连通性（网络、鉴权、配额）。
- **典型故障**：网络超时、Rate Limit（429）、认证失败（401/403）、流中断、空响应（连接侧）、内容安全拦截。
- **已有处理**：1.8.3 指数退避重试（仅 429/502/503）、1.8.5 断流重连、1.8.2 认证失败升级终止。
- **应对**：策略 2 重试 / 策略 5 终止。**只讲连接与调用，LLM 输出的语义质量问题归 12.4.2**。

#### 12.3.2 内置工具与客户端工具

- **定位**：引擎内置工具（文件读写、bash 等）+ 前端执行的 Client 工具。
- **典型故障**：执行失败（shell 非零退出、文件权限不足）、执行超时、客户端回传超时、回传篡改（request_id 不匹配）。
- **已有处理**：1.8.1 #18-19、1.9.2 request_id 去重。
- **应对**：策略 3 自愈（stderr 注入上下文）+ 12.3.7 幂等重试矩阵。

#### 12.3.3 MCP 服务

- **定位**：外部 MCP server（Stdio / SSE / HTTP 三种传输）。
- **典型故障**：启动失败、握手超时、tools/list 失败、工具不存在、调用异常、调用超时、连接意外断开、JSON-RPC 协议错误。
- **已有处理**：2.7.1 17a~h 全场景、2.7.2 三级策略（重连 → 注入上下文 → 服务级终止）、2.7.4 实现审查（G1-G5 缺口）。
- **应对**：策略 2 重连 / 策略 3 自愈 / 策略 5 服务级终止（不影响引擎）。

#### 12.3.4 Skill

- **定位**：按需加载的能力包（Hub / Custom / Builtin）。
- **典型故障**：加载失败（Hub JSON 格式错、SKILL.md 缺失/过短、状态损坏、重名）、调用失败（skill 未安装、核心文件缺失）、安装态错误。
- **已有处理**：3.7 S1-S9 全表 + 安装态 HTTP 状态码。
- **应对**：策略 0 静默容忍（加载失败仅影响该 Skill 可见性）+ 策略 3 自愈（调用失败经 tool_result 返回）。

#### 12.3.5 记忆库与外部系统

- **定位**：记忆/规则库、PostgreSQL、外部 API，以及上下文组装依赖的文件与配置。
- **典型故障**：记忆写冷却/保护拒绝、索引缺失（5.8）；DB 断连/慢查询（1.8.3 重试）；@文件缺失/过大（1.8.1 #5）；上下文组装 token 超限的外部依赖侧（1.8.1 #6）。
- **已有处理**：5.8、1.8.3、1.8.1 #4-6。
- **应对**：策略 0/3（记忆类返回 skipped/rejected，不打断）+ 策略 2（DB 重连）+ 策略 3（token 压缩告知）。

#### 12.3.6 多 Agent（子 agent 作为外部执行单元）

- **定位**：主 agent 委派出去的独立 agentic loop（第 9 章，设计中）。
- **典型故障**：子 agent 超时 / 超 token / 异常退出、输出缺失 `<final_output>`、越权。
- **已有处理**：9.4.2 `<task_result status="error">` + partial_output 回传、9.4.3 安全边界、9.7 级联取消。
- **应对**：策略 3 自愈（父 agent 基于错误重试 / 继续 / 换方案）+ 部分失败语义（缺口见 12.6 第 4 类讨论）。

#### 12.3.7 外部资源的统一应对框架

把 12.3.1~12.3.6 的恢复手段收敛成矩阵：

| 资源 | 瞬时故障 → 重试/重连 | 永久故障 → 自愈/终止 |
|------|---------------------|---------------------|
| **LLM 连接** | 退避重试 ≤3 次、断流重连 | 认证失败 → 终止 |
| **内置/Client 工具** | 只读可重试；写工具看幂等 | 失败注入上下文，LLM 换方案 |
| **MCP** | 重连 ≤3 次，耗尽服务级终止 | 工具错误注入上下文 |
| **Skill** | 无网络，不重连 | 加载失败静默跳过、调用失败 tool_result |
| **记忆/外部系统** | DB 重连 | 记忆拒绝返回 skipped |
| **多 Agent** | 父 agent 重新委派 | 子 agent error 回传，父 agent 消化 |

**幂等重试矩阵**（决定"能不能自动重试"的核心规则）：

| | 瞬时故障（可重试） | 永久故障（不可重试） |
|---|------------------|---------------------|
| **只读操作** | read_file、grep、查询类 → 退避重试 | 目标不存在 → 注入上下文，LLM 换方案 |
| **写操作（幂等）** | 覆盖写、upsert → 可安全重试 | 同上 |
| **写操作（非幂等）** | **禁止自动重试**（重复副作用风险） | 只能人工决策 |

现有实现已隐含部分判断（2.7.1 17e"不重试（幂等风险）"、1.9.2 request_id 去重），但**缺少统一幂等标注**（工具级 `idempotent: true/false`），见 12.6 缺口 3。

### 12.4 视角二：内部循环系统（故障如何在循环中传播）

#### 12.4.1 循环机理总览：推理 → 行动 → 观察

per-message loop 的每个 turn 走完整 ReAct：**推理**（LLM 生成决策 / tool_use）→ **行动**（框架执行工具）→ **观察**（结果解析后注入上下文，进入下一轮）。循环有明确边界：消息轮次上限（1.3，25 轮）、单条消息总超时（300s）、队列上限（1.7，10 条）、状态机暂停/终止（1.2）。

| 阶段 | 产物 | 典型异常 | 去处 |
|------|------|---------|------|
| **推理 Reason** | thinking / text / tool_use | 幻觉工具名、格式错误、空响应、思考退化、脱离任务 | 12.4.2 |
| **行动 Act** | 工具执行 | 权限、超时、执行失败、**外部资源故障注入** | 12.4.3 |
| **观察 Observe** | tool_result 注入 | 结果解析失败、结果超长、上下文污染 | 12.4.4 |
| **循环边界** | 终止/暂停 | 死循环、空转、轮次/超时耗尽 | 12.4.5 |

#### 12.4.2 推理 Reason 阶段（LLM 语义层）

LLM 输出的"语义质量问题"——**try/catch 捕不到的隐形故障**，按检测难度从低到高：

| 类别 | 表现 | 已有处理 | 检测手段缺口 |
|------|------|---------|-------------|
| **结构/协议类** | tool_use input 非法 JSON、工具名捏造、空响应 | 1.8 #11/#12、1.8.3 校验函数 | 基本覆盖（schema 校验 + 错误注入） |
| **内容/安全类** | 内容安全拦截、PII 泄露、prompt injection | 1.8 #14、8.9.6（仅 PII） | prompt injection 检测缺失（12.5.1） |
| **行为/过程类** | 死循环、重复操作、脱离任务、未按 plan 执行 | 1.8.4 循环检测 | **只查"同工具同输入连续 3 次"**，广义"空转/跑题"无检测（12.4.5） |
| **正确性/语义类（最难）** | 工具"成功"但结果错误、断言假象、部分完成未告知、幻觉事实 | **基本空白** | 需验证工具（编译/测试）、结果 schema 校验、HITL 确认 |

检测手段只有三类，成本递增：

| 手段 | 能抓到 | 成本 |
|------|-------|------|
| **结构验证** | 非法 JSON、schema 不符 | 低，已有 |
| **护栏规则**（Hooks / 权限 / 重复检测） | 机械型异常（死循环、越权） | 中，规则需人工维护 |
| **验证工具 / HITL 确认** | 语义正确性 | **高**（多一轮验证或一次人工打断），当前最大空白 |

#### 12.4.3 行动 Act 阶段（工具调用，衔接外部资源）

**外部故障从这里进入循环**：工具执行异常（权限拒绝、超时、失败）发生后，错误文本被注入上下文（1.8.2 第 2 级）——这是**自愈的起点**，也是本系统区别于传统软件的核心：错误不被吞掉，而是成为 LLM 下一轮推理的输入。

- **权限拒绝**：1.6 前置检查拦截，错误注入上下文，LLM 尝试替代方案。
- **执行失败**：stderr/退出码注入上下文（含 MCP 错误的可读化，2.7.4 G4）。
- **客户端回传超时/篡改**：1.9.2 request_id 去重、超时注入 `client.tool_timeout`。

**自愈的优点与风险**（边界规则见 12.4.5）：不打断流程、成本低；但 LLM 会礼貌地无限重试、可能越修越错、同一失败不同轮次修复方向不同。

#### 12.4.4 观察 Observe 阶段（结果解析与注入）

工具结果回到循环时的异常：

| 场景 | 说明 |
|------|------|
| **结果解析失败** | 工具返回非预期结构，无法注入 → 错误注入，LLM 重试 |
| **结果超长** | 大文件/大输出挤占上下文 → 截断/摘要（第 10 章上下文管理） |
| **上下文污染** | 工具输出含恶意指令（prompt injection）或脏数据混入 → 12.5.1 |
| **注入顺序错误** | 并发工具结果回填顺序与 LLM 输出顺序不一致 → 误导推理（6.2） |
| **上下文组装失败** | build_context 阶段 token 超限强制压缩（1.8.1 #6）、@文件缺失（#5） |

观察阶段同时是**自愈反馈的载体**——错误注入的质量直接决定 LLM 能否正确修正，因此"错误文本要可读、要含足够失败上下文"（2.7.4 G4/G5 均是此问题）。

#### 12.4.5 循环治理：死循环与空转检测、轮次与超时、暂停与终止

循环层的"护栏"——防止循环本身失控：

| 机制 | 已有 | 缺口 |
|------|------|------|
| **死循环检测** | 1.8.4：同工具同输入连续 3 次 → 终止 | 只覆盖机械重复，不覆盖"空转/跑题"（LLM 反复尝试同一失败路径但输入微变） |
| **轮次上限** | 1.3：max_turns=25 → 强制终止 | 固定值，未与成本/进度联动 |
| **总超时** | 1.8.1 #23：单消息 300s → 终止 | 同上 |
| **暂停/终止状态机** | 1.2：PROCESSING / WAITING_SYNC / IDLE | 6.4：PROCESSING 中途终止的收尾未定义 |
| **自愈边界规则** | 无 | **建议补**：判定"越修越错 / 陷入空转"，停止自愈并升级人工 |

**自愈边界判定**（本系统最值得补的规则，待评估）——何时判定自愈失败、升级给用户：

- 同一错误类型累计 N 次（如连续 3 次同一工具报同一错误）——1.8.4 只覆盖"同工具同输入"，未覆盖"同类错误反复"；
- 修复动作本身引入了新错误（错误率上升）；
- 自愈消耗的 token / 轮次超过阈值（与 12.5.2 成本预算联动）；
- LLM 反复说"我再试一次"却无实质进展。

> **触发升级条件**：线上出现"LLM 反复尝试同一失败路径 5 次以上、用户全程等待"的可复现案例时，本问题提升至 P0。

#### 12.4.6 终止收尾与恢复（用户取消 vs 系统异常）

Agent 是长生命周期 + 大量中间状态的系统，终止/恢复比传统软件复杂得多：

| 路径 | 现状 | 缺口 |
|------|------|------|
| **重启恢复**（1.8.6） | `processing → pending` 重置，从头重跑 | 重跑可能重复副作用；**未区分用户取消与系统异常**，被取消消息可能被重跑 |
| **断线缓冲回放**（1.8.5） | StreamBuffer 断线缓冲，重连回放 | 终止时 buffer 冲刷还是丢弃未定义 |
| **四级终止**（1.8.2） | `message.error(fatal=true)`，引擎回 IDLE/归档 | 内存态（plan_confirmed、build_step）、上下文压缩产物、MCP 连接、子 agent 收尾规则空白（6.4） |
| **用户取消**（1.9.2 cancel） | 定义了 Plan/Build 语义 | 底层协程取消（CancelledError / TaskGroup）、资源释放、级联取消未实现 |

**核心建议（承接 6.4，待评估）**：

1. **用户取消 vs 系统异常两条路径**：cancel 落 `cancelled` 状态，恢复查询排除之，避免"用户取消的消息被当异常重跑"；
2. **统一取消协议**：用 `asyncio.TaskGroup` 把 LLM 流、sync_waiter、工具执行包进可取消作用域，取消点显式设计；
3. **终止收尾清单**：消息落 cancelled/error → 清 current_message_id → 内存态重置 → buffer 丢弃并推最终 chunk → 子 task 级联取消（9.7）；
4. **状态一致性**：上下文压缩/卸载产物按消息粒度原子提交，避免半写入索引；
5. **副作用告知**：已执行且不可回滚的操作（文件改动、外部调用）记入清单，最终 chunk 告知"已执行 X，未回滚"——**记录但不回滚**（回滚可能引入半回滚新风险）。

### 12.5 横切机制（贯穿两个视角）

#### 12.5.1 安全护栏

护栏是**前置的错误预防**（策略 1），自愈是**后置的错误消化**——可前置拦截的（危险命令、越权、PII）绝不放给自愈，因为自愈是概率性的，可能恰好选择绕过路径。

| 手段 | 现状 | 效果 |
|---------|------|------|
| **Hooks 责任链**（8 章） | 已有，tool.before/llm.before 可拦截 | 把"可预期的危险"挡在处理流程前 |
| **权限检查**（1.6） | 已有 | 越权在工具层即被拦 |
| **内容安全**（1.8 #14） | 已有 | 阻止违规输出 |
| **prompt injection 检测** | **缺失** | 被注入指令会让 LLM 偏离任务（12.4.2 行为/过程类的重要来源）——目前唯一未设防的主动攻击向量 |
| **工具目录精简**（6.1） | 问题已定义未解决 | 工具多 → 幻觉工具名多 → 错误多，精简即预防 |
| **schema 严格 + description 精炼**（3.4/2.5） | 已有 | 降低格式错误与参数错误 |

> **投入建议**：优先补"prompt injection 检测"和"6.1 工具目录精简"——前者是目前唯一未设防的主动攻击向量，后者是错误率的结构性来源。

#### 12.5.2 成本预算与熔断

**每次失败都花钱**：一次重试 = 一次完整 LLM 调用。错误处理策略本身就是成本预算的一部分，二者必须联动。

| 层级 | 现有机制 | 缺口 |
|------|---------|------|
| **重试预算** | 1.8.3 固定退避 3 次 | 次数固定，未与成本/时间预算绑定 |
| **降级** | 11.6 D1-D4（缩 thinking、压缩上下文、禁重工具、换模型） | D1-D4 只挂在成本超限触发，未与"错误频发"联动 |
| **熔断** | **无** | 连续失败无"暂停该路径"的熔断器（如：连续 N 次 MCP 调用失败 → 熔断该服务 → 快速失败） |

**建议的错误处理编排链（待评估）**——把策略 2~5 串成成本感知的流水线：

```
尝试 → 失败 → 可重试?(幂等+瞬时) → 是: 退避重试(≤3) → 仍失败
                                      └→ 否: 错误注入(自愈, 消耗轮次/预算)
                                              ↓ 超出预算或错误率
                                          熔断该路径(快速失败) → 升级: 暂停给用户 / 终止
```

要点：**自愈不是免费的**，每一轮自愈都在消耗"重试预算 + token 预算 + 时间预算"。建议把 1.8.3 的固定重试次数与 11.6 的预算模型合并成单一"错误预算"，按错误类型分级分配。

> **触发升级条件**：单条消息因错误处理（重试+自愈）额外消耗的 token 超过该消息正常消耗的 50%，且该比例持续上升，本问题提升至 P1。

#### 12.5.3 人类在环（HITL）

用户是错误处理资源的**最后一道闸**，也是代价最高的一道。已有基建：Plan 计划确认 + plan.question（1.5）、Build 逐步确认（1.6）、Client 工具回传（1.3）、1.8.2 暂停策略。

**何时升级给用户**（判定规则，待评估）：

| 条件 | 理由 |
|------|------|
| 自愈失败 / 反复空转（12.4.5） | 继续让 LLM 试是浪费成本 |
| 高代价或不可逆操作 | 删除文件、外部 API 写操作——确认一次远低于误操作损失 |
| 用户意图不确定 | LLM 多次澄清仍歧义（plan.question 已覆盖部分） |
| 错误影响面大 | 多文件改动、跨会话状态 |

**错误时的用户选择**：重试 / 跳过 / 修改 / 终止——Build 模式的 confirm/skip/abort（1.3）可作通用模板。**粒度权衡**：Build 每步确认最安全但最打断，Ask 全自动最流畅但错误代价高，中间态（仅高代价操作确认）值得评估（12.6 缺口 6）。

#### 12.5.4 可观测闭环（异常即数据）

传统软件靠"复现 bug"改进，Agent 系统的错误**不可穷举、不可稳定复现**，唯一可靠路径是**让每次异常成为数据，沉淀为回归集**。

**已有基础（第 7 章）**：trace_id 贯穿（7.2）、结构化日志（7.4）、错误指标（7.3 error counter）、审计（7.5）、会话回放（7.6.4）。

**缺的是"闭环"**，即从数据到改进的链路：

| 环节 | 现状 | 建议（待评估） |
|------|------|--------------|
| **错误聚合** | 结构化日志已有 | 按错误码 + 特征聚合，识别 Top 错误（LLM 幻觉工具名？MCP 断连？超时？） |
| **告警** | 7.3 已有 Grafana 规则 | 针对"错误率突增""自愈空转"设专项告警 |
| **故障注入 / chaos** | 无 | 模拟 LLM 乱输出、MCP 断连、超时、限流，验证 1.8 四级策略按设计生效 |
| **错误回归集** | 无 | 把线上真实错误（含完整 trace + 上下文）变成自动化测试用例，回归验证修复 |
| **会话回放** | 7.6.4 用于用户查看 | 同一能力用于排障：重放非确定性错误的完整链路 |

> 建议优先落地**故障注入测试**：四级策略（1.8.2）的每一级都是"代码路径"，是 Agent 系统里少数**确定性可测试**的部分——值得用确定性测试保护，而非等线上随机触发。

### 12.6 现状盘点与缺口清单

**已有（各章已定义）**：

| 能力 | 章节 |
|------|------|
| 引擎四级策略（重试/降级/暂停/终止）+ 异常全景图 25 项 | 1.8 |
| 重试与退避实现、流重连、空响应/格式校验、死循环检测、断线缓冲回放、重启恢复 | 1.8.3~1.8.6 |
| MCP 三级策略 + 17a~h 场景 + 实现审查 | 2.7 |
| Skill 加载/调用/安装态错误 | 3.7 |
| 记忆工具错误语义 | 5.8 |
| 成本超限分级响应（告警/降级/阻断） | 11.6 |
| 可观测性（trace/metrics/logging/audit/回放） | 7 |
| 护栏（Hooks 责任链、权限、内容安全） | 8、1.6 |

**缺口（待评估，对齐第 6 章格式）**：

| # | 缺口 | 当前状态 | 潜在解法 | 触发升级条件 |
|---|------|---------|---------|-------------|
| 1 | **LLM 语义/正确性错误检测**（12.4.2 第 4 类） | 只有结构层校验，语义层空白 | 验证工具（编译/测试）、结果 schema 校验、HITL 确认 | 出现"工具成功但结果错误未被发现"的可复现线上案例 |
| 2 | **统一错误码体系** | 各章零散（system.status code 散落 1.8.2/2.7.3） | 汇总为单一错误码表，聚合统计与告警复用 | 错误聚合统计需要跨模块比对时 |
| 3 | **幂等标注与副作用追踪**（12.3.7/12.4.6） | 6.4 已提出未落地 | 工具级 `idempotent` 标注 + request_id 去重 + 副作用清单 | 出现重复副作用事故（重复写文件/重复外部调用） |
| 4 | **熔断器**（12.5.2） | 只有逐次重试无熔断 | 连续失败 N 次 → 暂停该路径快速失败 | 同一 MCP/路径连续失败拖垮消息整体耗时的案例 |
| 5 | **错误回归测试 / 故障注入**（12.5.4） | 无 | chaos 测试 + 线上错误回归集 | 四级策略在线上出现未按设计生效的情况 |
| 6 | **自愈边界与升级人工的判定规则**（12.4.5/12.5.3） | 只有 1.8.4 死循环检测 | 自愈预算 + 空转判定 + HITL 升级规则 | LLM 反复尝试同一失败路径而用户全程等待的可复现案例 |
| 7 | **prompt injection 检测**（12.5.1） | 缺失 | 工具返回内容注入模式识别 | 出现工具输出内容改变 LLM 行为的案例 |

> **落地优先级建议**：缺口的 2（统一错误码）与 5（故障注入测试）成本低、收益确定，建议本次落地；4（熔断）与 3（幂等/副作用）跟随 6.4 的终止收尾一并做；1、6、7 属长期演进，先按"触发升级条件"观察。

---

<a id="13-权限控制"></a>

## 13. 权限控制

权限控制决定「Agent 能做什么、不能做什么」。它不是为了防住用户——用户是这台 Agent 的主人，天然是超级管理员；它是为了防住 **LLM 这个概率系统**：模型每次只基于上下文猜测该调什么工具，猜错时副作用却落在真实的文件系统、数据库和外部服务上。本章回答三个问题：**谁能做什么、怎样判定、被拒了怎么办**。

> **本章定位**：完整落地设计，与第 8 章 Hooks、第 7 章可观测性一样属横切机制，贯穿所有工具/资源执行路径。用户场景为单用户自用 Agent，不做正式 RBAC——用户 = 超级管理员，策略 = 能力清单 + 工具级 allow/deny。

---

### 13.1 问题定义

#### 为什么需要权限控制

iWork 是 Client-Server 架构，服务器能做的远比「聊几句」多：改文件、跑命令、装 MCP、改 rules/memories、派生子 Agent、联网。这些动作大多不可逆或有外部影响。如果只依赖 LLM 的"自觉"，就会出现三种典型事故：

| 事故类型 | 例子 | 根因 |
|----------|------|------|
| **误删/误改** | LLM 把 `rm -rf build/` 打成 `rm -rf /`、覆盖写源文件 | 工具输入由模型生成，一次幻觉即事故 |
| **越界访问** | 读/写工作区外的敏感文件（`.env`、`.ssh`） | 模型不感知"工作区边界"这个隐式约束 |
| **外部副作用** | 未经确认就安装 MCP、发包、发 Webhook、写 rules | 高代价动作没有人工闸门 |

权限控制把这三类事故从「事后发现」变成「事前拦截或确认」。

#### 三个目标

1. **看得清**：每条工具调用过了哪条策略、结果 ALLOW / DENY / CONFIRM，全程可审计（联动第 7 章）。
2. **拦得住**：危险动作与越界访问必须拦截；高代价动作升级人工确认。
3. **续得上**：被拒不是死路——注入错误上下文让 LLM 换方案（复用 1.3 现有行为），而不是整条消息中断。

#### 现状与缺口

| 现状 | 缺口 |
|------|------|
| Build 模式每个 tool_use 前调 `_check_tool_permission()` → `permission.check()`（1.3） | 仅 Build 模式生效；`permission.check` 无规则模型，Plan/Ask 不走检查 |
| Hooks 已有 `tool.before` 拦截 + deny-rm / workspace-guard 脚本示例（8.9） | 手写 shell 脚本，非结构化、不可查询、难维护 |
| MCP 配置按用户隔离（每用户 `mcp_servers.yaml`，2.4.3） | 无用户/会话级权限控制层，多租户隔离未专门设计 |
| 审计已含 `tool.permission_denied` 动作（7.5） | 无策略评估记录（哪条规则命中、为何判定） |
| 子 Agent 已有硬限制（9.5：不可调 task、文件限父 workspace） | 主 Agent 权限继承规则未定义，`plugin.json` 的 `permission` 字段为空 |
| 12.5.1 安全护栏已定义概念 | 无独立 Policy Engine；prompt injection 检测（12.6 #7）**不在本章范围**，仅附注 |

---

### 13.2 权限模型：主体 × 客体 × 动作

权限判定是对三元组 `(主体, 客体, 动作)` 的策略求值。三层都定义清楚，检查点才能收敛成一个统一入口。

#### 主体（Subject）三层

| 层 | 定义 | 权限来源 |
|----|------|----------|
| **用户** | 单用户场景下的唯一自然人 | 超级管理员，持有全局能力清单（13.3），可改任何规则 |
| **会话** | 一次对话（session） | 继承用户；可在会话内临时收紧/放宽（会话级覆盖，13.6） |
| **Agent** | 主 Agent 与子 Agent | 主 Agent 继承会话；子 Agent 继承父会话策略 + 9.5 硬限制（不可调 task、不可改 rules/memories、文件限父 workspace） |

继承方向：**用户 → 会话 → Agent，权限只减不加**。子 Agent 不可能获得比父会话更多的权限——这是 9.5 已定结论的正式化。

#### 客体（Object）四类

| 客体类型 | 涵盖 | 典型动作 |
|----------|------|----------|
| **工具** | 内置工具、客户端工具、MCP 工具、Skill 工具 | `call` |
| **资源** | 文件路径、工作区、数据库表、记忆条目 | `read` / `write` / `delete` |
| **系统动作** | 安装/卸载 MCP、安装 Skill、改 rules/memories、spawn 子 Agent | `install` / `configure` / `spawn` |
| **成本/网络** | 预算额度、出网请求、模型档位 | `consume` / `emit` |

#### 动作（Action）

一个有限的枚举，策略据此匹配：

```
call   read   write   delete   install   configure   spawn   consume   emit
```

> 例：`(用户, 工具: fs.write_file, call)` 命中「写入文件需确认」→ CONFIRM。

---

### 13.3 策略模型（Policy Engine）

#### 为什么独立成引擎

Hooks 是脚本化的、按 `tool.before`/`llm.before` 事件注册的自定义拦截（第 8 章），擅长"写一段逻辑做任何事"，但**不可查询、不可审计、无法在引擎内做确定性判定**。Policy Engine 相反：规则是结构化数据（YAML），判定是确定性逻辑，适合做"引擎内第一道闸门"；Hooks 降级为**自定义兜底**——结构化规则覆盖不了的特殊场景，留给用户手写脚本。

```
工具调用
   │
   ├─→ ① Policy Engine（结构化规则，确定性）   → ALLOW / DENY / CONFIRM
   │       未命中自定义规则？仍走默认策略
   │
   └─→ ② Hooks 兜底（tool.before，用户脚本）  → CONTINUE / DENY（8.3）
         两道闸门任一 DENY 即拒绝
```

#### 规则形态（`permissions.yaml`）

```yaml
# server/permissions/permissions.yaml —— 全局策略（按用户隔离：每用户一份）
version: 1
mode: default-allow          # default-allow | lockdown(default-deny)

capabilities:               # ① 能力清单：用户级全局开关
  filesystem: true          #   是否允许文件类工具
  network: true             #   是否允许联网
  shell: true               #   是否允许 shell 命令
  memory_write: true        #   是否允许改 rules/memories
  spawn_agent: true         #   是否允许派生子 Agent

tool_rules:                 # ② 工具级 allow/deny：工具名 + 输入条件
  - object: "tool:shell.exec"
    action: deny
    when: { command: "^(rm|sudo|chmod|shutdown).*" }      # deny 危险命令前缀
  - object: "tool:fs.write_file"
    action: deny
    when: { path: "(^|\\.)(env|pem|ssh/|config.json)$" }  # deny 敏感路径
  - object: "tool:mcp.*.install"
    action: confirm          # 安装 MCP 一律确认
  - object: "tool:memory.rules.update"
    action: confirm          # 改 rules 需确认

resource_guards:            # ③ 资源级守卫：工作区边界 / 绝对禁区
  - guard: workspace         # 工作区边界：`{workspace}/**` 内 read 放行
    scope: "{workspace}/**"
    allow: "read"
    action: confirm          # 越界写 → 确认；越界读 → 确认
  - guard: deny-list         # 绝对禁区：命中即 deny，优先级最高
    scope: ["**/.env", "**/.ssh/**", "**/node_modules/**"]
    action: deny

confirm_rules:              # ④ 动作级规则：高代价/不可逆 → CONFIRM
  - action: delete
    reason: "不可逆删除"
  - action: overwrite
    reason: "覆盖已有文件"
```

#### 三层分发（复用配置层级思想）

| 层 | 位置 | 生命周期 | 覆盖关系 |
|----|------|----------|----------|
| 全局 | 每用户 `permissions.yaml` | 持久 | 基线 |
| 会话级 | session 对象内存 | 会话结束即弃 | 在全局之上临时收紧/放宽（如"本会话禁止联网"） |
| 消息级 | 单条消息参数 | 消息结束即弃 | 最内层临时策略（如"这条消息只读"） |

复用 MCP 配置的 yaml→Session→Message 三层层级思想（2.4.2），不新增分发机制。

#### 默认策略

- **default-allow**（日常）：能力清单 + 工具规则挡掉已知危险，其余放行；高代价动作走 confirm_rules 升级人工确认。
- **lockdown**（default-deny）：除显式 allow 白名单外全部拒绝。适合执行敏感任务、或用户想全程盯着的时候一键切换。

---

### 13.4 决策流程（ALLOW、DENY、CONFIRM）

```
# server/permissions/engine.py —— evaluate() 核心
async def evaluate(session, subject, obj, action, ctx) -> PermissionDecision:
    # 1. 收集各层规则：消息级 > 会话级 > 全局（层内按 priority 降序）
    rules = collect_rules(session, message=ctx.message)

    # 2. 命中 explicit deny 或 deny-list → 立即 DENY
    if any(r.effect == DENY and r.matches(obj, action, ctx) for r in rules):
        return DENY("explicit_deny", matched_rules)

    # 3. 命中 explicit allow / confirm 规则 → 返回首个命中的 effect
    for r in rules:
        if r.matches(obj, action, ctx):
            return r.effect              # ALLOW / CONFIRM

    # 4. 未命中任何规则 → 默认策略
    if session.mode == LOCKDOWN:
        return DENY("lockdown_default")
    if action in HIGH_COST_ACTIONS:      # delete/overwrite/install/emit...
        return CONFIRM("high_cost_default")
    return ALLOW("default")
```

**判定优先级**：explicit deny > deny-list > explicit allow > confirm 规则 > 默认策略。

**CONFIRM 的短时记忆**：同一会话内，用户对同一 `(object, action)` 选择「总是允许/总是拒绝」后，写入会话级覆盖（13.6），该判定本会话内不再重复确认——避免高频操作打断节奏。

**DENY 的处理**：不中断整条消息，复用 1.3 的 `append_error_feedback()` 注入错误上下文，LLM 换方案继续（Build 模式现有行为，1.6）。全程写审计。

---

### 13.5 人工确认通道（客户端即时确认）

#### chunk 类型：`client.permission_request`

把 Build 模式的 confirm/skip/abort（1.3、1.4）**泛化为统一确认通道**，三种模式一致可用。新增 chunk：

```json
{
  "type": "client.permission_request",
  "request_id": "req_9f3a",
  "tool_name": "shell.exec",
  "input": { "command": "rm -rf build" },
  "matched_rule": "tool:shell.exec deny rm 前缀",
  "reason": "危险命令前缀 rm",
  "effect": "confirm"
}
```

客户端弹确认框，用户三选一：

| 选项 | 含义 | 引擎处理 |
|------|------|----------|
| confirm | 允许本次 | 继续执行，写审计 |
| skip | 跳过本次 | 注入错误上下文，LLM 换方案（等同 DENY 续路） |
| abort | 终止消息 | `terminal = True`，联动 1.8 / 12.4.6 收尾 |
| 记住选择 | 附属于 confirm/skip | 写入会话级覆盖（13.6），本会话不再重复确认 |

#### 触发条件

- 命中 confirm_rules（不可逆/高代价动作）；
- 命中资源守卫的 confirm 分支（越界读/写）；
- 成本阈值触顶（联动 11.6：预算告警时高危操作自动升级为确认）；
- 会话级 `require_confirm` 锁定（用户手动开启"每步确认"，等同 Build 模式全程）。

#### 同步等待

复用 1.3 的 `sync_waiter` 同步等待机制（超时 300s，超时按 skip 处理并写审计），不引入异步审批队列——单用户即时确认场景下异步队列是过度设计。

---

### 13.6 权限数据模型

#### 表：`permission_rules`（可管理、可查询）

```sql
CREATE TABLE permission_rules (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,          -- 所属用户（按用户隔离）
    scope       TEXT NOT NULL,            -- global | session:<id> | message
    object      TEXT NOT NULL,            -- "tool:shell.exec" | "fs" | "action:delete"
    action      TEXT NOT NULL,            -- call/read/write/delete/install/configure/spawn/consume/emit
    effect      TEXT NOT NULL CHECK (effect IN ('allow','deny','confirm')),
    priority    INT  NOT NULL DEFAULT 0,  -- 数字越大越优先
    when_json   JSONB,                    -- 输入条件（对应 YAML 的 when）
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

全局 `permissions.yaml` 在启动时导入为 `scope='global'` 的规则行，用户通过管理 API 增删改也落到本表——**单一事实来源**，YAML 只是便于手写的初始形态。

#### 评估记录（复用审计表，7.5）

不新增表。审计新增动作类型：

| action_type | 含义 |
|-------------|------|
| `permission.check` | 工具调用过权限（含结果 effect） |
| `permission.denied` | 命中 deny（含 matched_rule） |
| `permission.confirmed` | 用户确认/跳过（含选择） |

审计行带 `matched_rule` 与 `effect`，可回答"这条调用为什么被拒"。

#### 会话级覆盖（内存，不落库）

会话内「记住选择」与临时收紧/放宽存 session 对象内存，随会话销毁，避免权限残留。

---

### 13.7 引擎接线（集成点）

权限检查插在**工具执行的汇聚单点**，一处接入、全局生效：

| 集成点 | 位置 | 改动 |
|--------|------|------|
| `_execute_tool_chunk()` | 1.3（L358 调用处） | 所有工具执行统一入口，执行前统一 `evaluate()`；DENY/CONFIRM 在此分流 |
| `_check_tool_permission()` | 1.3（L447） | 由内部静态逻辑升级为调用 `permissions.evaluate()` |
| `ToolDispatcher.classify()` | 1.3（L425） | CLIENT/SERVER 分流点，可附加工具级策略 |
| `skill` 工具执行 | 3.6（L3618） | 按 skill 名鉴权（`tool:skill.<name>`） |
| `task` 工具委派 | 9.2（L7075） | spawn 子 Agent 前检查父会话 `spawn_agent` 能力 + `spawn` 动作权限 |
| 客户端工具下发 | 1.3 `client.tool_request` | 下发前已过服务端权限，客户端仅展示结果/确认框 |

**一句话**：所有工具调用进 `_execute_tool_chunk()` 一条管道，权限引擎是管道上的第一道闸门，Hooks 是第二道（兜底）。

---

### 13.8 与现有模块的关系

| 模块 | 关系 |
|------|------|
| [第 1 章 Query Loop 引擎](#1-query-loop-引擎) | 升级 `_check_tool_permission` 为 Policy Engine 调用；`client.permission_request` 并入 1.9 chunk 清单 |
| [第 7 章 可观测性](#7-可观测性) | 审计新增 `permission.check/denied/confirmed` 动作；指标 `agent_tool_permission_denied_total` 已有（7.3），补齐 `permission_confirm` 系列 |
| [第 8 章 Hooks](#8-hooks-系统) | 结构化策略优先；Hooks 降级为自定义兜底；`tool.before` 的 DENY 与引擎 DENY 汇入同一审计 |
| [第 9 章 多 Agent 协作](#9-多-agent-协作) | 子 Agent 继承父会话策略 + 9.5 硬限制；`task` 委派前鉴权 |
| [第 11 章 成本控制](#11-成本控制) | 成本阈值触发 CONFIRM（11.6 降级动作的补充）；`consume` 动作与预算检查联动 |
| [第 12 章 异常处理全景](#12-agent-系统异常处理全景) | 补齐 12.5.1 安全护栏的权限部分；prompt injection 检测（12.6 #7）不在本章范围，保留为缺口 |

**依赖方向**：本章依赖第 7 章审计、第 9 章会话归属，但以新增 `permissions/` 模块对接，不修改既有核心逻辑（1.3 的钩子只做升级调用）。

---

### 13.9 实现路径

| 阶段 | 内容 | 交付物 | 依赖 |
|------|------|--------|------|
| **P0 策略引擎** | `permissions/engine.py` + `permissions.yaml` 加载 + ALLOW/DENY/CONFIRM 判定管线 | 引擎可独立评估，规则可单测 | 无 |
| **P1 引擎接线** | `_check_tool_permission` 改调引擎；`_execute_tool_chunk` 统一接入 | 三种模式工具调用全部过权限 | P0 |
| **P2 确认通道** | `client.permission_request` chunk + confirm/skip/abort 泛化到三种模式 | 危险操作客户端即时确认 | P0、P1 |
| **P3 规则管理** | `permission_rules` 表 + 管理 API + 会话级覆盖 | 规则可查可改、「记住选择」可用 | P0 |
| **P4 客体扩展** | `task` 委派鉴权、skill 鉴权、资源级守卫 | 覆盖全部客体 | P0、P1 |

**验收标准**：
1. 默认配置下，`rm -rf /`、覆盖写 `.env`、安装 MCP 分别触发 DENY / DENY / CONFIRM；
2. 被拒后同一消息内 LLM 换用安全方案继续，不中断整条消息；
3. 审计可查到每条调用的 `effect` 与 `matched_rule`；
4. 切到 `lockdown`（default-deny）后，未列白名单的工具全部被拦；
5. 「记住选择」后同一会话内同判定不再重复确认。

---
