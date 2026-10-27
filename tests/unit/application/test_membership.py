import pytest

from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import InvalidInput, NotAMember, RoomNotFound
from agent_rooms.domain.models import Role, RoomStatus
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


def test_join_unknown_room(membership: Membership) -> None:
    with pytest.raises(RoomNotFound):
        membership.join("claude", "ghost", Role.WRITER)


def test_join_rejects_invalid_alias(membership: Membership, rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    with pytest.raises(InvalidInput):
        membership.join("claude", "alpha", Role.WRITER, alias="Bad Alias")


def test_join_returns_summary_and_protocol(
    membership: Membership, rooms: RoomAdmin, protocol_text: str
) -> None:
    rooms.create_room("alpha", "Parser")

    result = membership.join("claude", "alpha", Role.WRITER, alias="fixer")

    assert result.room.slug == "alpha"
    assert [(m.agent, m.alias, m.role) for m in result.room.members] == [("claude", "fixer", Role.WRITER)]
    assert result.recent == ()
    assert result.protocol == protocol_text


def test_join_returns_the_last_twenty_messages_ascending(
    membership: Membership, messaging: Messaging, rooms: RoomAdmin
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    for i in range(25):
        messaging.post("claude", "alpha", f"msg {i}")

    result = membership.join("codex", "alpha", Role.REVIEWER)

    assert [m.text for m in result.recent] == [f"msg {i}" for i in range(5, 25)]


def test_join_sets_the_cursor_to_the_latest_message(
    membership: Membership, messaging: Messaging, rooms: RoomAdmin
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    messaging.post("claude", "alpha", "before codex joined")

    membership.join("codex", "alpha", Role.REVIEWER)

    assert messaging.read("codex", "alpha") == []


def test_join_is_allowed_in_a_paused_room(membership: Membership, rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    rooms.set_status("alpha", RoomStatus.PAUSED)
    assert membership.join("claude", "alpha", Role.OBSERVER).room.status is RoomStatus.PAUSED


def test_rejoin_keeps_joined_at_updates_role_and_clears_listening(
    membership: Membership, rooms: RoomAdmin, listening: Listening, uow: SqliteUnitOfWork, clock
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    first = uow.members.get("alpha", "claude", "")
    assert first is not None
    listening.listen("claude", "alpha", session="ses_A")
    clock.advance(120)

    membership.join("claude", "alpha", Role.REVIEWER)

    again = uow.members.get("alpha", "claude", "")
    assert again is not None
    assert again.joined_at == first.joined_at
    assert again.role is Role.REVIEWER
    assert (again.listening_until, again.listener_session) == (None, None)
    assert len(uow.members.list_for_room("alpha")) == 1


def test_rejoin_moves_the_cursor_to_the_latest_message(
    membership: Membership, messaging: Messaging, rooms: RoomAdmin
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    membership.join("codex", "alpha", Role.REVIEWER)
    messaging.post("claude", "alpha", "missed while away")

    membership.join("codex", "alpha", Role.REVIEWER)

    assert messaging.read("codex", "alpha") == []


def test_leave_removes_the_membership(membership: Membership, rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)

    membership.leave("claude", "alpha")

    assert rooms.get_summary("alpha").members == ()


def test_leave_only_removes_the_given_alias(membership: Membership, rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    membership.join("claude", "alpha", Role.WRITER, alias="fixer")

    membership.leave("claude", "alpha", alias="fixer")

    assert [m.alias for m in rooms.get_summary("alpha").members] == [""]


def test_leave_when_not_a_member(membership: Membership, rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    with pytest.raises(NotAMember):
        membership.leave("claude", "alpha")


def test_leave_unknown_room(membership: Membership) -> None:
    with pytest.raises(RoomNotFound):
        membership.leave("claude", "ghost")
