#!/usr/bin/env python3
"""hooks/cost-tracker.py — llm.after 记录 Token 用量"""
import json
import sys
from datetime import datetime

data = json.load(sys.stdin)
tokens = data["input"]["tokens"]
total_tokens = tokens.get("input", 0) + tokens.get("output", 0)

record = {
    "timestamp": datetime.now().isoformat(),
    "session_id": data["session_id"],
    "turn": data["turn"],
    "stop_reason": data["input"]["stop_reason"],
    "tokens_in": tokens.get("input", 0),
    "tokens_out": tokens.get("output", 0),
    "total_tokens": total_tokens,
}

print(json.dumps(record, ensure_ascii=False))
print(json.dumps({"action": "CONTINUE"}))
