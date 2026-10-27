import json
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from agent_rooms.presentation.http.auth import BearerAuthMiddleware
from agent_rooms.presentation.identity import agent_of

TOKENS = {
    "claude-token-0123456789-abcdef": "claude",
    "human-token-0123456789-abcdef": "human",
}


class FakeRegistry:
    def agent_for(self, token: str) -> str | None:
        return TOKENS.get(token)


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

    @property
    def status(self) -> int:
        return int(self.sent[0]["status"])

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode().lower(): v.decode() for k, v in self.sent[0]["headers"]}

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")


async def receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def call(
    path: str,
    *,
    method: str = "GET",
    authorization: str | None = None,
    token: str | None = None,
    raw_headers: list[tuple[bytes, bytes]] | None = None,
) -> Recorder:
    recorder = Recorder()
    middleware = BearerAuthMiddleware(recorder.app, FakeRegistry())
    headers = list(raw_headers or [])
    if token is not None:
        authorization = f"Bearer {token}"
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope: dict[str, Any] = {"type": "http", "method": method, "path": path, "headers": headers}
    await middleware(scope, receive, recorder.send)
    return recorder


UNAUTHORIZED = {"error": "unauthorized", "message": "Missing or invalid bearer token."}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/"), ("HEAD", "/"), ("GET", "/healthz"), ("HEAD", "/healthz")]
)
async def test_public_paths_need_no_token(method: str, path: str) -> None:
    recorder = await call(path, method=method)
    assert recorder.status == 200
    assert recorder.body == b"inner"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), [("POST", "/"), ("POST", "/healthz"), ("DELETE", "/healthz")])
async def test_public_exemption_is_limited_to_get_and_head(method: str, path: str) -> None:
    recorder = await call(path, method=method)
    assert recorder.status == 401


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/rooms", "/api/listen/next", "/mcp", "/other"])
async def test_missing_token_is_401_with_challenge(path: str) -> None:
    recorder = await call(path)
    assert recorder.status == 401
    assert json.loads(recorder.body) == UNAUTHORIZED
    assert recorder.headers["www-authenticate"] == 'Bearer realm="agent-rooms"'
    assert recorder.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong-token-0123456789-abcdef",
        "Bearer ",
        "Bearer",
        "Basic claude-token-0123456789-abcdef",
        "claude-token-0123456789-abcdef",
        "",
    ],
)
async def test_malformed_or_unknown_token_is_401(authorization: str) -> None:
    recorder = await call("/mcp", authorization=authorization)
    assert recorder.status == 401
    assert json.loads(recorder.body) == UNAUTHORIZED


@pytest.mark.anyio
async def test_bearer_scheme_is_case_insensitive() -> None:
    recorder = await call("/mcp", authorization="bearer claude-token-0123456789-abcdef")
    assert recorder.status == 200


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/rooms", "/api/rooms/r1/messages", "/api/rooms/r1/status"])
async def test_agent_is_forbidden_on_admin_rest(path: str) -> None:
    recorder = await call(path, token="claude-token-0123456789-abcdef")
    assert recorder.status == 403
    assert json.loads(recorder.body)["error"] == "forbidden"
    assert recorder.scopes == []


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/listen/next", "/mcp"])
async def test_human_is_forbidden_on_agent_endpoints(path: str) -> None:
    recorder = await call(path, token="human-token-0123456789-abcdef")
    assert recorder.status == 403
    assert json.loads(recorder.body)["error"] == "forbidden"
    assert recorder.scopes == []


@pytest.mark.anyio
async def test_human_may_use_admin_rest() -> None:
    recorder = await call("/api/rooms", token="human-token-0123456789-abcdef")
    assert recorder.status == 200
    assert recorder.scopes[0]["state"]["agent"] == "human"


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/listen/next", "/mcp"])
async def test_agent_may_use_agent_endpoints_and_state_is_set(path: str) -> None:
    recorder = await call(path, token="claude-token-0123456789-abcdef")
    assert recorder.status == 200
    assert agent_of(recorder.scopes[0]) == "claude"


