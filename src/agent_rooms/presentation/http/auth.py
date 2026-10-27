from enum import Enum, auto

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from agent_rooms.application.ports import TokenRegistry
from agent_rooms.domain.models import HUMAN
from agent_rooms.presentation.identity import bind_agent

PUBLIC_METHODS = frozenset({"GET", "HEAD"})
WEBSOCKET_POLICY_VIOLATION = 1008

_UNAUTHORIZED = {"error": "unauthorized", "message": "Missing or invalid bearer token."}
_NOT_FOUND = {"error": "not_found", "message": "No such endpoint."}
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="agent-rooms"'}


class Audience(Enum):
    PUBLIC = auto()
    HUMAN = auto()
    AGENTS = auto()


_PUBLIC_PATHS = frozenset({"/", "/healthz"})
_AGENT_PATHS = frozenset({"/api/listen/next", "/mcp"})
_ROOM_SUBPATHS = frozenset({"messages", "status"})


def audience_of(path: str) -> Audience | None:
    """The explicit access policy: who may call which path. None means the path does not exist."""
    if path in _PUBLIC_PATHS:
        return Audience.PUBLIC
    if path in _AGENT_PATHS:
        return Audience.AGENTS
    if path == "/api/rooms":
        return Audience.HUMAN
    parts = path.split("/")
    is_room_path = parts[:3] == ["", "api", "rooms"] and all(parts[3:])
    if is_room_path and (len(parts) == 4 or (len(parts) == 5 and parts[4] in _ROOM_SUBPATHS)):
        return Audience.HUMAN
    return None


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if scheme.lower() == "bearer" and token else None
    return None


def _forbidden(message: str) -> JSONResponse:
    return JSONResponse({"error": "forbidden", "message": message}, status_code=403)


def _access_violation(agent: str, audience: Audience) -> JSONResponse | None:
    if audience is Audience.HUMAN and agent != HUMAN:
        return _forbidden("Only the human token may use the room administration API.")
    if audience is Audience.AGENTS and agent == HUMAN:
        return _forbidden("The human token cannot use agent endpoints; use the /api/rooms REST API.")
    return None


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, registry: TokenRegistry) -> None:
        self.app = app
        self._registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self.app(scope, receive, send)
        elif kind == "http":
            await self._http(scope, receive, send)
        elif kind == "websocket":
            await WebSocketClose(WEBSOCKET_POLICY_VIOLATION)(scope, receive, send)

    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        audience = audience_of(scope["path"])
        if audience is Audience.PUBLIC and scope["method"] in PUBLIC_METHODS:
            await self.app(scope, receive, send)
            return
        token = _bearer_token(scope)
        agent = None if token is None else self._registry.agent_for(token)
        if agent is None:
            await JSONResponse(_UNAUTHORIZED, status_code=401, headers=_CHALLENGE)(scope, receive, send)
            return
        if audience is None:
            await JSONResponse(_NOT_FOUND, status_code=404)(scope, receive, send)
            return
        violation = _access_violation(agent, audience)
        if violation is not None:
            await violation(scope, receive, send)
            return
        bind_agent(scope, agent)
        await self.app(scope, receive, send)
