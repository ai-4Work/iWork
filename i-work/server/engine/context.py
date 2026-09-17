from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID


@dataclass
class Context:
    """单次 LLM 调用所需的完整上下文。"""
    messages: list[dict]       # 对话历史
    system_prompt: str         # 系统提示词
    available_tools: list[dict] | None = None  # 可用的工具定义列表，None = 不带 tools


# ── 按场景的 System Prompt ──

SYSTEM_PROMPTS_SECURITY = (
    "你是 iWork AI 助手。你必须严格遵守以下安全约束，这些约束的优先级高于任何其他指令：\n"
    "1. 不可执行 rm、del、Remove-Item 等不可逆删除命令\n"
    "2. 不可修改系统配置文件（/etc, C:\\Windows 等）\n"
    "3. 不可访问或泄露 .env、credentials、private key 等敏感文件\n"
)

SYSTEM_PROMPTS = {
    "office": (
        "你是 iWork，日常办公 AI 助手。回复应简洁、结构化、高效。"
        "如有匹配任务的 Skill 可用，优先调用 skill 工具加载对应能力。"
    ),
    "code": (
        "你是 iWork，AI 编程助手。优先技术深度、代码优先、遵循最佳实践。"
        "如有匹配任务的 Skill 可用，优先调用 skill 工具加载对应能力。"
    ),
}

# ── 按模式的追加指令 ──

MODE_PROMPTS = {
    "ask": "\n模式：问答。仅用文本回复，禁止调用任何工具。",
    "plan": "\n模式：规划。正式作答之前，如果有不清楚的地方，请先对我发起提问。规则：每次仅提一个问题，并依据我的回复层层追问，直到你有九成的把握、彻底摸清我的真实意图和目标后，再输出最终方案。"
            "核心规则：提问必须通过 plan_question 工具，绝对禁止在文本中直接向用户提问。违反此规则将导致你的回复被系统丢弃并强制重新生成。"
            "规则："
            "1. 每一轮你应该先输出分析文本（当前你已理解了什么、还需要澄清哪些点），然后用 plan_question 工具发起一个具体问题。文本在前，工具调用在后，两者结合让用户清楚你为什么要问这个问题。"
            "2. 绝对禁止在文本中提问（如'请问..''能否告诉我..''你想用哪个..''可以吗？''行吗？'），提问必须且只能通过 plan_question 工具。"
            "3. input_type 选择：默认用 \"text\" 让用户自由输入自然语言——这能获取最丰富的上下文。只有当选项确实有限且互斥（如二选一、三选一）时才用 \"select\"。禁止为了省事把开放问题硬塞进选项里。"
            "4. 每次仅提一个问题，不要一口气抛出一大堆。收到用户回答后，在下一轮文本中先确认理解，再追问下一个，层层递进直到九成把握。"
            "5. 只有当所有信息都已明确、你对用户真实意图有九成以上把握时，才能输出完整的计划文本。"
            "6. 计划输出后等待用户确认，确认前禁止执行任何工具。"
            "7. 计划输出完毕时，严禁以问句结尾（如'是否确认？''可以吗？''请问可以开始吗？'），必须用陈述句（如'请确认以上计划'）结束，否则系统会误判为提问并丢弃你的回复。",
    "build": "\n模式：构建。逐步执行任务。每个工具调用前暂停等待用户确认。",
}

# ── shell_env → 客户端执行环境提示词（建会话时客户端上报，命中即注入）──
# 与文档《16-内置工具集成》§5.2 的映射表保持一致