@pytest.mark.anyio
async def test_existing_scope_state_is_preserved() -> None:
    recorder = Recorder()
    middleware = BearerAuthMiddleware(recorder.app, FakeRegistry())
    shared: dict[str, Any] = {"keep": 1}
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer claude-token-0123456789-abcdef")],
        "state": shared,
    }
    await middleware(scope, receive, recorder.send)
    assert shared == {"keep": 1, "agent": "claude"}


@pytest.mark.anyio
async def test_lifespan_scope_passes_through_untouched() -> None:
    recorder = Recorder()
    middleware = BearerAuthMiddleware(recorder.app, FakeRegistry())
    scope: dict[str, Any] = {"type": "lifespan"}
    await middleware(scope, receive, recorder.send)
    assert recorder.scopes == [scope]
    assert "state" not in scope


def test_agent_of_reads_scope_and_request_like_objects() -> None:
    scope = {"state": {"agent": "codex"}}
    assert agent_of(scope) == "codex"
    assert agent_of(SimpleNamespace(scope=scope)) == "codex"


HUMAN_TOKEN = "human-token-0123456789-abcdef"
CLAUDE_TOKEN = "claude-token-0123456789-abcdef"
NOT_FOUND = {"error": "not_found", "message": "No such endpoint."}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    ["/nope", "/docs", "/api", "/api/", "/api/rooms/", "/api/rooms/r1/unknown", "/api/rooms/a/b/c",
     "/api/listen", "/api/listen/", "/api/listen/other", "/mcp/", "/mcp/x", "//api/rooms", "/API/rooms",
     "/healthz/", "//"],
)  # fmt: skip
@pytest.mark.parametrize("token", [HUMAN_TOKEN, CLAUDE_TOKEN])
async def test_paths_outside_the_allow_list_are_404_for_any_authenticated_identity(
    path: str, token: str
) -> None:
    recorder = await call(path, token=token)
    assert recorder.status == 404
    assert json.loads(recorder.body) == NOT_FOUND
    assert recorder.scopes == []


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/nope", "/api/rooms/r1/unknown", "/mcp/x"])
async def test_paths_outside_the_allow_list_are_401_without_a_token(path: str) -> None:
    recorder = await call(path)
    assert recorder.status == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/rooms"),
        ("POST", "/api/rooms"),
        ("GET", "/api/rooms/r1/messages"),
        ("POST", "/api/rooms/r1/messages"),
        ("POST", "/api/rooms/r1/status"),
        ("DELETE", "/api/rooms/r1"),
    ],
)
async def test_every_admin_route_is_human_only(method: str, path: str) -> None:
    assert (await call(path, method=method, token=HUMAN_TOKEN)).status == 200
    assert (await call(path, method=method, token=CLAUDE_TOKEN)).status == 403
    assert (await call(path, method=method)).status == 401


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/", "/healthz"])
async def test_authenticated_callers_reach_the_public_pages_with_any_method(path: str) -> None:
    assert (await call(path, method="POST", token=CLAUDE_TOKEN)).status == 200


@pytest.mark.anyio
@pytest.mark.parametrize("scope_type", ["websocket", "something-else"])
async def test_only_lifespan_and_http_scopes_reach_the_app(scope_type: str) -> None:
    recorder = Recorder()
    middleware = BearerAuthMiddleware(recorder.app, FakeRegistry())
    scope: dict[str, Any] = {
        "type": scope_type,
        "path": "/mcp",
        "headers": [(b"authorization", f"Bearer {CLAUDE_TOKEN}".encode())],
    }
    await middleware(scope, receive, recorder.send)
    assert recorder.scopes == []
    if scope_type == "websocket":
        assert [m["type"] for m in recorder.sent] == ["websocket.close"]
