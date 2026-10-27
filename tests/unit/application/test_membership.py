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


class TestRoleExclusivity:
    def test_one_agent_cannot_be_writer_and_reviewer_across_aliases(
        self, membership: Membership, rooms: RoomAdmin, uow: SqliteUnitOfWork
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("codex", "alpha", Role.REVIEWER)

        with pytest.raises(InvalidInput, match="cannot be both writer and reviewer in room 'alpha'"):
            membership.join("codex", "alpha", Role.WRITER, alias="sneaky")

        assert [m.alias for m in uow.members.list_for_room("alpha")] == [""]

    def test_it_works_in_the_other_direction_too(self, membership: Membership, rooms: RoomAdmin) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", Role.WRITER, alias="one")

        with pytest.raises(InvalidInput, match="cannot be both writer and reviewer"):
            membership.join("claude", "alpha", Role.REVIEWER, alias="two")

    def test_switching_the_role_of_a_single_persona_is_allowed(
        self, membership: Membership, rooms: RoomAdmin
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", Role.WRITER)

        membership.join("claude", "alpha", Role.REVIEWER)

    def test_switching_one_persona_into_conflict_with_another_is_rejected(
        self, membership: Membership, rooms: RoomAdmin
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", Role.WRITER, alias="a")
        membership.join("claude", "alpha", Role.WRITER, alias="b")

        with pytest.raises(InvalidInput):
            membership.join("claude", "alpha", Role.REVIEWER, alias="a")

    @pytest.mark.parametrize("other", [Role.WRITER, Role.REVIEWER, Role.OBSERVER])
    def test_observer_combines_with_anything(
        self, membership: Membership, rooms: RoomAdmin, other: Role
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", other)

        membership.join("claude", "alpha", Role.OBSERVER, alias="watcher")

        assert len(rooms.get_summary("alpha").members) == 2

    def test_two_writers_under_different_aliases_are_fine(
        self, membership: Membership, rooms: RoomAdmin
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", Role.WRITER, alias="a")
        membership.join("claude", "alpha", Role.WRITER, alias="b")

    def test_the_restriction_is_per_room(self, membership: Membership, rooms: RoomAdmin) -> None:
        rooms.create_room("alpha", "t")
        rooms.create_room("beta", "t")
        membership.join("claude", "alpha", Role.WRITER)

        membership.join("claude", "beta", Role.REVIEWER)

    def test_leaving_the_other_persona_lifts_the_restriction(
        self, membership: Membership, rooms: RoomAdmin
    ) -> None:
        rooms.create_room("alpha", "t")
        membership.join("claude", "alpha", Role.WRITER)
        membership.leave("claude", "alpha")

        membership.join("claude", "alpha", Role.REVIEWER, alias="now-reviewer")