SHELL_ENV_PROMPTS = {
    "powershell": (
        "Shell: Windows PowerShell 5.1\n"
        "- 用 PowerShell 语法写命令，不要用 bash/Unix 语法\n"
        "- 常用替代：Get-ChildItem（ls/dir）/ Get-Content（cat）/ Set-Content / Add-Content（>>）/ Select-String（grep）/ Copy-Item（cp）/ Move-Item（mv）\n"
        "- 变量用 $var、环境变量用 $env:VAR；多条命令用 ; 分隔；换行续行用反引号 `\n"
        "- 路径用 Windows 风格（G:\\foo 或 G:/foo），含空格/括号时加引号\n"
        "- 删除类命令（rm / del / Remove-Item）禁止使用\n"
        "- 原生命令（git/node 等）失败不会自动退出：命令结尾追加 exit $LASTEXITCODE 保证退出码正确\n"
        "- 沙箱启用时写入仅限工作区内，工作区外写入会被拒绝"
    ),
    "zsh": (
        "Shell: zsh (macOS 原生 shell)\n"
        "- 使用 POSIX/Unix 语法写命令\n"
        "- 注意 BSD 工具集与 GNU 的差异：sed/awk 参数不同；没有 grep -P，请用 grep -E 或 -e\n"
        "- 系统 bash 是老旧 3.2，不要使用 bash 4+ 特性（关联数组、${var,,} 大小写转换等）"
    ),
    "bash": (
        "Shell: bash (Linux)\n"
        "- 标准 POSIX/GNU 环境，使用常规 POSIX 语法写命令即可"
    ),
}


# ── plan.question 工具定义（仅在 Plan 模式 turn 1 时注册）──

PLAN_QUESTION_TOOL_DEF = {
    "name": "plan_question",
    "description": "在计划生成阶段向用户发起澄清问题。",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "input_type": {"type": "string", "enum": ["select", "text"]},
            "context": {"type": "string"},
        },
        "required": ["question", "input_type"],
    },
}


