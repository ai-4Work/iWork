from __future__ import annotations
from collections import deque


class StreamBuffer:
    """环形缓冲区：客户端断线期间缓冲数据块，重连后回放。
    上限 500 条，超出时丢弃最旧的数据块。
    """

    def __init__(self, max_size: int = 500):
        self._buffer: deque[dict] = deque(maxlen=max_size)

    def push(self, chunk: dict) -> None:
        """写入一个数据块。所有引擎发出的 _push_chunk 都会同时写入此缓冲。"""
        self._buffer.append(chunk)

    def drain(self, since_seq: int | None = None) -> list[dict]:
        """客户端重连时回放 since_seq 之后的数据块。
        since_seq=None 时返回全部缓冲。
        """
        if since_seq is None:
            return list(self._buffer)
        return [c for c in self._buffer if c.get("seq", 0) > since_seq]

    def __len__(self) -> int:
        return len(self._buffer)
