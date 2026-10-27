import anyio


async def raw_request(port: int, request: bytes, hold_s: float) -> bytes:
    """Send a raw HTTP request, hold the connection open for hold_s, then drop it. Returns what arrived."""
    received = b""
    async with await anyio.connect_tcp("127.0.0.1", port) as stream:
        await stream.send(request)
        with anyio.move_on_after(hold_s):
            received = await stream.receive(65536)
    return received
