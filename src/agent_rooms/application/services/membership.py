from agent_rooms.application.dto import JoinResult
from agent_rooms.application.limits import Limits
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.application.services._lookup import require_member, require_room
from agent_rooms.application.services._summaries import summarize
from agent_rooms.domain.errors import InvalidInput
from agent_rooms.domain.models import Member, Role, display_name, validate_alias


class Membership:
    def __init__(self, uow: UnitOfWork, clock: Clock, limits: Limits, protocol_text: str) -> None:
        self._uow = uow
        self._clock = clock
        self._limits = limits
        self._protocol_text = protocol_text

    def join(self, agent: str, room: str, role: Role, alias: str = "") -> JoinResult:
        validate_alias(alias)
        found = require_room(self._uow, room)
        with self._uow.transaction():
            self._ensure_role_exclusive(room, agent, alias, role)
            existing = self._uow.members.get(room, agent, alias)
            self._uow.members.upsert(
                Member(
                    room=room,
                    agent=agent,
                    alias=alias,
                    role=role,
                    last_read_id=self._uow.messages.latest_id(room),
                    listening_until=None,
                    listener_session=None,
                    joined_at=existing.joined_at if existing else self._clock.now(),
                )
            )
            recent = self._uow.messages.recent(room, self._limits.join_history)
        return JoinResult(summarize(self._uow, self._clock, found), tuple(recent), self._protocol_text)

    def leave(self, agent: str, room: str, alias: str = "") -> None:
        require_room(self._uow, room)
        require_member(self._uow, room, agent, alias)
        self._uow.members.remove(room, agent, alias)

    def _ensure_role_exclusive(self, room: str, agent: str, alias: str, role: Role) -> None:
        opposite = {Role.WRITER: Role.REVIEWER, Role.REVIEWER: Role.WRITER}.get(role)
        for other in self._uow.members.list_for_room(room):
            if other.agent == agent and other.alias != alias and other.role is opposite:
                raise InvalidInput(
                    f"An agent cannot be both writer and reviewer in room '{room}'. "
                    f"You already joined as {other.role} ({display_name(agent, other.alias)}). "
                    "Join as observer instead, or leave that persona first with leave_room."
                )
