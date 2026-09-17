"""父 → 子上下文 fork 与清洗（ch9 §9.11.5）。

现状是子 agent 只拿到一个 `prompt` 字符串，完全没有父的对话背景。fork 让子选择性
继承父历史，但**不原样搬运**——共三轮：切档（select）→ 清洗（clean）→ 落子历史。

**切档** `fork_turns`：
| 值 | 含义 |
|---|---|
| `none` | 全新上下文，只带 prompt（= 现状） |
| `all` | 继承父完整历史（清洗后） |
| `N` | 只继承父最近 N 轮 |

> 这里把「轮」定义成**一次用户请求**，用父历史行上 `_stamp()` 盖的 `message_id`
> 分组，而不是 `turn` 字段。原因：`turn` 是 per-message 从 0 重数的（`query_loop.py:1472`
> 盖 0、`:1544` 每轮递增），拿它当跨消息的轮次索引会连用户那句提问一起切掉。
> `message_id` 由同一个 `_stamp()` 盖，且天然一条消息 = 一轮。

**清洗**（§9.11.5）：
- **留** user 消息 + 每轮 assistant 的最终答案
- **丢** 工具行（`role="tool"`）与 assistant 的 `tool_calls` 块、reasoning 块
- **换** 父的 developer 指令 → 子自己的：父的指令块根本不落历史行（由
  `ContextManager.build` 从常量 + override 现拼），所以这里只需不搬运 system/developer 行，
  子引擎会各自 `_setup_expert_agent` 出自己的 system prompt。

**时机**：仅 `mode=spawn`。`followup` 沿用它自己已有的历史，不重新 fork。
"""
from __future__ import annotations

from uuid import UUID

from server.config import settings

FORK_NONE = "none"
FORK_ALL = "all"


def normalize_fork_turns(value: str | None, default: str | None = None) -> str:
    """把外部传进来的 fork_turns 规整成 `none` / `all` / `'<正整数>'`。

    取值来自 LLM 的工具入参或 agent `.md` frontmatter，都在边界上，所以非法值
    一律回落到默认档（§9.11.5 的全局 fallback），不让它渗进切档逻辑。
    """
    fallback = (default or settings.fork_turns_default).strip().lower()
    raw = (value or "").strip().lower()
    if raw in (FORK_NONE, FORK_ALL):
        return raw
    if raw.isdigit() and int(raw) > 0:
        return str(int(raw))
    return fallback


def _round_key(row: dict) -> str | None:
    return row.get("message_id")


def select_recent(rows: list[dict], fork_turns: str) -> list[dict]:
    """按 fork_turns 切档，返回父历史里该继承的那一段（尚未清洗）。"""
    if fork_turns == FORK_NONE:
        return []
    if fork_turns == FORK_ALL:
        return list(rows)

    n = int(fork_turns)
    keys: list[str | None] = []
    for row in rows:
        key = _round_key(row)
        if not keys or keys[-1] != key:
            keys.append(key)
    keep = set(keys[-n:])
    return [row for row in rows if _round_key(row) in keep]


def clean_rows(rows: list[dict]) -> list[dict]:
    """清洗继承段：只留 `{role, content}` 的对话骨架。

    assistant 行只保留正文——`tool_calls` / `reasoning_content` 一并丢掉，这正是
    §9.11.5「丢工具调用细节」的落点。副产品是父那条 **pending 的 task 调用**（它挂
    在最后一条 assistant 行的 `tool_calls` 里，还没写 tool_result）也一并消失，
    否则子会继承到「自己这次派发的句柄」。
    """
    out: list[dict] = []
    for row in rows:
        role = row.get("role")
        if role in ("tool", "system", "developer"):
            continue
        content = row.get("content") or ""
        if role == "assistant" and not content.strip():
            # 只有 tool_calls、没有正文的中间行：清掉工具块后就成了空行
            continue
        out.append({"role": role, "content": content})
    return out


async def fork_parent_history(
    *,
    parent_context_mgr,
    parent_session_id: UUID,
    child_context_mgr,
    child_session_id: UUID,
    fork_turns: str,
) -> int:
    """把父历史清洗后写进子会话，返回落了几行。

    落到子历史时**不盖 (message_id, turn) 印**（见 `append_prehistory`）：fork 段
    不属于子自己的任何一条消息，于是子对首条消息做 regenerate 不会把它截掉。
    """
    if fork_turns == FORK_NONE:
        return 0
    rows = await parent_context_mgr.list_rows(parent_session_id)
    kept = clean_rows(select_recent(rows, fork_turns))
    for row in kept:
        await child_context_mgr.append_prehistory(
            child_session_id, row["role"], row["content"],
        )
    return len(kept)
