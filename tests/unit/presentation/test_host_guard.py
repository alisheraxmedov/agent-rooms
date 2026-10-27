import json
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from agent_rooms.presentation.http.host_guard import HostGuardMiddleware

ALLOWED = ("127.0.0.1:*", "localhost:*", "rooms.internal:8765")


class Recorder:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.scopes: list[Scope] = []

    async def app(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"inner"})

    async def send(self, message: Message) -> None:
        self.sent.append(message)


async def receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def call(
    host: str | None, scope_type: str = "http", *, raw_hosts: list[bytes] | None = None
) -> Recorder:
    recorder = Recorder()
    middleware = HostGuardMiddleware(recorder.app, ALLOWED)
    headers = [(b"host", value) for value in raw_hosts or ([] if host is None else [host.encode()])]
    scope: dict[str, Any] = {"type": scope_type, "method": "GET", "path": "/x", "headers": headers}
    await middleware(scope, receive, recorder.send)
    return recorder


@pytest.mark.anyio
@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8765", "127.0.0.1:1", "localhost:65535", "LocalHost:8765", "rooms.internal:8765"],
)
async def test_allowed_hosts_pass_through(host: str) -> None:
    recorder = await call(host)
    assert recorder.sent[0]["status"] == 200
    assert len(recorder.scopes) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8765",
        "127.0.0.1",
        "localhost",
        "127.0.0.1.evil.example:8765",
        "localhost.evil.example:80",
        "127.0.0.1:80@evil.example",
        "127.0.0.1:abc",
        "127.0.0.1:",
        "127.0.0.1:123456",
        "rooms.internal:9999",
        "[::1]:8765",
        "",
    ],
)
async def test_other_hosts_are_421_with_a_json_body(host: str) -> None:
    recorder = await call(host)
    assert recorder.scopes == []
    assert recorder.sent[0]["status"] == 421
    body = json.loads(recorder.sent[1]["body"])
    assert body["error"] == "misdirected"
    assert body["message"]
    assert host not in body["message"] or host == ""


@pytest.mark.anyio
async def test_a_missing_host_header_is_421() -> None:
    recorder = await call(None)
    assert recorder.sent[0]["status"] == 421


@pytest.mark.anyio
async def test_duplicate_host_headers_are_421() -> None:
    recorder = await call(None, raw_hosts=[b"localhost:8765", b"localhost:8765"])
    assert recorder.sent[0]["status"] == 421


@pytest.mark.anyio
async def test_lifespan_scopes_are_not_checked() -> None:
    recorder = await call(None, "lifespan")
    assert len(recorder.scopes) == 1
