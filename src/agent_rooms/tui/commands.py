from collections.abc import Sequence
from dataclasses import dataclass
from itertools import takewhile


@dataclass(frozen=True, slots=True)
class Say:
    text: str


@dataclass(frozen=True, slots=True)
class Task:
    text: str


@dataclass(frozen=True, slots=True)
class Login:
    pass


@dataclass(frozen=True, slots=True)
class Logout:
    pass


@dataclass(frozen=True, slots=True)
class Room:
    slug: str


@dataclass(frozen=True, slots=True)
class New:
    slug: str
    topic: str


@dataclass(frozen=True, slots=True)
class SetStatus:
    status: str


ROLES = ("writer", "reviewer", "observer", "lead")


@dataclass(frozen=True, slots=True)
class SetRole:
    member: str
    role: str

    @property
    def agent(self) -> str:
        return self.member.partition("/")[0]

    @property
    def alias(self) -> str:
        return self.member.partition("/")[2]


@dataclass(frozen=True, slots=True)
class Delete:
    slug: str | None


@dataclass(frozen=True, slots=True)
class Help:
    pass


@dataclass(frozen=True, slots=True)
class Quit:
    pass


@dataclass(frozen=True, slots=True)
class Invalid:
    message: str


Command = Say | Task | Login | Logout | Room | New | SetStatus | SetRole | Delete | Help | Quit | Invalid


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    usage: str
    summary: str
    takes_arguments: bool = False


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("login", "/login", "Sign in with the human token (kept in the OS keyring)"),
    CommandSpec("logout", "/logout", "Forget the saved token"),
    CommandSpec("room", "/room <slug>", "Switch to a room", takes_arguments=True),
    CommandSpec("new", "/new <slug> <topic…>", "Create a room and switch to it", takes_arguments=True),
    CommandSpec("task", "/task <text>", "Post a message of kind task", takes_arguments=True),
    CommandSpec(
        "role", "/role <agent[/alias]> <role>", "Set a member's role (writer, reviewer, observer, lead)", True
    ),
    CommandSpec("pause", "/pause", "Pause the selected room"),
    CommandSpec("resume", "/resume", "Resume the selected room"),
    CommandSpec("close", "/close", "Close the selected room"),
    CommandSpec(
        "delete", "/delete [slug]", "Delete a closed room (type its slug to confirm)", takes_arguments=True
    ),
    CommandSpec("help", "/help", "Show this list"),
    CommandSpec("quit", "/quit", "Exit (Ctrl+C works too)"),
)
_USAGE = {spec.name: spec.usage for spec in COMMANDS}
_SIMPLE: dict[str, Command] = {
    "login": Login(),
    "logout": Logout(),
    "help": Help(),
    "quit": Quit(),
    "pause": SetStatus("paused"),
    "resume": SetStatus("active"),
    "close": SetStatus("closed"),
}


def _usage(name: str) -> Invalid:
    return Invalid(f"Usage: {_USAGE[name]}")


def _parse_role(args: list[str]) -> Command:
    if len(args) != 2:
        return _usage("role")
    role = args[1].lower()
    if role not in ROLES:
        return Invalid(f"Unknown role {args[1]}. Roles: {', '.join(ROLES)}.")
    return SetRole(args[0], role)


def _parse_slash(text: str) -> Command:
    head, _, rest = text[1:].partition(" ")
    name, rest = head.lower(), rest.strip()
    args = rest.split()
    if name in _SIMPLE:
        return _usage(name) if args else _SIMPLE[name]
    result: Command
    match name:
        case "room":
            result = Room(args[0]) if len(args) == 1 else _usage(name)
        case "task":
            result = Task(rest) if rest else _usage(name)
        case "new":
            slug, _, topic = rest.partition(" ")
            result = New(slug, topic.strip()) if slug and topic.strip() else _usage(name)
        case "role":
            result = _parse_role(args)
        case "delete":
            result = Delete(args[0] if args else None) if len(args) <= 1 else _usage(name)
        case _:
            result = Invalid(f"Unknown command /{head}. Type /help to list the commands.")
    return result


def parse(line: str) -> Command | None:
    text = line.strip()
    if not text:
        return None
    if text.startswith("//"):
        return Say(text[1:])
    return _parse_slash(text) if text.startswith("/") else Say(text)


@dataclass(frozen=True, slots=True)
class Completion:
    line: str
    options: tuple[str, ...]


_SLUG_COMMANDS = ("room", "delete")


def _common_prefix(items: list[str]) -> str:
    return "".join(
        letters[0] for letters in takewhile(lambda group: len(set(group)) == 1, zip(*items, strict=False))
    )


def _complete_command(prefix: str) -> Completion | None:
    specs = [spec for spec in COMMANDS if spec.name.startswith(prefix.lower())]
    if not specs:
        return None
    if len(specs) == 1:
        only = specs[0]
        return Completion(f"/{only.name}{' ' if only.takes_arguments else ''}", ())
    names = [f"/{spec.name}" for spec in specs]
    return Completion(_common_prefix(names), tuple(names))


def _completion(
    command: str, prefix: str, candidates: list[str], *, then_space: bool = False
) -> Completion | None:
    matches = [candidate for candidate in candidates if candidate.startswith(prefix)]
    if not matches:
        return None
    if len(matches) == 1:
        return Completion(f"{command}{matches[0]}{' ' if then_space else ''}", ())
    return Completion(f"{command}{_common_prefix(matches)}", tuple(matches))


def complete(line: str, slugs: list[str], members: Sequence[str] = ()) -> Completion | None:
    if not line.startswith("/"):
        return None
    if " " not in line:
        return _complete_command(line[1:])
    command, _, rest = line[1:].partition(" ")
    name, args = command.lower(), rest.split(" ")
    if name in _SLUG_COMMANDS and len(args) == 1:
        return _completion(f"/{command} ", args[0], slugs)
    if name == "role" and len(args) == 1:
        return _completion(f"/{command} ", args[0], list(members), then_space=True)
    if name == "role" and len(args) == 2 and args[0] in members:
        return _completion(f"/{command} {args[0]} ", args[1], list(ROLES))
    return None
