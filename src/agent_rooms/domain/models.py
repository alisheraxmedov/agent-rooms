import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from agent_rooms.domain.errors import InvalidInput

HUMAN = "human"
SYSTEM = "system"

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
_ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_TOPIC_MAX = 500
MAX_INT = 2**53


class RoomStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class Role(StrEnum):
    WRITER = "writer"
    REVIEWER = "reviewer"
    OBSERVER = "observer"


class MessageKind(StrEnum):
    CHAT = "chat"
    TASK = "task"
    REVIEW_REQUEST = "review_request"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    DONE = "done"
    SYSTEM = "system"


def validate_slug(slug: str) -> str:
    if not _SLUG.fullmatch(slug):
        raise InvalidInput(
            f"Invalid room slug {slug!r}: use 1-48 chars of lowercase letters, digits and '-', "
            "starting with a letter or digit."
        )
    return slug


def validate_alias(alias: str) -> str:
    if alias and not _ALIAS.fullmatch(alias):
        raise InvalidInput(
            f"Invalid alias {alias!r}: use 1-32 chars of lowercase letters, digits, '_' and '-', "
            "starting with a letter or digit, or leave it empty."
        )
    return alias


def validate_topic(topic: str) -> str:
    stripped = topic.strip()
    if not 1 <= len(stripped) <= _TOPIC_MAX:
        raise InvalidInput(f"Topic must be 1-{_TOPIC_MAX} characters.")
    return stripped


def validate_int(name: str, value: int) -> int:
    if not 0 <= value <= MAX_INT:
        raise InvalidInput(f"{name} must be an integer between 0 and {MAX_INT}.")
    return value


def validate_text(text: str, max_length: int) -> str:
    if not text.strip():
        raise InvalidInput("Text must not be blank.")
    if len(text) > max_length:
        raise InvalidInput(
            f"Text is {len(text)} characters; the limit is {max_length}. "
            "Send file paths and a commit SHA instead of pasted content."
        )
    return text


def _parse_enum[E: StrEnum](enum_type: type[E], label: str, value: str) -> E:
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        raise InvalidInput(f"Unknown {label} {value!r}. Allowed: {allowed}.") from None


def parse_role(value: str) -> Role:
    return _parse_enum(Role, "role", value)


def parse_kind(value: str) -> MessageKind:
    return _parse_enum(MessageKind, "kind", value)


def parse_status(value: str) -> RoomStatus:
    return _parse_enum(RoomStatus, "status", value)


def format_timestamp(moment: datetime) -> str:
    """Aware datetime -> UTC ISO-8601 with milliseconds and a Z suffix, e.g. 2026-10-27T10:00:00.123Z."""
    if moment.tzinfo is None:
        raise ValueError("format_timestamp needs a timezone-aware datetime.")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").removesuffix("+00:00") + "Z"


def display_name(agent: str, alias: str) -> str:
    return f"{agent}/{alias}" if alias else agent


@dataclass(frozen=True, slots=True)
class Room:
    slug: str
    topic: str
    status: RoomStatus
    max_messages: int
    budget_start_id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Member:
    room: str
    agent: str
    alias: str
    role: Role
    last_read_id: int
    listening_until: datetime | None
    listener_session: str | None
    joined_at: datetime

    def display_name(self) -> str:
        return display_name(self.agent, self.alias)

    def is_listening(self, now: datetime) -> bool:
        return self.listening_until is not None and self.listening_until > now


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    room: str
    sender: str
    alias: str
    kind: MessageKind
    text: str
    reply_to: int | None
    created_at: datetime

    def display_sender(self) -> str:
        return display_name(self.sender, self.alias)

    def is_from(self, agent: str, alias: str) -> bool:
        return self.sender == agent and self.alias == alias