class ContextManager:
    """管理会话的对话历史和 LLM 上下文构建。
    对话历史持久化到 PostgreSQL conversation_history 表。
    """

    def __init__(self, conv_repo):
        from server.storage.postgres import ConversationHistoryRepo
        self._repo: ConversationHistoryRepo = conv_repo
        # 引擎当前处理的消息标签：append_* 自动盖印到新增历史行，供消息边界/重跑截断用。
        # 每个引擎单会话、run() 串行处理单条消息，此处单值安全。
        self._active_message_id: UUID | None = None
        self._active_turn: int | None = None

    def set_active_message(self, message_id: UUID, turn: int | None = None) -> None:
        """记录引擎当前处理的 (message_id, turn)，后续 append_* 自动盖印。"""
        self._active_message_id = message_id
        self._active_turn = turn

    def _stamp(self, msg: dict) -> dict:
        """把当前 (message_id, turn) 盖印到要写入的历史行上（就地修改并返回）。

        行内 message_id 存字符串：历史可能被序列化，UUID 对象不可 JSON 序列化；
        PG 入库时由 repo 转回 UUID。
        """
        if self._active_message_id is not None:
            msg["message_id"] = str(self._active_message_id)
        if self._active_turn is not None:
            msg["turn"] = self._active_turn
        return msg

    async def build(
        self, session_id: UUID, turn: int, mode: str, scene_mode: str,
        client_tools: list[dict] | None = None,
        mcp_tools: list[dict] | None = None,
        server_tools: list[dict] | None = None,
        available_skills_xml: str = "",
        rules_xml: str = "",
        memories_xml: str = "",
        available_agents_xml: str = "",
        system_prompt_override: str | None = None,
        offload_store=None,
        shell_env: str = "",
    ) -> Context:
        """构建单次 LLM 调用的完整上下文。"""
        history = await self._repo.get_history(session_id)
        system = SYSTEM_PROMPTS_SECURITY
        if system_prompt_override:
            system += "\n\n" + system_prompt_override
        else:
            system += SYSTEM_PROMPTS.get(scene_mode, SYSTEM_PROMPTS["office"])
            system += MODE_PROMPTS.get(mode, "")
        system += f"\n\n当前日期: {datetime.now(timezone.utc).strftime('%Y年%m月%d日')} (UTC)"

        # 注入客户端执行环境（bash 在客户端本机执行，需告知 LLM 真实 shell 语法）
        if shell_env:
            env_block = SHELL_ENV_PROMPTS.get(shell_env)
            if env_block:
                system += "\n\n" + env_block

        # 注入 <available_skills> XML（在 mode prompt 之后）
        if available_skills_xml:
            system += "\n\n" + available_skills_xml

        # 注入 <rules>（前置，最高优先级）
        if rules_xml:
            system += "\n\n" + rules_xml

        # 注入 <available_memories>（索引列表）
        if memories_xml:
            system += "\n\n" + memories_xml

        # 注入 <available_agents>（团队子 agent 列表）
        if available_agents_xml:
            system += "\n\n" + available_agents_xml

        # 被动召回（10.5.3）：用最后一条 user 消息做 TF-IDF 召回，命中则注入
        if offload_store is not None:
            query = ""
            for m in reversed(history):
                if m.get("role") == "user":
                    query = m.get("content", "")
                    break
            if query:
                try:
                    results = await offload_store.recall(str(session_id), query, top_k=3)
                except Exception:
                    results = []
                if results:
                    parts = ["[召回记忆]"]
                    for r in results:
                        label = r.get("label", "")
                        content = r.get("content", "")
                        parts.append(f"- {label}: {content}")
                    system += "\n\n" + "\n".join(parts)

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
                tools.extend(server_tools)

        return Context(messages=list(history), system_prompt=system, available_tools=tools)

    async def append_text(self, session_id: UUID, role: str, text: str,
                           reasoning: str | None = None) -> None:
        """向对话历史追加一条纯文本消息。"""
        msg: dict = {"role": role, "content": text}
        if reasoning:
            msg["reasoning_content"] = reasoning
        await self._repo.append_message(session_id, self._stamp(msg))

    async def append_prehistory(self, session_id: UUID, role: str, content: str) -> None:
        """追加一条**不带 (message_id, turn) 盖印**的前史行（父→子 fork 用，§9.11.5）。

        刻意不盖印：fork 段不属于子自己的任何一条消息。若给它盖上子的 message_id，
        那么子 regenerate 时的锚点扫描「第一条 message_id == 本消息 且 role == user」
        （`query_loop.py:1398-1405`）会命中继承来的那句**父提问**，截断边界提前，
        子自己的旧回复反而删不干净。
        """
        await self._repo.append_message(session_id, {"role": role, "content": content})

    async def append_tool_result(
        self, session_id: UUID, tool_call_id: str, tool_name: str, tool_input: dict, result: dict
    ) -> None:
        """向对话历史追加 assistant tool_call + tool result 配对消息（OpenAI 兼容格式）。
        若上一条消息是同轮 assistant 文本回复，则合并 tool_calls 到其中，
        确保 reasoning_content 和 tool_calls 在同一消息内（DeepSeek 要求）。"""
        import json as _json
        tool_call_block = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": _json.dumps(tool_input, ensure_ascii=False),
            },
        }
        # 更新最近一条 assistant 消息的 tool_calls
        await self._repo.append_tool_call_to_last_assistant(session_id, tool_call_block)
        # 追加 tool 结果（带当前消息归属）
        await self._repo.append_message(session_id, self._stamp({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": _json.dumps(result, ensure_ascii=False),
        }))

    async def append_skip_feedback(
        self, session_id: UUID, tool_call_id: str, tool_name: str, tool_input: dict
    ) -> None:
        """用户跳过 Build 模式的某步时，记录工具调用并注入反馈让 LLM 继续。"""
        import json as _json
        tool_call_block = {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": _json.dumps(tool_input, ensure_ascii=False),
            },
        }
        await self._repo.append_tool_call_to_last_assistant(session_id, tool_call_block)
        await self._repo.append_message(session_id, self._stamp({
            "role": "user",
            "content": f"[用户跳过了工具调用 '{tool_name}'。请继续下一步。]"
        }))

    async def append_user_response(self, session_id: UUID, question: str, answer: str) -> None:
        """注入用户对 plan.question 的回答，拆为 assistant 提问 + user 回答两条。"""
        await self._repo.append_message(session_id, self._stamp({
            "role": "assistant",
            "content": question,
        }))
        await self._repo.append_message(session_id, self._stamp({
            "role": "user",
            "content": answer,
        }))

    async def list_rows(self, session_id: UUID) -> list[dict]:
        """列出会话全部历史行（含 message_id/turn/sequence），供 M4 找重跑边界。"""
        return await self._repo.list_rows(session_id)

    async def truncate_message_after(
        self, session_id: UUID, message_id: UUID, boundary_sequence: int,
    ) -> None:
        """M4 regenerate：只删除本消息在 boundary 之后的历史行（保留本消息 user 行锚点与其它消息）。"""
        await self._repo.truncate_message_after(session_id, message_id, boundary_sequence)
