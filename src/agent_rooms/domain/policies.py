from collections.abc import Iterable
from dataclasses import dataclass

from agent_rooms.domain.errors import InvalidInput, KindNotAllowed
from agent_rooms.domain.models import Member, MessageKind, Role, RoomStatus, display_name

ROLE_KINDS: dict[Role, frozenset[MessageKind]] = {
    Role.WRITER: frozenset(
        {MessageKind.CHAT, MessageKind.TASK, MessageKind.REVIEW_REQUEST, MessageKind.DONE}
    ),
    Role.REVIEWER: frozenset(
        {MessageKind.CHAT, MessageKind.TASK, MessageKind.CHANGES_REQUESTED, MessageKind.APPROVED}
    ),
    Role.OBSERVER: frozenset({MessageKind.CHAT}),
    Role.LEAD: frozenset({MessageKind.CHAT, MessageKind.TASK, MessageKind.DONE}),
}
HUMAN_KINDS: frozenset[MessageKind] = frozenset({MessageKind.CHAT, MessageKind.TASK})

_HUMAN_NOTICES = {
    RoomStatus.ACTIVE: "Room resumed by human.",
    RoomStatus.PAUSED: "Room paused by human.",
    RoomStatus.CLOSED: "Room closed by human.",
}


@dataclass(frozen=True, slots=True)
class GuardTrip:
    status: RoomStatus
    notice: str


def _kind_list(kinds: frozenset[MessageKind]) -> str:
    return ", ".join(sorted(kind.value for kind in kinds))


def ensure_kind_allowed(role: Role, kind: MessageKind) -> None:
    allowed = ROLE_KINDS[role]
    if kind not in allowed:
        raise KindNotAllowed(
            f"Role '{role}' may not post kind '{kind}'. Allowed kinds: {_kind_list(allowed)}."
        )


def ensure_human_kind(kind: MessageKind) -> None:
    if kind not in HUMAN_KINDS:
        raise KindNotAllowed(f"Humans may post only these kinds: {_kind_list(HUMAN_KINDS)}.")


def message_cap_trip(agent_posts_since_budget: int, max_messages: int) -> GuardTrip | None:
    if agent_posts_since_budget < max_messages:
        return None
    return GuardTrip(
        RoomStatus.PAUSED,
        f"Message limit reached ({max_messages} agent messages since the last resume). "
        "Room paused; a human must resume it.",
    )


def after_post_trip(
    kind: MessageKind, poster_display: str, changes_requested_since_budget: int, review_round_cap: int
) -> GuardTrip | None:
    if kind is MessageKind.DONE:
        return GuardTrip(RoomStatus.CLOSED, f"{poster_display} posted done. Room closed.")
    if kind is MessageKind.CHANGES_REQUESTED and changes_requested_since_budget >= review_round_cap:
        return GuardTrip(
            RoomStatus.PAUSED,
            f"Review-round limit reached ({review_round_cap} changes_requested since the last resume). "
            "Room paused; a human must resume it.",
        )
    return None


def status_change_notice(status: RoomStatus) -> str:
    return _HUMAN_NOTICES[status]


def ensure_single_lead(room: str, members: Iterable[Member], agent: str, alias: str) -> None:
    """A room has at most one lead identity (agent + alias); `agent`/`alias` may already be that lead."""
    for other in members:
        if other.role is Role.LEAD and not (other.agent == agent and other.alias == alias):
            raise InvalidInput(
                f"Room '{room}' already has a lead ({other.display_name()}). Ask the human to change roles."
            )


def ensure_role_exclusive(room: str, members: Iterable[Member], agent: str, alias: str, role: Role) -> None:
    """One agent identity cannot be both writer and reviewer in a room, across aliases."""
    opposite = {Role.WRITER: Role.REVIEWER, Role.REVIEWER: Role.WRITER}.get(role)
    for other in members:
        if other.agent == agent and other.alias != alias and other.role is opposite:
            raise InvalidInput(
                f"An agent cannot be both writer and reviewer in room '{room}'. "
                f"You already joined as {other.role} ({display_name(agent, other.alias)}). "
                "Join as observer instead, or leave that persona first with leave_room."
            )


def role_change_notice(display: str, old: Role, new: Role) -> str:
    return f"Human changed {display}'s role: {old} → {new}."
