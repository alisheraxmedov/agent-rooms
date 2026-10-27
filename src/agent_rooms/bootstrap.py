from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.types import ASGIApp

from agent_rooms import __version__
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.infrastructure.clock import SystemClock
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from agent_rooms.infrastructure.token_registry import JsonFileTokenRegistry
from agent_rooms.presentation.http.auth import BearerAuthMiddleware
from agent_rooms.presentation.http.rest import build_routes
from agent_rooms.presentation.mcp.tools import register_tools
from agent_rooms.settings import Settings

INSTRUCTIONS = (
    "Agent Rooms: shared rooms where coding agents exchange messages. "
    "Use list_rooms and join_room first, then follow the protocol returned by join_room."
)


def _protocol_text() -> str:
    return (files("agent_rooms") / "resources" / "protocol.md").read_text(encoding="utf-8")


def _close_on_shutdown(app: Starlette, uow: SqliteUnitOfWork) -> None:
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope_app: Starlette) -> AsyncIterator[None]:
        try:
            async with inner(scope_app):
                yield
        finally:
            uow.close()

    app.router.lifespan_context = lifespan


def create_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or Settings.from_env()
    registry = JsonFileTokenRegistry.from_file(settings.tokens_file)
    protocol = _protocol_text()
    uow = SqliteUnitOfWork.open(settings.db_path)
    clock, limits = SystemClock(), settings.limits

    rooms = RoomAdmin(uow, clock, limits)
    membership = Membership(uow, clock, limits, protocol)
    messaging = Messaging(uow, clock, limits)
    listening = Listening(uow, clock, limits)

    server = MCPServer(name="agent-rooms", instructions=INSTRUCTIONS, version=__version__)
    register_tools(server, rooms=rooms, membership=membership, messaging=messaging, listening=listening)
    for route in build_routes(rooms=rooms, messaging=messaging, listening=listening, limits=limits):
        server.custom_route(route.path, methods=sorted((route.methods or set()) - {"HEAD"}))(route.endpoint)

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts),
            allowed_origins=[f"http://{host}" for host in settings.allowed_hosts],
        ),
    )
    _close_on_shutdown(app, uow)
    return BearerAuthMiddleware(app, registry)
