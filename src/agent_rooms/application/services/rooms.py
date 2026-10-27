from agent_rooms.application.dto import RoomSummary
from agent_rooms.application.limits import Limits
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.application.services._lookup import require_member, require_room
from agent_rooms.application.services._summaries import summarize
from agent_rooms.application.services._transitions import change_room_status
from agent_rooms.domain.errors import RoomNotClosed
from agent_rooms.domain.models import (
    SYSTEM,
    MessageKind,
    Role,
    Room,
    RoomStatus,
    validate_slug,
    validate_topic,
)
from agent_rooms.domain.policies import ensure_role_exclusive, role_change_notice, status_change_notice


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
        with self._uow.transaction():
            room = require_room(self._uow, slug)
            if room.status is status:
                return summarize(self._uow, self._clock, room)
            if status is RoomStatus.ACTIVE:
                self._uow.rooms.reset_budget(slug, self._uow.messages.latest_id(slug))
            change_room_status(self._uow, self._clock, slug, status, status_change_notice(status))
        return self.get_summary(slug)

    def set_member_role(self, slug: str, agent: str, alias: str, role: Role) -> RoomSummary:
        with self._uow.transaction():
            require_room(self._uow, slug)
            member = require_member(self._uow, slug, agent, alias)
            if member.role is role:
                return self.get_summary(slug)
            ensure_role_exclusive(slug, self._uow.members.list_for_room(slug), agent, alias, role)
            if role is Role.LEAD:
                for current in self._uow.members.list_for_room(slug):
                    if current.role is Role.LEAD:
                        self._change_role(slug, current.agent, current.alias, Role.OBSERVER)
            self._change_role(slug, agent, alias, role)
        return self.get_summary(slug)

    def _change_role(self, slug: str, agent: str, alias: str, role: Role) -> None:
        member = require_member(self._uow, slug, agent, alias)
        self._uow.members.set_role(slug, agent, alias, role)
        self._uow.messages.add(
            room=slug,
            sender=SYSTEM,
            alias="",
            kind=MessageKind.SYSTEM,
            text=role_change_notice(member.display_name(), member.role, role),
            reply_to=None,
            created_at=self._clock.now(),
        )

    def delete_room(self, slug: str) -> None:
        with self._uow.transaction():
            room = require_room(self._uow, slug)
            if room.status is not RoomStatus.CLOSED:
                raise RoomNotClosed(
                    f"Room '{slug}' is {room.status}; only closed rooms can be deleted. Close the room first."
                )
            self._uow.rooms.delete(slug)
