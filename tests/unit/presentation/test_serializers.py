from datetime import UTC, datetime, timedelta, timezone

from agent_rooms.application.dto import MemberView, RoomSummary, Wake
from agent_rooms.domain.models import Message, MessageKind, Role, RoomStatus
from agent_rooms.presentation.serializers import (
    iso,
    member_json,
    message_json,
    summary_json,
    wake_json,
)

MOMENT = datetime(2026, 10, 27, 10, 0, 0, 123000, tzinfo=UTC)


def make_message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "id": 7,
        "room": "r1",
        "sender": "claude",
        "alias": "a",
        "kind": MessageKind.REVIEW_REQUEST,
        "text": "please review",
        "reply_to": 3,
        "created_at": MOMENT,
    }
    return Message(**{**fields, **overrides})  # type: ignore[arg-type]


def test_iso_uses_milliseconds_and_z_suffix() -> None:
    assert iso(MOMENT) == "2026-10-27T10:00:00.123Z"


def test_iso_pads_missing_milliseconds() -> None:
    assert iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05.000Z"


def test_iso_converts_other_offsets_to_utc() -> None:
    tashkent = timezone(timedelta(hours=5))
    assert iso(datetime(2026, 10, 27, 15, 0, 0, tzinfo=tashkent)) == "2026-10-27T10:00:00.000Z"


def test_message_json_has_exactly_the_spec_keys() -> None:
    assert message_json(make_message()) == {
        "id": 7,
        "room": "r1",
        "sender": "claude",
        "alias": "a",
        "kind": "review_request",
        "text": "please review",
        "reply_to": 3,
        "created_at": "2026-10-27T10:00:00.123Z",
    }


def test_message_json_keeps_null_reply_to() -> None:
    assert message_json(make_message(reply_to=None))["reply_to"] is None


def test_member_json() -> None:
    assert member_json(MemberView("codex", "", Role.REVIEWER, True)) == {
        "agent": "codex",
        "alias": "",
        "role": "reviewer",
        "listening": True,
    }


def test_summary_json() -> None:
    summary = RoomSummary(
        slug="r1",
        topic="topic",
        status=RoomStatus.PAUSED,
        max_messages=60,
        message_count=4,
        members=(MemberView("claude", "", Role.WRITER, False),),
        created_at=MOMENT,
    )
    assert summary_json(summary) == {
        "slug": "r1",
        "topic": "topic",
        "status": "paused",
        "max_messages": 60,
        "message_count": 4,
        "members": [{"agent": "claude", "alias": "", "role": "writer", "listening": False}],
        "created_at": "2026-10-27T10:00:00.123Z",
    }


def test_wake_json() -> None:
    wake = Wake(room="r1", alias="a", messages=(make_message(),), prompt="hello")
    result = wake_json(wake)
    assert set(result) == {"room", "alias", "messages", "prompt"}
    assert result["room"] == "r1"
    assert result["alias"] == "a"
    assert result["prompt"] == "hello"
    assert result["messages"] == [message_json(make_message())]
