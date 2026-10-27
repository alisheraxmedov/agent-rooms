import pytest

from agent_rooms.domain.errors import KindNotAllowed
from agent_rooms.domain.models import MessageKind, Role, RoomStatus
from agent_rooms.domain.policies import (
    HUMAN_KINDS,
    ROLE_KINDS,
    GuardTrip,
    after_post_trip,
    ensure_human_kind,
    ensure_kind_allowed,
    message_cap_trip,
    status_change_notice,
)

K = MessageKind
EXPECTED = {
    Role.WRITER: {K.CHAT, K.TASK, K.REVIEW_REQUEST, K.DONE},
    Role.REVIEWER: {K.CHAT, K.TASK, K.CHANGES_REQUESTED, K.APPROVED},
    Role.OBSERVER: {K.CHAT},
}


def test_role_kinds_table_matches_spec() -> None:
    assert ROLE_KINDS == EXPECTED
    assert {K.CHAT, K.TASK} == HUMAN_KINDS


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("kind", list(MessageKind))
def test_ensure_kind_allowed_matrix(role: Role, kind: MessageKind) -> None:
    if kind in EXPECTED[role]:
        ensure_kind_allowed(role, kind)
    else:
        with pytest.raises(KindNotAllowed):
            ensure_kind_allowed(role, kind)


@pytest.mark.parametrize("role", list(Role))
def test_system_kind_is_never_allowed_for_agents(role: Role) -> None:
    with pytest.raises(KindNotAllowed):
        ensure_kind_allowed(role, K.SYSTEM)


def test_kind_not_allowed_message_lists_allowed_kinds() -> None:
    with pytest.raises(KindNotAllowed) as raised:
        ensure_kind_allowed(Role.OBSERVER, K.DONE)
    assert "observer" in str(raised.value)
    assert "chat" in str(raised.value)


@pytest.mark.parametrize("kind", list(MessageKind))
def test_ensure_human_kind_allows_only_chat_and_task(kind: MessageKind) -> None:
    if kind in {K.CHAT, K.TASK}:
        ensure_human_kind(kind)
    else:
        with pytest.raises(KindNotAllowed):
            ensure_human_kind(kind)


def test_message_cap_trip_below_cap_is_none() -> None:
    assert message_cap_trip(59, 60) is None


def test_message_cap_trip_at_cap_pauses_with_notice() -> None:
    assert message_cap_trip(60, 60) == GuardTrip(
        RoomStatus.PAUSED,
        "Message limit reached (60 agent messages since the last resume). "
        "Room paused; a human must resume it.",
    )


def test_done_closes_room() -> None:
    assert after_post_trip(K.DONE, "claude", 0, 5) == GuardTrip(
        RoomStatus.CLOSED, "claude posted done. Room closed."
    )


def test_done_notice_uses_display_name_with_alias() -> None:
    trip = after_post_trip(K.DONE, "claude/fixer", 0, 5)
    assert trip is not None
    assert trip.notice == "claude/fixer posted done. Room closed."


def test_changes_requested_below_cap_is_none() -> None:
    assert after_post_trip(K.CHANGES_REQUESTED, "codex", 4, 5) is None


def test_changes_requested_at_cap_pauses_with_notice() -> None:
    assert after_post_trip(K.CHANGES_REQUESTED, "codex", 5, 5) == GuardTrip(
        RoomStatus.PAUSED,
        "Review-round limit reached (5 changes_requested since the last resume). "
        "Room paused; a human must resume it.",
    )


@pytest.mark.parametrize("kind", [K.CHAT, K.TASK, K.REVIEW_REQUEST, K.APPROVED, K.SYSTEM])
def test_other_kinds_never_trip_after_post(kind: MessageKind) -> None:
    assert after_post_trip(kind, "claude", 99, 5) is None


def test_status_change_notices() -> None:
    assert status_change_notice(RoomStatus.ACTIVE) == "Room resumed by human."
    assert status_change_notice(RoomStatus.PAUSED) == "Room paused by human."
    assert status_change_notice(RoomStatus.CLOSED) == "Room closed by human."


def test_guard_trip_is_frozen() -> None:
    trip = GuardTrip(RoomStatus.PAUSED, "x")
    with pytest.raises(AttributeError):
        trip.notice = "y"  # type: ignore[misc]
