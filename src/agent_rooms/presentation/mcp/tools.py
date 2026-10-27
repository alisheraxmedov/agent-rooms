from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import DomainError
from agent_rooms.domain.models import parse_kind, parse_role
from agent_rooms.presentation.http.auth import agent_of
from agent_rooms.presentation.serializers import iso, message_json, summary_json

OPENCODE_SESSION_KEY = "ai.opencode/sessionID"

RoleName = Literal["writer", "reviewer", "observer"]
PostKind = Literal["chat", "task", "review_request", "changes_requested", "approved", "done"]


def _agent(ctx: Context) -> str:
    request = ctx.request_context.request
    try:
        return agent_of(request) if request is not None else _unauthenticated()
    except KeyError:
        return _unauthenticated()


def _unauthenticated() -> str:
    raise ToolError("unauthorized: this tool needs an authenticated HTTP request.")


def _opencode_session(ctx: Context) -> str | None:
    value = (ctx.request_context.meta or {}).get(OPENCODE_SESSION_KEY)
    return value if isinstance(value, str) and value else None


@contextmanager
def _domain_errors() -> Iterator[None]:
    try:
        yield
    except DomainError as error:
        raise ToolError(f"{error.code}: {error}") from error


def register_tools(
    server: MCPServer,
    *,
    rooms: RoomAdmin,
    membership: Membership,
    messaging: Messaging,
    listening: Listening,
) -> None:
    @server.tool()
    async def list_rooms() -> dict[str, Any]:
        """List all rooms with status, topic, message count and members. Call this first to find a room."""
        with _domain_errors():
            return {"rooms": [summary_json(summary) for summary in rooms.list_rooms()]}

    @server.tool()
    async def create_room(slug: str, topic: str) -> dict[str, Any]:
        """Create a room (slug: lowercase letters, digits and '-', max 48 chars; topic: what the room is for).
        Create one only when no suitable room exists yet; the human may have prepared it."""
        with _domain_errors():
            return summary_json(rooms.create_room(slug, topic))

    @server.tool()
    async def join_room(room: str, role: RoleName, ctx: Context, alias: str = "") -> dict[str, Any]:
        """Join a room before using it. role is writer (changes code), reviewer (reviews, never edits) or
        observer (chat only). Returns the room summary, the latest messages and the protocol you must follow:
        read it. Pass the same alias to every later tool call if you set one (needed only to join the same
        room twice as different personas). Joining again resets your read position to the newest message."""
        with _domain_errors():
            result = membership.join(_agent(ctx), room, parse_role(role), alias)
            return {
                "room": summary_json(result.room),
                "recent_messages": [message_json(message) for message in result.recent],
                "protocol": result.protocol,
            }

    @server.tool()
    async def post(  # noqa: PLR0917 - parameter order is part of the tool contract
        room: str,
        text: str,
        ctx: Context,
        kind: PostKind = "chat",
        reply_to: int | None = None,
        alias: str = "",
    ) -> dict[str, Any]:
        """Post a message to a room you joined. text is at most 8000 characters: send file paths and a
        commit SHA, not pasted diffs. kind must be allowed for your role (writer: chat, task,
        review_request, done; reviewer: chat, task, changes_requested, approved; observer: chat).
        reply_to is an optional message id. Returns the new message id and the room status; a paused
        or closed room rejects posts."""
        with _domain_errors():
            result = messaging.post(_agent(ctx), room, text, parse_kind(kind), reply_to, alias)
            return {
                "id": result.message.id,
                "room_status": result.room_status.value,
                "notice": result.notice,
            }

    @server.tool()
    async def read(room: str, ctx: Context, limit: int = 50, alias: str = "") -> dict[str, Any]:
        """Return unread messages from other participants (oldest first, at most limit) and mark them read.
        Your own messages are never returned. An empty list means nothing new."""
        with _domain_errors():
            messages = messaging.read(_agent(ctx), room, alias, limit)
            return {"messages": [message_json(message) for message in messages]}

    @server.tool()
    async def wait(room: str, ctx: Context, timeout_s: int = 30, alias: str = "") -> dict[str, Any]:
        """Block up to timeout_s seconds (server maximum applies) until a new message arrives, then return it
        like read. On timeout returns no messages and a hint. Use it to wait briefly for a reply; for long
        waits call listen and end your turn instead. If the hint says the room is paused or closed, stop."""
        with _domain_errors():
            result = await listening.wait(_agent(ctx), room, alias, timeout_s)
            return {"messages": [message_json(message) for message in result.messages], "hint": result.hint}

    @server.tool()
    async def listen(room: str, ctx: Context, minutes: int = 30, alias: str = "") -> dict[str, Any]:
        """Ask to be woken when the next message arrives in this room, for up to minutes (max 60). After
        this call finish your turn immediately: a hook or plugin starts a new turn with the message. One
        wake-up per listen call, so call listen again after you reply."""
        with _domain_errors():
            ack = listening.listen(
                _agent(ctx), room, alias=alias, minutes=minutes, session=_opencode_session(ctx)
            )
            return {
                "room": ack.room,
                "listening_until": iso(ack.listening_until),
                "instruction": ack.instruction,
            }

    @server.tool()
    async def leave_room(room: str, ctx: Context, alias: str = "") -> dict[str, Any]:
        """Leave a room; you stop receiving its messages and wake-ups. You can join again later."""
        with _domain_errors():
            membership.leave(_agent(ctx), room, alias)
            return {"left": True}
