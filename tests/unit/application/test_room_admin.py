import pytest

from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import (
    GuardTripped,
    InvalidInput,
    RoomAlreadyExists,
    RoomNotActive,
    RoomNotFound,
)
from agent_rooms.domain.models import MessageKind, Role, RoomStatus
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


@pytest.fixture
def limits() -> Limits:
    return Limits(default_max_messages=3, poll_interval_s=0.01)


def test_create_room_returns_active_summary_with_default_cap(rooms: RoomAdmin) -> None:
    summary = rooms.create_room("alpha", "  Review the parser  ")
    assert summary.slug == "alpha"
    assert summary.topic == "Review the parser"
    assert summary.status is RoomStatus.ACTIVE
    assert summary.max_messages == 3
    assert summary.message_count == 0
    assert summary.members == ()


@pytest.mark.parametrize(("slug", "topic"), [("Bad Slug", "t"), ("", "t"), ("ok", "   ")])
def test_create_room_validates_input(rooms: RoomAdmin, slug: str, topic: str) -> None:
    with pytest.raises(InvalidInput):
        rooms.create_room(slug, topic)
    assert rooms.list_rooms() == []


def test_create_room_starts_with_budget_at_zero(rooms: RoomAdmin, uow: SqliteUnitOfWork) -> None:
    rooms.create_room("alpha", "t")
    room = uow.rooms.get("alpha")
    assert room is not None
    assert room.budget_start_id == 0


def test_duplicate_slug_is_rejected(rooms: RoomAdmin) -> None:
    rooms.create_room("alpha", "t")
    with pytest.raises(RoomAlreadyExists):
        rooms.create_room("alpha", "other")


def test_get_summary_of_unknown_room(rooms: RoomAdmin) -> None:
    with pytest.raises(RoomNotFound):
        rooms.get_summary("ghost")


def test_list_rooms_includes_members_listening_flags_and_message_count(
    rooms: RoomAdmin, membership: Membership, messaging: Messaging, listening: Listening
) -> None:
    rooms.create_room("alpha", "t")
    rooms.create_room("beta", "t")
    membership.join("claude", "alpha", Role.WRITER)
    membership.join("codex", "alpha", Role.REVIEWER, alias="strict")
    messaging.post("claude", "alpha", "hello")
    listening.listen("codex", "alpha", alias="strict")

    summaries = {s.slug: s for s in rooms.list_rooms()}

    assert list(summaries) == ["alpha", "beta"]
    alpha = summaries["alpha"]
    assert alpha.message_count == 1
    assert [(m.agent, m.alias, m.role, m.listening) for m in alpha.members] == [
        ("claude", "", Role.WRITER, False),
        ("codex", "strict", Role.REVIEWER, True),
    ]
    assert summaries["beta"].members == ()


def test_listening_flag_turns_off_when_the_listen_window_expires(
    rooms: RoomAdmin, membership: Membership, listening: Listening, clock
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    listening.listen("claude", "alpha", minutes=1)
    assert rooms.get_summary("alpha").members[0].listening is True
    clock.advance(61)
    assert rooms.get_summary("alpha").members[0].listening is False


@pytest.mark.parametrize(
    ("status", "notice"),
    [
        (RoomStatus.PAUSED, "Room paused by human."),
        (RoomStatus.CLOSED, "Room closed by human."),
    ],
)
def test_set_status_stores_the_exact_system_notice(
    rooms: RoomAdmin, uow: SqliteUnitOfWork, status: RoomStatus, notice: str
) -> None:
    rooms.create_room("alpha", "t")

    summary = rooms.set_status("alpha", status)

    assert summary.status is status
    (stored,) = uow.messages.recent("alpha", 10)
    assert (stored.sender, stored.alias, stored.kind, stored.text) == (
        "system",
        "",
        MessageKind.SYSTEM,
        notice,
    )
    assert summary.message_count == 1


def test_resume_stores_the_resumed_notice(rooms: RoomAdmin, uow: SqliteUnitOfWork) -> None:
    rooms.create_room("alpha", "t")
    rooms.set_status("alpha", RoomStatus.PAUSED)
    summary = rooms.set_status("alpha", RoomStatus.ACTIVE)
    assert summary.status is RoomStatus.ACTIVE
    assert uow.messages.recent("alpha", 1)[0].text == "Room resumed by human."


def test_same_status_is_a_no_op(rooms: RoomAdmin, uow: SqliteUnitOfWork) -> None:
    rooms.create_room("alpha", "t")
    summary = rooms.set_status("alpha", RoomStatus.ACTIVE)
    assert summary.status is RoomStatus.ACTIVE
    assert uow.messages.count("alpha") == 0


def test_set_status_of_unknown_room(rooms: RoomAdmin) -> None:
    with pytest.raises(RoomNotFound):
        rooms.set_status("ghost", RoomStatus.PAUSED)


def test_resume_gives_a_fresh_message_budget(
    rooms: RoomAdmin, membership: Membership, messaging: Messaging, uow: SqliteUnitOfWork
) -> None:
    rooms.create_room("alpha", "t")
    membership.join("claude", "alpha", Role.WRITER)
    for i in range(3):
        messaging.post("claude", "alpha", f"msg {i}")
    with pytest.raises(GuardTripped):
        messaging.post("claude", "alpha", "one too many")
    with pytest.raises(RoomNotActive):
        messaging.post("claude", "alpha", "still paused")

    last_before_resume = uow.messages.latest_id("alpha")
    rooms.set_status("alpha", RoomStatus.ACTIVE)

    room = uow.rooms.get("alpha")
    assert room is not None
    assert room.budget_start_id == last_before_resume
    for i in range(3):
        messaging.post("claude", "alpha", f"fresh {i}")
    with pytest.raises(GuardTripped):
        messaging.post("claude", "alpha", "over again")
