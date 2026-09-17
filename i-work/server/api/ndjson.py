import json
from collections.abc import AsyncIterator
from fastapi.responses import StreamingResponse


class NDJSONStream:
    """构建 NDJSON 流式响应的辅助工具。每行一个完整 JSON，'\n' 分隔。"""

    @staticmethod
    def response(events: AsyncIterator[dict]) -> StreamingResponse:
        async def generate():
            async for event in events:
                yield json.dumps(event, default=str) + "\n"

        return StreamingResponse(
            generate(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
            },
        )
