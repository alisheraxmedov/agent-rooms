from agent_rooms.application.ports import UnitOfWork
from agent_rooms.domain.errors import NotAMember, RoomNotFound
from agent_rooms.domain.models import Member, Room


def require_room(uow: UnitOfWork, slug: str) -> Room:
    room = uow.rooms.get(slug)
    if room is None:
        raise RoomNotFound(f"Room '{slug}' does not exist. Use list_rooms to see the available rooms.")
    return room


def require_member(uow: UnitOfWork, room: str, agent: str, alias: str) -> Member:
    member = uow.members.get(room, agent, alias)
    if member is None:
        raise NotAMember(f"Join room '{room}' with join_room first.")
    return member
