from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.domain.models import SYSTEM, MessageKind, RoomStatus


def change_room_status(uow: UnitOfWork, clock: Clock, slug: str, status: RoomStatus, notice: str) -> None:
    """Moves a room to `status` and stores the system notice. Runs inside the caller's transaction.

    A room that is not active has no listeners: every member's listen window is released with it.
    """
    if status is not RoomStatus.ACTIVE:
        uow.members.clear_listening_in_room(slug)
    uow.rooms.set_status(slug, status)
    uow.messages.add(
        room=slug,
        sender=SYSTEM,
        alias="",
        kind=MessageKind.SYSTEM,
        text=notice,
        reply_to=None,
        created_at=clock.now(),
    )
