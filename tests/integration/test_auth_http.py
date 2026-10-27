import http.client
import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import pytest

from agent_rooms.domain.models import Role

pytestmark = pytest.mark.integration

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
UNAUTHORIZED = {"error": "unauthorized", "message": "Missing or invalid bearer token."}
WEB_UI = files("agent_rooms") / "presentation" / "http" / "web" / "index.html"


def raw(
    port: int,
    method: str,
    path: str,
    *,
    host: str | None = None,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, dict[str, str], str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read().decode()
    finally:
        conn.close()


@pytest.mark.anyio
async def test_healthz_is_public(rest: Callable[..., Any]) -> None:
    response = await rest(None, "GET", "/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
@pytest.mark.skipif(not WEB_UI.is_file(), reason="web UI file not delivered yet")
async def test_index_is_public_html_with_security_headers(rest: Callable[..., Any]) -> None:
    response = await rest(None, "GET", "/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/rooms"),
        ("POST", "/api/rooms"),
        ("GET", "/api/rooms/x/messages"),
        ("GET", "/api/listen/next"),
        ("POST", "/mcp"),
        ("GET", "/healthz-not-public"),
        ("POST", "/healthz"),
    ],
)
async def test_missing_token_is_401_everywhere(rest: Callable[..., Any], method: str, path: str) -> None:
    response = await rest(None, method, path)
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert response.headers["www-authenticate"] == 'Bearer realm="agent-rooms"'


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/rooms", "/api/listen/next", "/mcp"])
async def test_wrong_token_is_401(rest: Callable[..., Any], path: str) -> None:
    response = await rest(None, "GET", path, headers={"Authorization": "Bearer not-a-real-token-0123456789"})
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED


@pytest.mark.anyio
async def test_mcp_client_without_token_is_rejected(live_server: Any) -> None:
    status, headers, body = raw(live_server.port, "POST", "/mcp", headers=MCP_HEADERS, body=LIST_TOOLS)
    assert status == 401
    assert json.loads(body) == UNAUTHORIZED
    assert headers["www-authenticate"] == 'Bearer realm="agent-rooms"'


@pytest.mark.anyio
@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/rooms"), ("POST", "/api/rooms"), ("GET", "/api/rooms/x/messages")]
)
async def test_agents_are_forbidden_on_admin_rest(
    rest: Callable[..., Any], agent: str, method: str, path: str
) -> None:
    response = await rest(agent, method, path)
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), [("GET", "/api/listen/next"), ("POST", "/mcp")])
async def test_human_is_forbidden_on_agent_endpoints(
    rest: Callable[..., Any], method: str, path: str
) -> None:
    response = await rest("human", method, path, headers=MCP_HEADERS)
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


@pytest.mark.anyio
async def test_mcp_rejects_foreign_host_header_with_valid_token(live_server: Any) -> None:
    headers = {**MCP_HEADERS, **live_server.headers("claude")}
    status, _, _ = raw(
        live_server.port, "POST", "/mcp", host="evil.example", headers=headers, body=LIST_TOOLS
    )
    assert status == 421
    status, _, _ = raw(
        live_server.port,
        "POST",
        "/mcp",
        host=f"localhost:{live_server.port}",
        headers=headers,
        body=LIST_TOOLS,
    )
    assert status == 200


@pytest.mark.anyio
async def test_mcp_rejects_foreign_origin(live_server: Any) -> None:
    headers = {**MCP_HEADERS, **live_server.headers("claude"), "Origin": "http://evil.example"}
    status, _, _ = raw(live_server.port, "POST", "/mcp", headers=headers, body=LIST_TOOLS)
    assert status == 403


@pytest.mark.anyio
async def test_identity_comes_from_the_token_not_from_arguments(call: Any) -> None:
    await call("claude", "create_room", slug="who", topic="identity check")
    await call("claude", "join_room", room="who", role=Role.WRITER.value)
    await call("codex", "join_room", room="who", role=Role.REVIEWER.value)
    await call("claude", "post", room="who", text="hello")
    read = await call("codex", "read", room="who")
    assert [m["sender"] for m in read["messages"]] == ["claude"]
