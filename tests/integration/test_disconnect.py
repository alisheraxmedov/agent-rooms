"""A client that drops a long poll must never consume messages (code review, Critical #1)."""

import json
from typing import Any

import anyio
import pytest
from rawhttp import raw_request

pytestmark = pytest.mark.integration

SETTLE_S = 0.4  # several poll intervals: an orphaned poller would have consumed the message by now
HOLD_S = 0.3


def http_get(path: str, authorization: str, port: int) -> bytes:
    return (
        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: {authorization}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()


def mcp_post(body: dict[str, Any], authorization: str, port: int) -> bytes:
    payload = json.dumps(body).encode()
    head = (
        f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: {authorization}\r\n"
        "Content-Type: application/json\r\nAccept: application/json, text/event-stream\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
    ).encode()
    return head + payload


async def room_with_two_members(call: Any) -> None:
    await call("claude", "create_room", slug="r1", topic="Pair work")
    await call("claude", "join_room", room="r1", role="writer")
    await call("codex", "join_room", room="r1", role="reviewer")


@pytest.mark.anyio
async def test_dropped_listen_next_does_not_swallow_the_wake(live_server: Any, call: Any) -> None:
    await room_with_two_members(call)
    await call("codex", "listen", room="r1")
    request = http_get(
        "/api/listen/next?timeout=30", live_server.headers("codex")["Authorization"], live_server.port
    )

    await raw_request(live_server.port, request, HOLD_S)
    await call("claude", "post", room="r1", text="are you there?")
    await anyio.sleep(SETTLE_S)

    assert [m["text"] for m in (await call("codex", "read", room="r1"))["messages"]] == ["are you there?"]


@pytest.mark.anyio
async def test_dropped_listen_next_keeps_the_agent_listening(live_server: Any, call: Any, rest: Any) -> None:
    await room_with_two_members(call)
    await call("codex", "listen", room="r1")
    request = http_get(
        "/api/listen/next?timeout=30", live_server.headers("codex")["Authorization"], live_server.port
    )

    await raw_request(live_server.port, request, HOLD_S)
    await call("claude", "post", room="r1", text="ping")
    await anyio.sleep(SETTLE_S)

    replacement = await rest("codex", "GET", "/api/listen/next", params={"timeout": 5})
    assert replacement.status_code == 200
    assert [m["text"] for m in replacement.json()["messages"]] == ["ping"]


@pytest.mark.anyio
async def test_dropped_raw_mcp_wait_does_not_swallow_the_message(live_server: Any, call: Any) -> None:
    await room_with_two_members(call)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "wait", "arguments": {"room": "r1", "timeout_s": 1}},
    }
    request = mcp_post(body, live_server.headers("codex")["Authorization"], live_server.port)

    await raw_request(live_server.port, request, HOLD_S)
    await call("claude", "post", room="r1", text="hello after the drop")
    await anyio.sleep(SETTLE_S)

    assert [m["text"] for m in (await call("codex", "read", room="r1"))["messages"]] == [
        "hello after the drop"
    ]


@pytest.mark.anyio
async def test_cancelled_mcp_client_wait_does_not_swallow_the_message(call: Any, open_mcp: Any) -> None:
    await room_with_two_members(call)

    async with open_mcp("codex") as client:
        with anyio.move_on_after(HOLD_S):
            await client.call_tool("wait", {"room": "r1", "timeout_s": 1})
    await call("claude", "post", room="r1", text="hello after the cancel")
    await anyio.sleep(SETTLE_S)

    assert [m["text"] for m in (await call("codex", "read", room="r1"))["messages"]] == [
        "hello after the cancel"
    ]
