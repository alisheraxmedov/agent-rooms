import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent_rooms.application.limits import Limits
from agent_rooms.bootstrap import create_app
from agent_rooms.settings import Settings

TOKENS = {
    "claude": "claude-test-token-0123456789",
    "codex": "codex-test-token-0123456789",
    "opencode": "opencode-test-token-0123456789",
    "human": "human-test-token-0123456789",
}

CallTool = Callable[..., Awaitable[dict[str, Any]]]
CallError = Callable[..., Awaitable[str]]
Rest = Callable[..., Awaitable[httpx2.Response]]


@dataclass(frozen=True)
class LiveServer:
    base_url: str
    port: int

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp"

    def headers(self, agent: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKENS[agent]}"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_healthy(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with urllib.request.urlopen(url, timeout=1):  # noqa: S310
                return
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise RuntimeError(f"server did not become healthy at {url}") from None
            time.sleep(0.02)


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[LiveServer]:
    tokens_file = tmp_path / "agents.json"
    tokens_file.write_text(json.dumps(TOKENS))
    port = _free_port()
    settings = Settings(
        host="127.0.0.1",
        port=port,
        db_path=tmp_path / "rooms.db",
        tokens_file=tokens_file,
        limits=Limits(poll_interval_s=0.05, wait_max_s=1),
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_until_healthy(f"http://127.0.0.1:{port}/healthz", timeout_s=10)
    yield LiveServer(f"http://127.0.0.1:{port}", port)
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(params=["auto", "legacy"])
def mcp_mode(request: pytest.FixtureRequest) -> str:
    """auto = 2026-07-28 with server/discover (Claude Code); legacy = initialize (OpenCode)."""
    return str(request.param)


@asynccontextmanager
async def mcp_client(url: str, token: str, mode: str) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(15.0)) as http:
        transport = streamable_http_client(url, http_client=http, terminate_on_close=False)
        async with Client(transport, mode=mode) as client:  # type: ignore[arg-type]
            yield client


@pytest.fixture
def open_mcp(live_server: LiveServer, mcp_mode: str) -> Callable[[str], AbstractAsyncContextManager[Client]]:
    def _open(agent: str) -> AbstractAsyncContextManager[Client]:
        return mcp_client(live_server.mcp_url, TOKENS[agent], mcp_mode)

    return _open


@pytest.fixture
def call(live_server: LiveServer, mcp_mode: str) -> CallTool:
    async def _call(
        agent: str, tool: str, _meta: dict[str, Any] | None = None, **args: Any
    ) -> dict[str, Any]:
        async with mcp_client(live_server.mcp_url, TOKENS[agent], mcp_mode) as client:
            result = await client.call_tool(tool, args, meta=_meta)
            assert not result.is_error, result.content[0].text  # type: ignore[union-attr]
            assert result.structured_content is not None
            return result.structured_content

    return _call


@pytest.fixture
def call_error(live_server: LiveServer, mcp_mode: str) -> CallError:
    async def _call_error(agent: str, tool: str, _meta: dict[str, Any] | None = None, **args: Any) -> str:
        async with mcp_client(live_server.mcp_url, TOKENS[agent], mcp_mode) as client:
            result = await client.call_tool(tool, args, meta=_meta)
            assert result.is_error, result.structured_content
            return str(result.content[0].text)  # type: ignore[union-attr]

    return _call_error


@dataclass(frozen=True)
class ServerDescription:
    tools: list[Any]
    instructions: str | None
    protocol_version: str


@pytest.fixture
def describe_server(live_server: LiveServer, mcp_mode: str) -> Callable[[str], Awaitable[ServerDescription]]:
    async def _describe(agent: str) -> ServerDescription:
        async with mcp_client(live_server.mcp_url, TOKENS[agent], mcp_mode) as client:
            listed = await client.list_tools()
            return ServerDescription(listed.tools, client.instructions, str(client.protocol_version))

    return _describe


@pytest.fixture
def rest(live_server: LiveServer) -> Rest:
    async def _rest(agent: str | None, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        headers = {**(live_server.headers(agent) if agent else {}), **kwargs.pop("headers", {})}
        async with httpx2.AsyncClient(base_url=live_server.base_url, timeout=15.0) as http:
            return await http.request(method, path, headers=headers, **kwargs)

    return _rest
