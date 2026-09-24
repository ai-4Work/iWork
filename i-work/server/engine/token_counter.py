"""Token estimation utility for context window management.

No external dependency — uses a chars/token heuristic calibrated against
actual usage data returned by the LLM API.
"""

from __future__ import annotations

import json as _json


class TokenCounter:
    """Heuristic token counter with real-usage calibration.

    Different models have different tokenization characteristics:
    - DeepSeek (Chinese-heavy): ~2.5 chars per token
    - Anthropic (English-heavy): ~3.5 chars per token

    These two are only the *initial* baseline. Once a model is known, the
    caller feeds the real ratio in via `use_model()` — the resolver computes
    it per model, so this counter must not be pinned to one vendor at
    construction time.

    A 15% safety margin is applied to avoid underestimation at API boundaries.
    """

    def __init__(self, provider: str = "deepseek") -> None:
        self._provider = provider
        self._chars_per_token = 2.5 if provider == "deepseek" else 3.5
        self._safety_margin = 1.15
        self._calibrated_ratio: float | None = None

    def use_model(self, chars_per_token: float) -> None:
        """Switch the baseline ratio to the current model's characteristic.

        Deliberately keeps `_calibrated_ratio`: that number came from real
        usage data and beats any constant, so switching models must not throw
        it away (switching back would otherwise lose the calibration).
        """
        if chars_per_token > 0:
            self._chars_per_token = chars_per_token

    @property
    def chars_per_token(self) -> float:
        """Return the calibrated ratio if available, otherwise the default."""
        return self._calibrated_ratio or self._chars_per_token

    def estimate(self, text: str) -> int:
        """Estimate token count for a single string."""
        if not text:
            return 0
        return max(1, int(len(text) / self.chars_per_token * self._safety_margin))

    def calibrate(self, text: str, actual_tokens: int) -> None:
        """Calibrate the chars/token ratio using actual API usage data.

        Called after each LLM call where real input_tokens is available.
        Uses an exponential moving average to smooth noise.
        """
        if actual_tokens <= 0 or not text:
            return
        sampled_ratio = len(text) / actual_tokens
        if self._calibrated_ratio is None:
            self._calibrated_ratio = sampled_ratio
        else:
            # EMA with alpha=0.3: recent data weighted 30%
            self._calibrated_ratio = 0.7 * self._calibrated_ratio + 0.3 * sampled_ratio

    def estimate_message(self, msg: dict) -> int:
        """Estimate token count for a single OpenAI-format message."""
        total = 0
        content = msg.get("content", "")
        if isinstance(content, str):
            total += self.estimate(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += self.estimate(part["text"])
        if msg.get("reasoning_content"):
            total += self.estimate(msg["reasoning_content"])
        if msg.get("tool_calls"):
            total += self.estimate(_json.dumps(msg["tool_calls"], ensure_ascii=False))
        if msg.get("tool_call_id"):
            total += self.estimate(msg["tool_call_id"])
        return max(1, total)

    def estimate_messages(self, messages: list[dict]) -> int:
        """Estimate total tokens for a list of messages."""
        return sum(self.estimate_message(m) for m in messages)

    def estimate_tool_defs(self, tools: list[dict] | None) -> int:
        """Estimate tokens consumed by tool definitions."""
        if not tools:
            return 0
        return self.estimate(_json.dumps(tools, ensure_ascii=False))

    def estimate_context(
        self,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> int:
        """Estimate total token count for a full LLM call context."""
        total = self.estimate(system_prompt)
        total += self.estimate_messages(messages)
        total += self.estimate_tool_defs(tools)
        return total

    def usage_ratio(
        self,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
        context_limit: int = 65536,
    ) -> float:
        """Return usage ratio (0.0-1.0) against the context limit."""
        estimated = self.estimate_context(messages, system_prompt, tools)
        return estimated / max(context_limit, 1)
