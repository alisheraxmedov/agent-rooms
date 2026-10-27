from datetime import UTC, datetime, timedelta

import pytest

from agent_rooms.domain.errors import (
    DomainError,
    GuardTripped,
    InvalidInput,
    KindNotAllowed,
    NotAMember,
    RoomAlreadyExists,
    RoomNotActive,
    RoomNotFound,
)
from agent_rooms.domain.models import (
    HUMAN,
    SYSTEM,
    Member,
    Message,
    MessageKind,
    Role,
    RoomStatus,
    display_name,
    parse_kind,
    parse_role,
    parse_status,
    validate_alias,
    validate_slug,
    validate_text,
    validate_topic,
)

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def make_member(**overrides: object) -> Member:
    fields: dict[str, object] = {
        "room": "demo",
        "agent": "claude",
        "alias": "",
        "role": Role.WRITER,
        "last_read_id": 0,
        "listening_until": None,
        "listener_session": None,
        "joined_at": NOW,
    }
    fields.update(overrides)
    return Member(**fields)  # type: ignore[arg-type]


def make_message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "id": 1,
        "room": "demo",
        "sender": "claude",
        "alias": "",
        "kind": MessageKind.CHAT,
        "text": "hi",
        "reply_to": None,
        "created_at": NOW,
    }
    fields.update(overrides)
    return Message(**fields)  # type: ignore[arg-type]


def test_enum_values() -> None:
    assert [s.value for s in RoomStatus] == ["active", "paused", "closed"]
    assert [r.value for r in Role] == ["writer", "reviewer", "observer"]
    assert [k.value for k in MessageKind] == [
        "chat",
        "task",
        "review_request",
        "changes_requested",
        "approved",
        "done",
        "system",
    ]


def test_reserved_identities() -> None:
    assert HUMAN == "human"
    assert SYSTEM == "system"


@pytest.mark.parametrize("slug", ["a", "0", "demo-room", "a" * 48])
def test_validate_slug_accepts_valid_edges(slug: str) -> None:
    assert validate_slug(slug) == slug


@pytest.mark.parametrize("slug", ["", "-a", "A", "a" * 49, "a_b", "a b", "a.b"])
def test_validate_slug_rejects_invalid_edges(slug: str) -> None:
    with pytest.raises(InvalidInput):
        validate_slug(slug)


@pytest.mark.parametrize("alias", ["", "a", "fixer_1-x", "a" * 32])
def test_validate_alias_accepts_valid_edges(alias: str) -> None:
    assert validate_alias(alias) == alias


@pytest.mark.parametrize("alias", ["-a", "_a", "A", "a" * 33, "bad alias"])
def test_validate_alias_rejects_invalid_edges(alias: str) -> None:
    with pytest.raises(InvalidInput):
        validate_alias(alias)


def test_validate_topic_strips_and_returns() -> None:
    assert validate_topic("  Fix the bug \n") == "Fix the bug"
    assert validate_topic("t" * 500) == "t" * 500


@pytest.mark.parametrize("topic", ["", "   \n", "t" * 501])
def test_validate_topic_rejects_blank_and_too_long(topic: str) -> None:
    with pytest.raises(InvalidInput):
        validate_topic(topic)


def test_validate_text_accepts_edge_and_keeps_raw_value() -> None:
    assert validate_text(" hi \n", 8000) == " hi \n"
    assert validate_text("x" * 8000, 8000) == "x" * 8000


@pytest.mark.parametrize("text", ["", "  \n\t", "x" * 8001])
def test_validate_text_rejects_blank_and_too_long(text: str) -> None:
    with pytest.raises(InvalidInput):
        validate_text(text, 8000)


def test_parse_enums_accept_values() -> None:
    assert parse_role("reviewer") is Role.REVIEWER
    assert parse_kind("changes_requested") is MessageKind.CHANGES_REQUESTED
    assert parse_status("paused") is RoomStatus.PAUSED


@pytest.mark.parametrize("parser", [parse_role, parse_kind, parse_status])
def test_parse_enums_reject_unknown_value(parser: object) -> None:
    with pytest.raises(InvalidInput) as raised:
        parser("admin")  # type: ignore[operator]
    assert "admin" in str(raised.value)


def test_display_name() -> None:
    assert display_name("claude", "") == "claude"
    assert display_name("claude", "fixer") == "claude/fixer"


def test_member_display_name() -> None:
    assert make_member(alias="fixer").display_name() == "claude/fixer"
    assert make_member().display_name() == "claude"


def test_member_is_listening_only_while_deadline_is_in_the_future() -> None:
    assert not make_member().is_listening(NOW)
    assert make_member(listening_until=NOW + timedelta(seconds=1)).is_listening(NOW)
    assert not make_member(listening_until=NOW).is_listening(NOW)
    assert not make_member(listening_until=NOW - timedelta(seconds=1)).is_listening(NOW)


def test_message_display_sender() -> None:
    assert make_message(alias="fixer").display_sender() == "claude/fixer"
    assert make_message().display_sender() == "claude"


def test_message_is_from_matches_agent_and_alias() -> None:
    message = make_message(alias="fixer")
    assert message.is_from("claude", "fixer")
    assert not message.is_from("claude", "")
    assert not message.is_from("codex", "fixer")


def test_entities_are_frozen_and_slotted() -> None:
    member = make_member()
    with pytest.raises(AttributeError):
        member.agent = "codex"  # type: ignore[misc]
    assert not hasattr(member, "__dict__")


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (InvalidInput, "invalid_input"),
        (RoomNotFound, "room_not_found"),
        (RoomAlreadyExists, "room_exists"),
        (RoomNotActive, "room_not_active"),
        (NotAMember, "not_a_member"),
        (KindNotAllowed, "kind_not_allowed"),
        (GuardTripped, "guard_tripped"),
    ],
)
def test_error_codes(error: type[DomainError], code: str) -> None:
    assert error.code == code
    assert issubclass(error, DomainError)
    assert str(error("something actionable")) == "something actionable"
