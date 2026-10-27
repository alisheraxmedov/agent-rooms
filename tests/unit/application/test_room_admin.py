import pytest
from fakes import FakeClock

from agent_rooms.application.limits import Limits
from agent_rooms.application.services._summaries import summarize
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import (
    GuardTripped,
    InvalidInput,
    KindNotAllowed,
    NotAMember,
    RoomAlreadyExists,
    RoomNotActive,
    RoomNotClosed,
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


@pytest.mark.parametrize("status", [RoomStatus.PAUSED, RoomStatus.CLOSED])
def test_pausing_or_closing_releases_every_listener(
    rooms: RoomAdmin,
    membership: Membership,
    listening: Listening,
    uow: SqliteUnitOfWork,
    alpha: str,
    status: RoomStatus,
) -> None:
    membership.join("claude", alpha, Role.WRITER, alias="fixer")
    listening.listen("claude", alpha, session="ses_A")
    listening.listen("claude", alpha, alias="fixer")
    listening.listen("codex", alpha)

    summary = rooms.set_status(alpha, status)

    assert [m.listening for m in summary.members] == [False, False, False]
    for member in uow.members.list_for_room(alpha):
        assert (member.listening_until, member.listener_session) == (None, None)


def test_resuming_does_not_make_anybody_listen_again(
    rooms: RoomAdmin, listening: Listening, alpha: str
) -> None:
    listening.listen("codex", alpha)
    rooms.set_status(alpha, RoomStatus.PAUSED)

    summary = rooms.set_status(alpha, RoomStatus.ACTIVE)

    assert [m.listening for m in summary.members] == [False, False]


def test_delete_room_removes_a_closed_room_with_its_members_and_messages(
    rooms: RoomAdmin, messaging: Messaging, uow: SqliteUnitOfWork, alpha: str
) -> None:
    messaging.post("claude", alpha, "hello")
    rooms.set_status(alpha, RoomStatus.CLOSED)

    rooms.delete_room(alpha)

    assert uow.rooms.get(alpha) is None
    assert uow.members.list_for_room(alpha) == []
    assert uow.messages.count(alpha) == 0


@pytest.mark.parametrize("status", [RoomStatus.ACTIVE, RoomStatus.PAUSED])
def test_delete_room_refuses_rooms_that_are_not_closed(
    rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str, status: RoomStatus
) -> None:
    rooms.set_status(alpha, status)

    with pytest.raises(RoomNotClosed, match="Close the room first"):
        rooms.delete_room(alpha)

    assert uow.rooms.get(alpha) is not None


def test_delete_room_of_unknown_room(rooms: RoomAdmin) -> None:
    with pytest.raises(RoomNotFound):
        rooms.delete_room("ghost")


def test_summarize_lives_in_the_shared_summaries_module(
    uow: SqliteUnitOfWork, clock: FakeClock, alpha: str, messaging: Messaging
) -> None:
    messaging.post("claude", alpha, "hello")
    room = uow.rooms.get(alpha)
    assert room is not None

    summary = summarize(uow, clock, room)

    assert (summary.slug, summary.message_count) == (alpha, 1)
    assert [(m.agent, m.listening) for m in summary.members] == [("claude", False), ("codex", False)]


def _roles(uow: SqliteUnitOfWork, room: str) -> dict[tuple[str, str], Role]:
    return {(m.agent, m.alias): m.role for m in uow.members.list_for_room(room)}


def _notices(uow: SqliteUnitOfWork, room: str) -> list[str]:
    return [m.text for m in uow.messages.recent(room, 50) if m.kind is MessageKind.SYSTEM]


class TestSetMemberRole:
    def test_changes_the_role_and_stores_the_exact_system_notice(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        summary = rooms.set_member_role(alpha, "claude", "", Role.OBSERVER)

        assert {(m.agent, m.role) for m in summary.members} == {
            ("claude", Role.OBSERVER),
            ("codex", Role.REVIEWER),
        }
        (stored,) = uow.messages.recent(alpha, 10)
        assert (stored.sender, stored.alias, stored.kind) == ("system", "", MessageKind.SYSTEM)
        assert stored.text == "Human changed claude's role: writer → observer."
        assert summary.message_count == 1

    def test_notice_uses_the_display_name_with_alias(
        self, rooms: RoomAdmin, membership: Membership, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        membership.join("opencode", alpha, Role.OBSERVER, alias="watcher")

        rooms.set_member_role(alpha, "opencode", "watcher", Role.REVIEWER)

        assert _notices(uow, alpha) == ["Human changed opencode/watcher's role: observer → reviewer."]

    def test_same_role_is_a_no_op_without_a_message(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        summary = rooms.set_member_role(alpha, "claude", "", Role.WRITER)

        assert summary.message_count == 0
        assert uow.messages.count(alpha) == 0

    def test_unknown_room(self, rooms: RoomAdmin) -> None:
        with pytest.raises(RoomNotFound):
            rooms.set_member_role("ghost", "claude", "", Role.LEAD)

    def test_not_a_member(self, rooms: RoomAdmin, alpha: str) -> None:
        with pytest.raises(NotAMember, match="join_room"):
            rooms.set_member_role(alpha, "stranger", "", Role.LEAD)

    def test_the_alias_must_match(self, rooms: RoomAdmin, alpha: str) -> None:
        with pytest.raises(NotAMember):
            rooms.set_member_role(alpha, "claude", "fixer", Role.LEAD)

    def test_assigning_lead_to_a_member_when_nobody_leads(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        assert _roles(uow, alpha)[("claude", "")] is Role.LEAD
        assert _notices(uow, alpha) == ["Human changed claude's role: writer → lead."]

    def test_assigning_lead_demotes_the_old_lead_to_observer_with_its_own_notice(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        summary = rooms.set_member_role(alpha, "codex", "", Role.LEAD)

        assert _roles(uow, alpha) == {("claude", ""): Role.OBSERVER, ("codex", ""): Role.LEAD}
        assert [m.role for m in summary.members] == [Role.OBSERVER, Role.LEAD]
        assert _notices(uow, alpha)[1:] == [
            "Human changed claude's role: lead → observer.",
            "Human changed codex's role: reviewer → lead.",
        ]
        assert summary.message_count == 3

    def test_reassigning_lead_to_the_current_lead_changes_nothing(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        assert uow.messages.count(alpha) == 1

    def test_demoting_the_lead_directly_leaves_the_room_without_a_lead(
        self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        rooms.set_member_role(alpha, "claude", "", Role.WRITER)

        assert _roles(uow, alpha)[("claude", "")] is Role.WRITER
        assert _notices(uow, alpha)[-1] == "Human changed claude's role: lead → writer."

    def test_writer_reviewer_exclusivity_is_enforced(
        self, rooms: RoomAdmin, membership: Membership, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        membership.join("codex", alpha, Role.OBSERVER, alias="watcher")

        with pytest.raises(InvalidInput, match="cannot be both writer and reviewer in room 'alpha'"):
            rooms.set_member_role(alpha, "codex", "watcher", Role.WRITER)

        assert _roles(uow, alpha)[("codex", "watcher")] is Role.OBSERVER
        assert uow.messages.count(alpha) == 0

    def test_a_rejected_change_stores_no_partial_demotion(
        self, rooms: RoomAdmin, membership: Membership, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)
        before = uow.messages.count(alpha)
        membership.join("codex", alpha, Role.OBSERVER, alias="watcher")

        with pytest.raises(InvalidInput):
            rooms.set_member_role(alpha, "codex", "watcher", Role.WRITER)

        assert _roles(uow, alpha)[("claude", "")] is Role.LEAD
        assert uow.messages.count(alpha) == before

    def test_listening_state_and_cursor_are_untouched(
        self, rooms: RoomAdmin, listening: Listening, uow: SqliteUnitOfWork, alpha: str
    ) -> None:
        listening.listen("claude", alpha, session="ses_A")
        before = uow.members.get(alpha, "claude", "")
        assert before is not None

        summary = rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        after = uow.members.get(alpha, "claude", "")
        assert after is not None
        assert (after.listening_until, after.listener_session, after.last_read_id, after.joined_at) == (
            before.listening_until,
            before.listener_session,
            before.last_read_id,
            before.joined_at,
        )
        assert [m.listening for m in summary.members] == [True, False]

    def test_works_in_a_paused_room(self, rooms: RoomAdmin, uow: SqliteUnitOfWork, alpha: str) -> None:
        rooms.set_status(alpha, RoomStatus.PAUSED)

        rooms.set_member_role(alpha, "claude", "", Role.OBSERVER)

        assert _roles(uow, alpha)[("claude", "")] is Role.OBSERVER

    def test_the_new_role_governs_what_the_member_may_post(
        self, rooms: RoomAdmin, messaging: Messaging, alpha: str
    ) -> None:
        with pytest.raises(KindNotAllowed):
            messaging.post("claude", alpha, "all done", MessageKind.APPROVED)
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        messaging.post("claude", alpha, "task for the team", MessageKind.TASK)
        with pytest.raises(KindNotAllowed):
            messaging.post("claude", alpha, "please review", MessageKind.REVIEW_REQUEST)

    def test_the_lead_can_close_the_room_with_done(
        self, rooms: RoomAdmin, messaging: Messaging, alpha: str
    ) -> None:
        rooms.set_member_role(alpha, "claude", "", Role.LEAD)

        result = messaging.post("claude", alpha, "ship it", MessageKind.DONE)

        assert result.room_status is RoomStatus.CLOSED
