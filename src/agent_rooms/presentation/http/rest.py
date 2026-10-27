import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from agent_rooms.application.dto import Wake
from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import InvalidInput
from agent_rooms.domain.models import parse_kind, parse_role, parse_status, validate_alias
from agent_rooms.presentation.http.csp import content_security_policy
from agent_rooms.presentation.http.errors import Endpoint, HttpError, api_errors
from agent_rooms.presentation.identity import agent_of
from agent_rooms.presentation.serializers import message_json, summary_json, wake_json

MAX_BODY_BYTES = 64 * 1024
MAX_SESSION_LENGTH = 128
HISTORY_DEFAULT_LIMIT = 200
WEB_UI = files("agent_rooms") / "presentation" / "http" / "web" / "index.html"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@dataclass(frozen=True, slots=True)
class RouteSpec:
    path: str
    methods: tuple[str, ...]
    endpoint: Endpoint


def _too_large() -> HttpError:
    return HttpError(413, "payload_too_large", f"Request body exceeds {MAX_BODY_BYTES} bytes.")


async def _json_object(request: Request) -> dict[str, Any]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _too_large()
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks))
    except ValueError:
        raise InvalidInput("Request body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise InvalidInput("Request body must be a JSON object.")
    return data


def _string(body: dict[str, Any], name: str, default: str | None = None) -> str:
    value = body.get(name, default)
    if not isinstance(value, str):
        raise InvalidInput(f"Field '{name}' is required and must be a string.")
    return value


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise InvalidInput(f"Query parameter '{name}' must be an integer.") from None


def _session_param(request: Request) -> str | None:
    session = request.query_params.get("session", "")
    if len(session) > MAX_SESSION_LENGTH:
        raise InvalidInput(f"Query parameter 'session' must be at most {MAX_SESSION_LENGTH} characters.")
    return session or None


def _alias_param(request: Request) -> str | None:
    alias = request.query_params.get("alias", "")
    return validate_alias(alias) or None


def build_routes(
    *, rooms: RoomAdmin, messaging: Messaging, listening: Listening, limits: Limits
) -> list[RouteSpec]:
    page = WEB_UI.read_text(encoding="utf-8")
    page_headers = {"Content-Security-Policy": content_security_policy(page), **SECURITY_HEADERS}

    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def index(_request: Request) -> Response:
        return HTMLResponse(page, headers=page_headers)

    @api_errors
    async def list_rooms(_request: Request) -> Response:
        return JSONResponse([summary_json(summary) for summary in rooms.list_rooms()])

    @api_errors
    async def create_room(request: Request) -> Response:
        body = await _json_object(request)
        summary = rooms.create_room(_string(body, "slug"), _string(body, "topic"))
        return JSONResponse(summary_json(summary), status_code=201)

    @api_errors
    async def room_messages(request: Request) -> Response:
        slug = request.path_params["room"]
        after = _int_param(request, "after", 0)
        limit = _int_param(request, "limit", HISTORY_DEFAULT_LIMIT)
        summary = rooms.get_summary(slug)
        messages = messaging.history(slug, after, limit)
        return JSONResponse(
            {"room": summary_json(summary), "messages": [message_json(message) for message in messages]}
        )

    @api_errors
    async def post_message(request: Request) -> Response:
        body = await _json_object(request)
        message = messaging.post_as_human(
            request.path_params["room"],
            _string(body, "text"),
            parse_kind(_string(body, "kind", "chat")),
        )
        return JSONResponse(message_json(message), status_code=201)

    @api_errors
    async def set_status(request: Request) -> Response:
        body = await _json_object(request)
        summary = rooms.set_status(request.path_params["room"], parse_status(_string(body, "status")))
        return JSONResponse(summary_json(summary))

    @api_errors
    async def set_member_role(request: Request) -> Response:
        body = await _json_object(request)
        summary = rooms.set_member_role(
            request.path_params["room"],
            _string(body, "agent"),
            validate_alias(_string(body, "alias", "")),
            parse_role(_string(body, "role")),
        )
        return JSONResponse(summary_json(summary))

    @api_errors
    async def delete_room(request: Request) -> Response:
        rooms.delete_room(request.path_params["room"])
        return Response(status_code=204)

    @api_errors
    async def listen_next(request: Request) -> Response:
        timeout = _int_param(request, "timeout", limits.listen_next_default_s)
        result = await listening.next_for_listener(
            agent_of(request),
            timeout,
            alias=_alias_param(request),
            session=_session_param(request),
            should_stop=request.is_disconnected,
        )
        if isinstance(result, Wake):
            return JSONResponse(wake_json(result))
        return Response(status_code=204, headers={"X-Listen-State": result.reason})

    return [
        RouteSpec("/healthz", ("GET",), healthz),
        RouteSpec("/", ("GET",), index),
        RouteSpec("/api/rooms", ("GET",), list_rooms),
        RouteSpec("/api/rooms", ("POST",), create_room),
        RouteSpec("/api/rooms/{room}", ("DELETE",), delete_room),
        RouteSpec("/api/rooms/{room}/messages", ("GET",), room_messages),
        RouteSpec("/api/rooms/{room}/messages", ("POST",), post_message),
        RouteSpec("/api/rooms/{room}/status", ("POST",), set_status),
        RouteSpec("/api/rooms/{room}/members/role", ("POST",), set_member_role),
        RouteSpec("/api/listen/next", ("GET",), listen_next),
    ]
