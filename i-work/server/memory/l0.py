"""L0 读取与清洗（设计文档 L1-1.1 / L1-2.2 / L1-2.3）。

L0 是 conversation_history 表（LLM 可见的那条流），不是 messages 表（面向用户的
消息信封）。本模块只读，不改任何写入路径 —— 清洗放在读侧，零回归风险。

四步清洗：
1. 角色/类型过滤：只收 user / assistant；数组 content 只取 type="text" 片段
2. 文本清洗：剥注入的记忆标签、框架元数据、行首时间戳、媒体标记、base64、控制字符
3. 代码块剥离：仅 assistant 的围栏代码块整体删除
4. 结构过滤：空/纯空白、框架噪音、斜杠命令、纯符号 → 丢弃

再切两块：新消息（≤10，唯一抽取源）+ 背景消息（≤5，仅供上下文，严禁提取）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ROLES = ("user", "assistant")

# ── 第 2 步：文本清洗 ────────────────────────────────────────────

# 注入的记忆标签（召回侧自己拼进用户消息前缀的那类东西，不能再喂回抽取）
_INJECTED_TAG_RE = re.compile(
    r"<(relevant-memories|available_memories|user-persona|scene-navigation"
    r"|memory-tools-guide)>.*?</\1>",
    re.DOTALL,
)
# 行首时间戳：[2026-08-18T02:00:00.000Z] / 2026-08-18 02:00:00
_LEADING_TS_RE = re.compile(
    r"^\s*(?:\[)?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?(?:\])?\s*",
    re.MULTILINE,
)
_MEDIA_RE = re.compile(r"\[media attached:[^\]]*\]", re.IGNORECASE)
_SYSTEM_EXEC_RE = re.compile(
    r"^\s*System:\s*\[[^\]]*\]\s*Exec completed.*$", re.MULTILINE,
)
_DATA_URL_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ── 第 3 步：代码块剥离（仅 assistant）───────────────────────────

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_FENCE_LINE_RE = re.compile(r"^\s*```.*$", re.MULTILINE)

# ── 第 4 步：结构过滤 ────────────────────────────────────────────

_FRAMEWORK_NOISE_EXACT = ("no_reply",)
_FRAMEWORK_NOISE_PREFIX = (
    "[pre-compaction",
    "pre-compaction memory flush",
    "内存冲刷",
    "/new",
    "/reset",
)
# 无字母/数字/CJK（\w 在 Python 里含 CJK），即纯符号
_SYMBOL_ONLY_RE = re.compile(r"^[\W_]+$")
_PURE_QUESTION_RE = re.compile(r"^[?？\s]{1,5}$")


def text_of(content) -> str:
    """content 可能是 str，也可能是 OpenAI 数组格式（只取 type="text" 片段）。"""
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return str(content)


def clean_text(text: str, role: str) -> str:
    """四步清洗的第 2~3 步。纯函数，可单测。"""
    if not text:
        return ""
    text = _CTRL_RE.sub("", text)
    text = _INJECTED_TAG_RE.sub("", text)
    text = _SYSTEM_EXEC_RE.sub("", text)
    text = _MEDIA_RE.sub("", text)
    text = _DATA_URL_RE.sub("[image]", text)
    text = _BASE64_RE.sub("[image]", text)
    text = _LEADING_TS_RE.sub("", text)
    if role == "assistant":
        text = _FENCE_RE.sub("", text)
        text = _FENCE_LINE_RE.sub("", text)
    return text.strip()


def is_noise(text: str) -> bool:
    """第 4 步 + 质量门：结构噪音与纯符号。滤不掉语义上的闲聊（交给 LLM）。"""
    stripped = text.strip()
    if not stripped:
        return True
    low = stripped.lower()
    if low in _FRAMEWORK_NOISE_EXACT or low.startswith(_FRAMEWORK_NOISE_PREFIX):
        return True
    # 斜杠命令：整条就是一个命令
    if stripped.startswith("/") and "\n" not in stripped:
        return True
    if _PURE_QUESTION_RE.match(stripped):
        return True
    if _SYMBOL_ONLY_RE.match(stripped):
        return True
    return False


@dataclass
class L0Message:
    """清洗后的一条对话消息。id 缺失时用 seq<sequence> 兜底（稳定且可溯源）。"""

    id: str
    role: str
    content: str
    sequence: int
    timestamp: str | None = None

    def render(self) -> str:
        return f"[{self.id}] [{self.role}] [{self.timestamp or '未知时间'}]: {self.content}"


@dataclass
class L0Batch:
    """一次增量读取的产出。

    read_cursor 是游标推进目标：处理完这一批后应写到检查点。注意它按**读到的
    原始行**推进（含被过滤核掉的行），否则被过滤的行会让游标卡死、反复重抽。
    """

    new_messages: list[L0Message] = field(default_factory=list)
    background: list[L0Message] = field(default_factory=list)
    read_cursor: int = 0
    pending_total: int = 0
    pending_user: int = 0

    @property
    def has_new(self) -> bool:
        return bool(self.new_messages)


def to_message(row: dict) -> L0Message | None:
    """一行 conversation_history → L0Message；不合格返回 None。"""
    role = str(row.get("role") or "").lower()
    if role not in ROLES:
        return None
    text = clean_text(text_of(row.get("content")), role)
    if is_noise(text):
        return None
    seq = int(row.get("sequence") or 0)
    return L0Message(
        id=str(row.get("message_id") or f"seq{seq}"),
        role=role,
        content=text,
        sequence=seq,
        timestamp=row.get("created_at"),
    )


class L0Reader:
    """按游标增量读 L0 并切分成新消息 / 背景消息。

    history_repo 只需实现 list_rows(session_id)（Pg 与内存替身都有）。
    会话历史量级不大，这里一次读全量再在内存里切片；量级上去后可以改成
    "sequence > cursor LIMIT n" 的分页查询，接口不变。
    """

    def __init__(self, history_repo, *, batch_size: int = 10, background_size: int = 5):
        self._history = history_repo
        self._batch = batch_size
        self._background = background_size

    async def current_end(self, session_id) -> int:
        """会话当前最大 sequence（冷启动跳过存量用）。不取内容，也不受游标影响。"""
        rows = await self._history.list_rows(session_id)
        return max((int(r.get("sequence") or 0) for r in rows), default=0)

    async def read_batch(self, session_id, cursor: int = 0) -> L0Batch:
        rows = await self._history.list_rows(session_id)

        survivors: list[L0Message] = []
        before: list[L0Message] = []
        for row in rows:
            seq = int(row.get("sequence") or 0)
            msg = to_message(row)
            if seq <= cursor:
                if msg is not None:
                    before.append(msg)
            elif msg is not None:
                survivors.append(msg)

        new_messages = survivors[: self._batch]
        if len(survivors) > self._batch:
            # 只消化了最早一批：游标停在本批最后一条，后面的留给下一轮 sweep
            read_cursor = new_messages[-1].sequence
        else:
            # 全部消化：游标推到读过的最大原始行，跳过被过滤掉的行
            read_cursor = max(
                (int(r.get("sequence") or 0) for r in rows), default=cursor,
            )

        return L0Batch(
            new_messages=new_messages,
            background=before[-self._background:],
            read_cursor=max(read_cursor, cursor),
            pending_total=len(survivors),
            pending_user=sum(1 for m in survivors if m.role == "user"),
        )
