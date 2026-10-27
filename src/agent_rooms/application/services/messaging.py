from agent_rooms.application.dto import PostResult
from agent_rooms.application.limits import Limits, clamp
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.application.services._lookup import require_member, require_room
from agent_rooms.domain.errors import GuardTripped, InvalidInput, RoomNotActive
from agent_rooms.domain.models import (
    HUMAN,
    SYSTEM,
    Member,
    Message,
    MessageKind,
    Room,
    RoomStatus,
    validate_alias,
    validate_text,
)
from agent_rooms.domain.policies import (
    GuardTrip,
    after_post_trip,
    ensure_human_kind,
    ensure_kind_allowed,
    message_cap_trip,
)


class Messaging:
    def __init__(self, uow: UnitOfWork, clock: Clock, limits: Limits) -> None:
        self._uow = uow
        self._clock = clock
        self._limits = limits

    def post(  # noqa: PLR0917 - positional order is the spec'd signature
        self,
        agent: str,
        room: str,
        text: str,
        kind: MessageKind = MessageKind.CHAT,
        reply_to: int | None = None,
        alias: str = "",
    ) -> PostResult:
        validate_alias(alias)
        found = require_room(self._uow, room)
        member = require_member(self._uow, room, agent, alias)
        if found.status is not RoomStatus.ACTIVE:
            raise RoomNotActive(
                f"Room '{room}' is {found.status}; it does not accept posts. Stop and let the human decide."
            )
        ensure_kind_allowed(member.role, kind)
        validate_text(text, self._limits.max_text_length)
        self._ensure_reply_target(room, reply_to)

        with self._uow.transaction():
            cap_trip = message_cap_trip(
                self._uow.messages.count_agent_posts_since(room, found.budget_start_id), found.max_messages
            )
            if cap_trip is None:
                return self._store_agent_post(found, member, text, kind, reply_to)
            self._apply_trip(room, cap_trip)
        raise GuardTripped(cap_trip.notice)

    def post_as_human(self, room: str, text: str, kind: MessageKind = MessageKind.CHAT) -> Message:
        found = require_room(self._uow, room)
        if found.status is RoomStatus.CLOSED:
            raise RoomNotActive(f"Room '{room}' is closed; it does not accept posts.")
        ensure_human_kind(kind)
        validate_text(text, self._limits.max_text_length)
        return self._add(room, HUMAN, kind, text)

    def read(self, agent: str, room: str, alias: str = "", limit: int = 50) -> list[Message]:
        require_room(self._uow, room)
        with self._uow.transaction():
            member = require_member(self._uow, room, agent, alias)
            messages = self._uow.messages.unread_for(member, clamp(limit, 1, self._limits.read_max))
            if messages:
                self._uow.members.advance_cursor(room, agent, alias, messages[-1].id)
        return messages

    def history(self, room: str, after_id: int = 0, limit: int = 200) -> list[Message]:
        require_room(self._uow, room)
        return self._uow.messages.after(room, after_id, clamp(limit, 1, self._limits.history_max))

    def _ensure_reply_target(self, room: str, reply_to: int | None) -> None:
        if reply_to is not None and self._uow.messages.get(room, reply_to) is None:
            raise InvalidInput(f"reply_to {reply_to} is not a message in room '{room}'.")

    def _store_agent_post(
        self, room: Room, member: Member, text: str, kind: MessageKind, reply_to: int | None
    ) -> PostResult:
        message = self._add(room.slug, member.agent, kind, text, alias=member.alias, reply_to=reply_to)
        trip = after_post_trip(
            kind,
            member.display_name(),
            self._uow.messages.count_kind_since(
                room.slug, MessageKind.CHANGES_REQUESTED, room.budget_start_id
            ),
            self._limits.review_round_cap,
        )
        if trip is None:
            return PostResult(message, RoomStatus.ACTIVE, None)
        self._apply_trip(room.slug, trip)
        return PostResult(message, trip.status, trip.notice)

    def _apply_trip(self, room: str, trip: GuardTrip) -> None:
        self._uow.rooms.set_status(room, trip.status)
        self._add(room, SYSTEM, MessageKind.SYSTEM, trip.notice)

    def _add(
        self,
        room: str,
        sender: str,
        kind: MessageKind,
        text: str,
        *,
        alias: str = "",
        reply_to: int | None = None,
    ) -> Message:
        return self._uow.messages.add(
            room=room,
            sender=sender,
            alias=alias,
            kind=kind,
            text=text,
            reply_to=reply_to,
            created_at=self._clock.now(),
        )
