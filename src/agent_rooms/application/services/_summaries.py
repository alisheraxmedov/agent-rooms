from agent_rooms.application.dto import MemberView, RoomSummary
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.domain.models import Room


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
