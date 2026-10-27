from collections.abc import Callable
from importlib.resources import files

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_rooms import __version__
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.infrastructure.clock import SystemClock
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from agent_rooms.infrastructure.token_registry import JsonFileTokenRegistry
from agent_rooms.presentation.http.auth import BearerAuthMiddleware
from agent_rooms.presentation.http.host_guard import HostGuardMiddleware
from agent_rooms.presentation.http.rest import build_routes
from agent_rooms.presentation.mcp.tools import register_tools
from agent_rooms.settings import Settings

INSTRUCTIONS = (
    "Agent Rooms: shared rooms where coding agents exchange messages. "
    "Use list_rooms and join_room first, then follow the protocol returned by join_room."
)


def _protocol_text() -> str:
    return (files("agent_rooms") / "resources" / "protocol.md").read_text(encoding="utf-8")


class CloseOnShutdown:
    """Pure ASGI wrapper: runs `close` once the wrapped app's lifespan has ended (or failed)."""

    def __init__(self, app: ASGIApp, close: Callable[[], None]) -> None:
        self.app = app
        self._close = close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._close()


def create_app(settings: Settings | None = None) -> ASGIApp:
    settings = settings or Settings.from_env()
    registry = JsonFileTokenRegistry.from_file(settings.tokens_file)
    protocol = _protocol_text()
    uow = SqliteUnitOfWork.open(settings.db_path)
    try:
        return _compose(settings, registry, protocol, uow)
    except BaseException:
        uow.close()
        raise


def _compose(
    settings: Settings, registry: JsonFileTokenRegistry, protocol: str, uow: SqliteUnitOfWork
) -> ASGIApp:
    clock, limits = SystemClock(), settings.limits
    rooms = RoomAdmin(uow, clock, limits)
    membership = Membership(uow, clock, limits, protocol)
    messaging = Messaging(uow, clock, limits)
    listening = Listening(uow, clock, limits)

    server = MCPServer(name="agent-rooms", instructions=INSTRUCTIONS, version=__version__)
    register_tools(server, rooms=rooms, membership=membership, messaging=messaging, listening=listening)
    for route in build_routes(rooms=rooms, messaging=messaging, listening=listening, limits=limits):
        server.custom_route(route.path, methods=list(route.methods))(route.endpoint)

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
    guarded = HostGuardMiddleware(BearerAuthMiddleware(app, registry), settings.allowed_hosts)
    return CloseOnShutdown(guarded, uow.close)
