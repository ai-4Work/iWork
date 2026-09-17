#!/bin/bash
# hooks/deny-rm.sh — tool.before 拦截危险命令
set -euo pipefail

INPUT=$(cat)
TOOL=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['input']['tool_name'])" 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['input'].get('args',{}).get('command',''))" 2>/dev/null || echo "")

if [ "$TOOL" != "bash" ]; then
    echo '{"action":"CONTINUE"}'
    exit 0
fi

BLOCKED=("rm -rf /" "mkfs." "dd if=" "> /dev/sda" ":(){ :|:& };:" "chmod 777 /")

for pattern in "${BLOCKED[@]}"; do
    if [[ "$CMD" == *"$pattern"* ]]; then
        echo "{\"action\":\"STOP\",\"reason\":\"禁止执行危险命令: $CMD\"}"
        exit 0
    fi
done

echo '{"action":"CONTINUE"}'
