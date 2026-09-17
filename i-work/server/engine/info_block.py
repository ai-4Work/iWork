"""InfoBlock — the fundamental unit of context compression and offloading."""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum


class ContentType(str, Enum):
    TOOL_RESULT = "tool_result"
    ASSISTANT_TEXT = "assistant_text"
    USER_INPUT = "user_input"
    SUMMARY = "summary"
    THINKING = "thinking"


class Precision(str, Enum):
    """LLM-assigned confidence label for each piece of information.

    CONFIRMED: user explicitly confirmed or tool returned conclusive result.
    DERIVED:   agent-reasoned conclusion, not directly confirmed.
    OBSOLETE:  superseded by a later operation (e.g. v1 replaced by v3).
    PENDING:   user raised question not yet resolved.
    """
    CONFIRMED = "CONFIRMED"
    DERIVED = "DERIVED"
    OBSOLETE = "OBSOLETE"
    PENDING = "PENDING"


@dataclass
class InfoBlock:
    """A single chunk of information in the conversation context.

    Each block is the atomic unit for compression, offloading, and recall.
    """

    block_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
    artifact: str = ""                    # normalized artifact name, "" if none
    title: str = ""                       # 5-10 char topic label, used as offload index
    content: str = ""
    content_type: ContentType = ContentType.ASSISTANT_TEXT
    precision: Precision = Precision.DERIVED
    created_turn: int = 0
    last_referenced_turn: int = -1        # -1 means never referenced
    token_count: int = 0
    tool_name: str | None = None
    is_one_shot: bool = False
    extracted_ids: list[str] = field(default_factory=list)
    compressed: bool = False              # True after granularity-1 compression applied
