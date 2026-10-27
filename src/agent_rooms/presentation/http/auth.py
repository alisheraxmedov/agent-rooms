from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_rooms.application.ports import TokenRegistry
from agent_rooms.domain.models import HUMAN

PUBLIC_PATHS = frozenset({"/", "/healthz"})
PUBLIC_METHODS = frozenset({"GET", "HEAD"})
HUMAN_ONLY_PREFIXES = ("/api/rooms",)
AGENT_ONLY_PREFIXES = ("/api/listen", "/mcp")

_UNAUTHORIZED = {"error": "unauthorized", "message": "Missing or invalid bearer token."}
_CHALLENGE = {"WWW-Authenticate": 'Bearer realm="agent-rooms"'}


def agent_of(request_or_scope: Any) -> str:
    scope = request_or_scope if isinstance(request_or_scope, dict) else request_or_scope.scope
    agent: str = scope["state"]["agent"]
    return agent


def _bearer_token(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            token = token.strip()
            return token if scheme.lower() == "bearer" and token else None
    return None


def _forbidden(message: str) -> JSONResponse:
    return JSONResponse({"error": "forbidden", "message": message}, status_code=403)


def _access_violation(agent: str, path: str) -> JSONResponse | None:
    if agent != HUMAN and path.startswith(HUMAN_ONLY_PREFIXES):
        return _forbidden("Only the human token may use the room administration API.")
    if agent == HUMAN and path.startswith(AGENT_ONLY_PREFIXES):
        return _forbidden("The human token cannot use agent endpoints; use the /api/rooms REST API.")
    return None


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, registry: TokenRegistry) -> None:
        self.app = app
        self._registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._is_public(scope):
            await self.app(scope, receive, send)
            return
        token = _bearer_token(scope)
        agent = None if token is None else self._registry.agent_for(token)
        if agent is None:
            response = JSONResponse(_UNAUTHORIZED, status_code=401, headers=_CHALLENGE)
            await response(scope, receive, send)
            return
        violation = _access_violation(agent, scope["path"])
        if violation is not None:
            await violation(scope, receive, send)
            return
        scope.setdefault("state", {})["agent"] = agent
        await self.app(scope, receive, send)

    @staticmethod
    def _is_public(scope: Scope) -> bool:
        return scope["method"] in PUBLIC_METHODS and scope["path"] in PUBLIC_PATHS
