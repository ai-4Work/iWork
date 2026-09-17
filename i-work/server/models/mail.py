"""Session Mailbox 信封模型（ch9 §9.11.7）。

邮箱是**父子限定**的私有邮箱：发件人只往对方邮箱投递，收件人只从自己邮箱取信。
当前只有 lead ↔ member 一条通信轴，没有广播、没有 `list_agents` 注册表列举。

存储上不做新表——信封就是一行 `messages`，`msg_type` 决定它是不是一个"回合"：
可跑的（user/task/followup）会被 `dequeue_next` 取走跑一轮 LLM，不可跑的
（result/status）只作为数据喂给挂起的父协程。这个分流必须由**仓储层**做，
且 `dequeue_next` / `count_pending` / `list_pending` / `renumber_queue` 四处
过滤器必须完全一致——漏掉 `count_pending` 就会让信封吃掉 `max_queue_size`
预算，把合法用户消息挤成 429。
"""
from __future__ import annotations
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ── 信封类型 ──
MSG_USER = "user"              # 用户在客户端直接输入（不是信封，但共用 msg_type 列）
MSG_TASK = "task"              # 父→子：首次派生 + 初始任务
MSG_FOLLOWUP = "followup"      # 父→子：追加任务、唤醒新回合
MSG_RESULT = "result"          # 子→父：子回合终态的最终答案
MSG_STATUS = "status"          # 子→父：中途状态（预留，本轮不消费）

# 会被 dequeue_next 取走、开一轮 LLM 的类型
RUNNABLE_MSG_TYPES: tuple[str, ...] = (MSG_USER, MSG_TASK, MSG_FOLLOWUP)
# 只喂给挂起父协程、绝不触发回合的类型
RESULT_MSG_TYPES: tuple[str, ...] = (MSG_RESULT, MSG_STATUS)
# msg_type 全部合法取值
ALL_MSG_TYPES: tuple[str, ...] = (*RUNNABLE_MSG_TYPES, *RESULT_MSG_TYPES)

# 子 agent 全无文本产出时的占位（§9.11.9）。空串会让父把"没结果"读成"成功但空"。
NO_OUTPUT_PLACEHOLDER = "子任务无文本产出"

# 投递后要不要唤醒对方开新回合（§9.11.7 表）
TRIGGER_TURN: dict[str, bool] = {
    MSG_TASK: True,
    MSG_FOLLOWUP: True,
    MSG_RESULT: False,
    MSG_STATUS: False,
}


class MailMessage(BaseModel):
    """一个信封。`sender` / `recipient` 都是 agent_path（用户一侧记 "user"）。"""
    id: UUID = Field(default_factory=uuid4)
    sender: str                    # agent_path，或 "user"
    recipient: str                 # agent_path
    type: str                      # task | followup | result | status
    payload: dict = Field(default_factory=dict)   # 正文 / 结果 / 状态内容
    cid: UUID | None = None        # 关联 id：把 result 对回它那条 task
    trigger_turn: bool = False     # 投递后要不要唤醒对方开新回合


def agent_path_of(member_id: str) -> str:
    """扁平成员 id → agent 树路径（§9.11.4）。

    当前只有两层（顶层 /root + 一层成员），所以直接拼前缀。多层放开后这里要
    改成「父路径 + 成员 id」，但路径**门**（§9.11.7）已经按段数判定，不需要跟着改。
    """
    return f"/root/{member_id}"


def agent_id_from_path(path: str) -> str:
    """agent 树路径 → 扁平成员 id（展示层用；路径本身不进展示，§9.11.13）。"""
    return path.rstrip("/").rsplit("/", 1)[-1]
