from agent_rooms.application.dto import MemberView, RoomSummary
from agent_rooms.application.limits import Limits
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.application.services._lookup import require_room
from agent_rooms.domain.models import SYSTEM, MessageKind, Room, RoomStatus, validate_slug, validate_topic
from agent_rooms.domain.policies import status_change_notice


def summarize(uow: UnitOfWork, clock: Clock, room: Room) -> RoomSummary:
    now = clock.now()
    members = tuple(
        MemberView(m.agent, m.alias, m.role, m.is_listening(now))
        for m in uow.members.list_for_room(room.slug)
    )
    return RoomSummary(
        slug=room.slug,
        topic=room.topic,
        status=room.status,
        max_messages=room.max_messages,
        message_count=uow.messages.count(room.slug),
        members=members,
        created_at=room.created_at,
    )


class RoomAdmin:
    def __init__(self, uow: UnitOfWork, clock: Clock, limits: Limits) -> None:
        self._uow = uow
        self._clock = clock
        self._limits = limits

    def list_rooms(self) -> list[RoomSummary]:
        return [summarize(self._uow, self._clock, room) for room in self._uow.rooms.list_all()]

    def get_summary(self, slug: str) -> RoomSummary:
        return summarize(self._uow, self._clock, require_room(self._uow, slug))

    def create_room(self, slug: str, topic: str) -> RoomSummary:
        room = Room(
            slug=validate_slug(slug),
            topic=validate_topic(topic),
            status=RoomStatus.ACTIVE,
            max_messages=self._limits.default_max_messages,
            budget_start_id=0,
            created_at=self._clock.now(),
        )
        self._uow.rooms.add(room)
        return summarize(self._uow, self._clock, room)

    def set_status(self, slug: str, status: RoomStatus) -> RoomSummary:
        room = require_room(self._uow, slug)
        if room.status is status:
            return summarize(self._uow, self._clock, room)
        with self._uow.transaction():
            if status is RoomStatus.ACTIVE:
                self._uow.rooms.reset_budget(slug, self._uow.messages.latest_id(slug))
            self._uow.rooms.set_status(slug, status)
            self._uow.messages.add(
                room=slug,
                sender=SYSTEM,
                alias="",
                kind=MessageKind.SYSTEM,
                text=status_change_notice(status),
                reply_to=None,
                created_at=self._clock.now(),
            )
        return self.get_summary(slug)
