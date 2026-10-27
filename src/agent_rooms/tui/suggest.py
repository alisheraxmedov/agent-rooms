"""What the slash-command popup offers for a given input line. Pure: line plus room state in, rows out."""

from collections.abc import Sequence
from dataclasses import dataclass

from agent_rooms.tui.api import Member, Summary
from agent_rooms.tui.commands import COMMANDS, ROLE_MEANINGS, ROLES


@dataclass(frozen=True, slots=True)
class Suggestion:
    insert: str
    name: str
    hint: str = ""
    description: str = ""

    def is_typed(self, line: str) -> bool:
        return line.strip() == self.insert.strip()


def _commands(prefix: str) -> list[Suggestion]:
    return [
        Suggestion(f"/{spec.name} ", f"/{spec.name}", spec.hint, spec.description)
        for spec in COMMANDS
        if spec.name.startswith(prefix.lower())
    ]


def _rooms(
    command: str, prefix: str, rooms: Sequence[Summary], *, only: str | None = None
) -> list[Suggestion]:
    return [
        Suggestion(f"/{command} {room.slug}", room.slug, room.status, room.topic)
        for room in rooms
        if room.slug.startswith(prefix) and (only is None or room.status == only)
    ]


def _members(prefix: str, members: Sequence[Member]) -> list[Suggestion]:
    return [
        Suggestion(
            f"/role {member.name} ",
            member.name,
            member.role,
            "listening" if member.listening else "not listening",
        )
        for member in members
        if member.name.startswith(prefix)
    ]


def _roles(member: str, prefix: str) -> list[Suggestion]:
    return [
        Suggestion(f"/role {member} {role}", role, "", ROLE_MEANINGS[role])
        for role in ROLES
        if role.startswith(prefix.lower())
    ]


def suggest(line: str, rooms: Sequence[Summary] = (), members: Sequence[Member] = ()) -> list[Suggestion]:
    if not line.startswith("/") or line.startswith("//"):
        return []
    if " " not in line:
        return _commands(line[1:])
    command, _, rest = line[1:].partition(" ")
    args = rest.split(" ")
    result: list[Suggestion] = []
    match command.lower(), len(args):
        case "room", 1:
            result = _rooms("room", args[0], rooms)
        case "delete", 1:
            result = _rooms("delete", args[0], rooms, only="closed")
        case "role", 1:
            result = _members(args[0], members)
        case "role", 2:
            result = _roles(args[0], args[1])
    return result
