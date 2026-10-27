import pytest

from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import GuardTripped
from agent_rooms.domain.models import MessageKind, RoomStatus
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


@pytest.fixture
def limits() -> Limits:
    return Limits(default_max_messages=3, review_round_cap=2, poll_interval_s=0.01)


def listening_flags(rooms: RoomAdmin, slug: str) -> dict[str, bool]:
    return {m.agent: m.listening for m in rooms.get_summary(slug).members}


def status_of(uow: SqliteUnitOfWork, slug: str) -> RoomStatus:
    room = uow.rooms.get(slug)
    assert room is not None
    return room.status


class TestGuardDrivenTransitionsReleaseListeners:
    def test_done_closes_the_room_and_clears_the_listening_reviewer(
        self, messaging: Messaging, listening: Listening, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        listening.listen("codex", alpha, session="ses_A")
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": True}

        messaging.post("claude", alpha, "all merged", kind=MessageKind.DONE)

        assert status_of(uow, alpha) is RoomStatus.CLOSED
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": False}
        codex = uow.members.get(alpha, "codex", "")
        assert codex is not None
        assert (codex.listening_until, codex.listener_session) == (None, None)

    def test_message_cap_trip_pauses_the_room_and_clears_every_listener(
        self, messaging: Messaging, listening: Listening, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        for i in range(3):
            messaging.post("claude", alpha, f"msg {i}")
        listening.listen("codex", alpha)
        listening.listen("claude", alpha)

        with pytest.raises(GuardTripped):
            messaging.post("claude", alpha, "one too many")

        assert status_of(uow, alpha) is RoomStatus.PAUSED
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": False}

    def test_review_round_trip_pauses_the_room_and_clears_every_listener(
        self, messaging: Messaging, listening: Listening, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        messaging.post("codex", alpha, "round 1", kind=MessageKind.CHANGES_REQUESTED)
        listening.listen("claude", alpha)
        assert listening_flags(rooms, alpha)["claude"] is True

        result = messaging.post("codex", alpha, "round 2", kind=MessageKind.CHANGES_REQUESTED)

        assert result.room_status is RoomStatus.PAUSED
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": False}

    def test_a_post_that_trips_nothing_leaves_listeners_alone(
        self, messaging: Messaging, listening: Listening, rooms: RoomAdmin, alpha: str
    ) -> None:
        listening.listen("codex", alpha)
        messaging.post("claude", alpha, "just chatting")
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": True}

    def test_the_system_notice_is_still_stored_once_per_transition(
        self, messaging: Messaging, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        messaging.post("claude", alpha, "all merged", kind=MessageKind.DONE)
        notices = [m.text for m in uow.messages.recent(alpha, 10) if m.sender == "system"]
        assert notices == ["claude posted done. Room closed."]


class TestSummaryNeverShowsStaleListeners:
    @pytest.mark.parametrize("status", [RoomStatus.PAUSED, RoomStatus.CLOSED])
    def test_a_legacy_row_with_a_future_listen_window_is_not_reported_in_a_non_active_room(
        self,
        rooms: RoomAdmin,
        listening: Listening,
        uow: SqliteUnitOfWork,
        alpha: str,
        status: RoomStatus,
    ) -> None:
        listening.listen("codex", alpha)
        rooms.set_status(alpha, status)
        # a row written before the release logic existed: listener set although the room is not active
        codex = uow.members.get(alpha, "codex", "")
        assert codex is not None
        uow.members.set_listening(alpha, "codex", "", codex.joined_at.replace(year=2999), "ses_old")

        assert listening_flags(rooms, alpha) == {"claude": False, "codex": False}
        assert [s.slug for s in rooms.list_rooms()] == [alpha]
        assert all(not m.listening for m in rooms.list_rooms()[0].members)

    def test_active_rooms_still_report_listening(
        self, rooms: RoomAdmin, listening: Listening, alpha: str
    ) -> None:
        listening.listen("codex", alpha)
        assert listening_flags(rooms, alpha) == {"claude": False, "codex": True}
