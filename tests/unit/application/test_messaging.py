import pytest

from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import (
    GuardTripped,
    InvalidInput,
    KindNotAllowed,
    NotAMember,
    RoomNotActive,
    RoomNotFound,
)
from agent_rooms.domain.models import MessageKind, Role, RoomStatus
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

CAP_NOTICE = (
    "Message limit reached (60 agent messages since the last resume). Room paused; a human must resume it."
)
REVIEW_NOTICE = (
    "Review-round limit reached (5 changes_requested since the last resume). "
    "Room paused; a human must resume it."
)


def status_of(uow: SqliteUnitOfWork, slug: str = "alpha") -> RoomStatus:
    room = uow.rooms.get(slug)
    assert room is not None
    return room.status


class TestPostCheckOrder:
    def test_successful_post(self, messaging: Messaging, alpha: str) -> None:
        result = messaging.post("claude", alpha, "hello", kind=MessageKind.TASK)
        assert result.message.id > 0
        assert (result.message.sender, result.message.alias, result.message.kind, result.message.text) == (
            "claude",
            "",
            MessageKind.TASK,
            "hello",
        )
        assert result.room_status is RoomStatus.ACTIVE
        assert result.notice is None

    def test_text_is_stored_as_given(self, messaging: Messaging, alpha: str) -> None:
        assert messaging.post("claude", alpha, "  padded \n").message.text == "  padded \n"

    def test_1_bad_alias(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput):
            messaging.post("claude", alpha, "hi", alias="Bad Alias")

    def test_1_alias_is_checked_before_the_room_exists(self, messaging: Messaging) -> None:
        with pytest.raises(InvalidInput):
            messaging.post("claude", "ghost", "hi", alias="Bad Alias")

    def test_2_missing_room(self, messaging: Messaging) -> None:
        with pytest.raises(RoomNotFound):
            messaging.post("claude", "ghost", "hi")

    def test_2_room_is_checked_before_membership(self, messaging: Messaging) -> None:
        with pytest.raises(RoomNotFound):
            messaging.post("stranger", "ghost", "hi")

    def test_3_not_a_member_with_actionable_message(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(NotAMember, match=r"Join room 'alpha' with join_room first\."):
            messaging.post("stranger", alpha, "hi")

    def test_3_the_alias_is_part_of_the_membership_identity(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(NotAMember):
            messaging.post("claude", alpha, "hi", alias="fixer")

    def test_3_membership_is_checked_before_room_status(
        self, messaging: Messaging, rooms: RoomAdmin, alpha: str
    ) -> None:
        rooms.set_status(alpha, RoomStatus.PAUSED)
        with pytest.raises(NotAMember):
            messaging.post("stranger", alpha, "hi")

    @pytest.mark.parametrize("status", [RoomStatus.PAUSED, RoomStatus.CLOSED])
    def test_4_inactive_room(
        self, messaging: Messaging, rooms: RoomAdmin, alpha: str, status: RoomStatus
    ) -> None:
        rooms.set_status(alpha, status)
        with pytest.raises(RoomNotActive, match=status.value):
            messaging.post("claude", alpha, "hi")

    def test_4_room_status_is_checked_before_kind(
        self, messaging: Messaging, rooms: RoomAdmin, alpha: str
    ) -> None:
        rooms.set_status(alpha, RoomStatus.PAUSED)
        with pytest.raises(RoomNotActive):
            messaging.post("claude", alpha, "hi", kind=MessageKind.APPROVED)

    def test_5_kind_not_allowed_for_role(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(KindNotAllowed):
            messaging.post("claude", alpha, "ship it", kind=MessageKind.APPROVED)
        with pytest.raises(KindNotAllowed):
            messaging.post("codex", alpha, "done?", kind=MessageKind.DONE)

    def test_5_system_kind_is_never_allowed(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(KindNotAllowed):
            messaging.post("claude", alpha, "fake notice", kind=MessageKind.SYSTEM)

    def test_5_kind_is_checked_before_text(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(KindNotAllowed):
            messaging.post("claude", alpha, "   ", kind=MessageKind.APPROVED)

    def test_6_blank_text(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput):
            messaging.post("claude", alpha, "  \n ")

    def test_6_text_over_the_limit(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput):
            messaging.post("claude", alpha, "x" * 8001)

    def test_6_text_at_the_limit_is_accepted(self, messaging: Messaging, alpha: str) -> None:
        assert len(messaging.post("claude", alpha, "x" * 8000).message.text) == 8000

    def test_6_text_is_checked_before_reply_to(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput, match="blank"):
            messaging.post("claude", alpha, " ", reply_to=999)

    def test_7_unknown_reply_to(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput, match="reply_to"):
            messaging.post("claude", alpha, "hi", reply_to=999)

    def test_7_reply_to_must_be_in_the_same_room(
        self, messaging: Messaging, rooms: RoomAdmin, membership: Membership, alpha: str
    ) -> None:
        rooms.create_room("beta", "other")
        membership.join("claude", "beta", Role.WRITER)
        other = messaging.post("claude", "beta", "elsewhere")
        with pytest.raises(InvalidInput, match="reply_to"):
            messaging.post("claude", alpha, "hi", reply_to=other.message.id)

    def test_7_valid_reply_to_is_stored(self, messaging: Messaging, alpha: str) -> None:
        first = messaging.post("claude", alpha, "question")
        reply = messaging.post("codex", alpha, "answer", reply_to=first.message.id)
        assert reply.message.reply_to == first.message.id

    def test_failed_validation_stores_nothing(
        self, messaging: Messaging, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        with pytest.raises(InvalidInput):
            messaging.post("claude", alpha, "x" * 8001)
        assert uow.messages.count(alpha) == 0


class TestMessageCap:
    def fill(self, messaging: Messaging, count: int) -> None:
        for i in range(count):
            sender = "claude" if i % 2 == 0 else "codex"
            messaging.post(sender, "alpha", f"msg {i}")

    def test_sixtieth_post_succeeds_and_sixty_first_trips(
        self, messaging: Messaging, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        self.fill(messaging, 60)
        assert status_of(uow) is RoomStatus.ACTIVE

        with pytest.raises(GuardTripped, match="Message limit reached") as excinfo:
            messaging.post("claude", alpha, "the 61st")

        assert str(excinfo.value) == CAP_NOTICE
        assert status_of(uow) is RoomStatus.PAUSED
        stored = uow.messages.recent(alpha, 100)
        assert len(stored) == 61
        assert (stored[-1].sender, stored[-1].kind, stored[-1].text) == (
            "system",
            MessageKind.SYSTEM,
            CAP_NOTICE,
        )
        assert "the 61st" not in [m.text for m in stored]

    def test_room_stays_paused_afterwards(self, messaging: Messaging, alpha: str) -> None:
        self.fill(messaging, 60)
        with pytest.raises(GuardTripped):
            messaging.post("claude", alpha, "the 61st")
        with pytest.raises(RoomNotActive):
            messaging.post("codex", alpha, "another")

    def test_human_and_system_messages_do_not_count(
        self, messaging: Messaging, rooms: RoomAdmin, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        self.fill(messaging, 59)
        for i in range(5):
            messaging.post_as_human(alpha, f"human {i}")

        assert messaging.post("claude", alpha, "the 60th agent post").room_status is RoomStatus.ACTIVE
        with pytest.raises(GuardTripped):
            messaging.post("claude", alpha, "the 61st agent post")

    def test_human_messages_alone_never_trip_the_cap(self, messaging: Messaging, alpha: str) -> None:
        for i in range(70):
            messaging.post_as_human(alpha, f"human {i}")
        assert messaging.post("claude", alpha, "agent can still post").room_status is RoomStatus.ACTIVE

    def test_human_messages_in_the_middle_do_not_use_up_budget(
        self, messaging: Messaging, alpha: str
    ) -> None:
        self.fill(messaging, 30)
        for i in range(40):
            messaging.post_as_human(alpha, f"human {i}")
        self.fill(messaging, 30)
        with pytest.raises(GuardTripped):
            messaging.post("claude", alpha, "the 61st agent post")


class TestReviewRoundCap:
    def request_changes(self, messaging: Messaging, n: int) -> list[object]:
        return [
            messaging.post("codex", "alpha", f"round {i}", kind=MessageKind.CHANGES_REQUESTED)
            for i in range(n)
        ]

    def test_fourth_changes_requested_keeps_the_room_active(self, messaging: Messaging, alpha: str) -> None:
        results = self.request_changes(messaging, 4)
        assert all(r.room_status is RoomStatus.ACTIVE for r in results)  # type: ignore[attr-defined]

    def test_fifth_is_stored_then_the_room_pauses(
        self, messaging: Messaging, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        self.request_changes(messaging, 4)

        result = messaging.post("codex", alpha, "round 5", kind=MessageKind.CHANGES_REQUESTED)

        assert result.room_status is RoomStatus.PAUSED
        assert result.notice == REVIEW_NOTICE
        assert status_of(uow) is RoomStatus.PAUSED
        stored = uow.messages.recent(alpha, 2)
        assert [(m.sender, m.kind, m.text) for m in stored] == [
            ("codex", MessageKind.CHANGES_REQUESTED, "round 5"),
            ("system", MessageKind.SYSTEM, REVIEW_NOTICE),
        ]

    def test_other_kinds_do_not_count_as_review_rounds(self, messaging: Messaging, alpha: str) -> None:
        for i in range(8):
            messaging.post("codex", alpha, f"chat {i}")
        assert messaging.post("codex", alpha, "round 1", kind=MessageKind.CHANGES_REQUESTED).notice is None

    def test_resume_restarts_the_review_count(
        self, messaging: Messaging, rooms: RoomAdmin, alpha: str
    ) -> None:
        self.request_changes(messaging, 5)
        rooms.set_status(alpha, RoomStatus.ACTIVE)
        assert messaging.post("codex", alpha, "again", kind=MessageKind.CHANGES_REQUESTED).notice is None


class TestDone:
    def test_done_is_stored_then_the_room_closes(
        self, messaging: Messaging, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        result = messaging.post("claude", alpha, "all merged", kind=MessageKind.DONE)

        assert result.room_status is RoomStatus.CLOSED
        assert result.notice == "claude posted done. Room closed."
        assert status_of(uow) is RoomStatus.CLOSED
        stored = uow.messages.recent(alpha, 2)
        assert [(m.sender, m.kind) for m in stored] == [
            ("claude", MessageKind.DONE),
            ("system", MessageKind.SYSTEM),
        ]
        assert stored[1].text == "claude posted done. Room closed."

    def test_notice_names_the_alias(self, messaging: Messaging, membership: Membership, alpha: str) -> None:
        membership.join("claude", alpha, Role.WRITER, alias="fixer")
        result = messaging.post("claude", alpha, "done", kind=MessageKind.DONE, alias="fixer")
        assert result.notice == "claude/fixer posted done. Room closed."

    def test_closed_room_rejects_further_posts(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("claude", alpha, "done", kind=MessageKind.DONE)
        with pytest.raises(RoomNotActive):
            messaging.post("codex", alpha, "wait")


class TestPostAsHuman:
    def test_stores_a_human_message(self, messaging: Messaging, alpha: str) -> None:
        message = messaging.post_as_human(alpha, "please focus on parser.py", kind=MessageKind.TASK)
        assert (message.sender, message.alias, message.kind) == ("human", "", MessageKind.TASK)
        assert message.text == "please focus on parser.py"

    def test_allowed_in_a_paused_room(self, messaging: Messaging, rooms: RoomAdmin, alpha: str) -> None:
        rooms.set_status(alpha, RoomStatus.PAUSED)
        assert messaging.post_as_human(alpha, "hold on").kind is MessageKind.CHAT

    def test_rejected_in_a_closed_room(self, messaging: Messaging, rooms: RoomAdmin, alpha: str) -> None:
        rooms.set_status(alpha, RoomStatus.CLOSED)
        with pytest.raises(RoomNotActive):
            messaging.post_as_human(alpha, "hello?")

    def test_unknown_room(self, messaging: Messaging) -> None:
        with pytest.raises(RoomNotFound):
            messaging.post_as_human("ghost", "hello")

    @pytest.mark.parametrize(
        "kind",
        [
            MessageKind.REVIEW_REQUEST,
            MessageKind.CHANGES_REQUESTED,
            MessageKind.APPROVED,
            MessageKind.DONE,
            MessageKind.SYSTEM,
        ],
    )
    def test_only_chat_and_task_kinds(self, messaging: Messaging, alpha: str, kind: MessageKind) -> None:
        with pytest.raises(KindNotAllowed):
            messaging.post_as_human(alpha, "x", kind=kind)

    def test_validates_text(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(InvalidInput):
            messaging.post_as_human(alpha, "   ")
        with pytest.raises(InvalidInput):
            messaging.post_as_human(alpha, "x" * 8001)

    def test_has_no_guards(self, messaging: Messaging, alpha: str, uow: SqliteUnitOfWork) -> None:
        for i in range(65):
            messaging.post_as_human(alpha, f"human {i}")
        assert status_of(uow) is RoomStatus.ACTIVE


class TestRead:
    def test_returns_only_other_senders_unread_messages(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("claude", alpha, "mine")
        messaging.post("codex", alpha, "from codex")
        messaging.post_as_human(alpha, "from human")

        assert [m.text for m in messaging.read("claude", alpha)] == ["from codex", "from human"]

    def test_includes_system_notices(self, messaging: Messaging, rooms: RoomAdmin, alpha: str) -> None:
        rooms.set_status(alpha, RoomStatus.PAUSED)
        assert [m.text for m in messaging.read("claude", alpha)] == ["Room paused by human."]

    def test_advances_the_cursor(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("codex", alpha, "one")
        assert len(messaging.read("claude", alpha)) == 1
        assert messaging.read("claude", alpha) == []
        messaging.post("codex", alpha, "two")
        assert [m.text for m in messaging.read("claude", alpha)] == ["two"]

    def test_limit_returns_the_oldest_unread_first_and_keeps_the_rest(
        self, messaging: Messaging, alpha: str
    ) -> None:
        for i in range(5):
            messaging.post("codex", alpha, f"m{i}")
        assert [m.text for m in messaging.read("claude", alpha, limit=2)] == ["m0", "m1"]
        assert [m.text for m in messaging.read("claude", alpha, limit=10)] == ["m2", "m3", "m4"]

    def test_limit_is_clamped_to_at_least_one(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("codex", alpha, "one")
        messaging.post("codex", alpha, "two")
        assert len(messaging.read("claude", alpha, limit=0)) == 1

    def test_limit_is_clamped_to_the_maximum(self, messaging: Messaging, alpha: str) -> None:
        for i in range(210):
            messaging.post_as_human(alpha, f"h{i}")
        assert len(messaging.read("claude", alpha, limit=10_000)) == 200

    def test_aliases_are_isolated_identities(
        self, messaging: Messaging, membership: Membership, alpha: str
    ) -> None:
        membership.join("claude", alpha, Role.WRITER, alias="fixer")
        messaging.post("claude", alpha, "from main")
        messaging.post("claude", alpha, "from fixer", alias="fixer")

        assert [m.text for m in messaging.read("claude", alpha)] == ["from fixer"]
        assert [m.text for m in messaging.read("claude", alpha, alias="fixer")] == ["from main"]

    def test_works_in_closed_rooms(self, messaging: Messaging, rooms: RoomAdmin, alpha: str) -> None:
        messaging.post("codex", alpha, "last words")
        rooms.set_status(alpha, RoomStatus.CLOSED)
        assert [m.text for m in messaging.read("claude", alpha)] == ["last words", "Room closed by human."]

    def test_unknown_room(self, messaging: Messaging) -> None:
        with pytest.raises(RoomNotFound):
            messaging.read("claude", "ghost")

    def test_not_a_member(self, messaging: Messaging, alpha: str) -> None:
        with pytest.raises(NotAMember):
            messaging.read("stranger", alpha)


class TestHistory:
    def test_pages_by_after_id_and_limit(self, messaging: Messaging, alpha: str) -> None:
        ids = [messaging.post("claude", alpha, f"m{i}").message.id for i in range(5)]

        assert [m.id for m in messaging.history(alpha)] == ids
        assert [m.id for m in messaging.history(alpha, after_id=ids[1])] == ids[2:]
        assert [m.id for m in messaging.history(alpha, after_id=ids[1], limit=2)] == ids[2:4]

    def test_includes_every_sender_and_does_not_move_cursors(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("claude", alpha, "own")
        messaging.post_as_human(alpha, "human")
        assert [m.text for m in messaging.history(alpha)] == ["own", "human"]
        assert [m.text for m in messaging.read("claude", alpha)] == ["human"]

    def test_limit_is_clamped(self, messaging: Messaging, alpha: str) -> None:
        messaging.post("claude", alpha, "one")
        messaging.post("claude", alpha, "two")
        assert len(messaging.history(alpha, limit=0)) == 1

    def test_unknown_room(self, messaging: Messaging) -> None:
        with pytest.raises(RoomNotFound):
            messaging.history("ghost")


class TestIntegerBounds:
    @pytest.mark.parametrize("value", [-1, 2**53 + 1, 2**63, 10**30])
    def test_reply_to_out_of_range_is_invalid_input(
        self, messaging: Messaging, alpha: str, value: int
    ) -> None:
        with pytest.raises(InvalidInput, match="reply_to"):
            messaging.post("claude", alpha, "hi", reply_to=value)

    @pytest.mark.parametrize("value", [-1, 2**63])
    def test_read_limit_out_of_range_is_invalid_input(
        self, messaging: Messaging, alpha: str, value: int
    ) -> None:
        with pytest.raises(InvalidInput, match="limit"):
            messaging.read("claude", alpha, limit=value)

    @pytest.mark.parametrize("value", [-1, 2**63])
    def test_history_bounds(self, messaging: Messaging, alpha: str, value: int) -> None:
        with pytest.raises(InvalidInput, match="after"):
            messaging.history(alpha, after_id=value)
        with pytest.raises(InvalidInput, match="limit"):
            messaging.history(alpha, limit=value)

    def test_zero_and_huge_but_valid_values_are_clamped_not_rejected(
        self, messaging: Messaging, alpha: str
    ) -> None:
        assert messaging.read("claude", alpha, limit=0) == []
        assert messaging.read("claude", alpha, limit=2**53) == []
        assert messaging.history(alpha, after_id=2**53, limit=2**53) == []
