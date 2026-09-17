from server.engine.stream_buffer import StreamBuffer


def test_push_and_drain_all():
    buf = StreamBuffer(max_size=10)
    buf.push({"seq": 1, "type": "agent.text", "delta": "hello"})
    buf.push({"seq": 2, "type": "agent.text", "delta": " world"})
    result = buf.drain()
    assert len(result) == 2


def test_drain_since_seq():
    buf = StreamBuffer(max_size=10)
    for i in range(5):
        buf.push({"seq": i, "type": "agent.text", "delta": str(i)})
    result = buf.drain(since_seq=2)
    assert len(result) == 2
    assert result[0]["seq"] == 3
    assert result[1]["seq"] == 4


def test_max_size_eviction():
    buf = StreamBuffer(max_size=3)
    for i in range(5):
        buf.push({"seq": i})
    assert len(buf) == 3
    result = buf.drain()
    assert result[0]["seq"] == 2
