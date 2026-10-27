"""Pure builders for what the widgets draw. Server text goes through `Content(...)`, never markup."""

from datetime import datetime, tzinfo

from rich.cells import cell_len
from textual.content import Content

from agent_rooms.tui.api import Member, Message, Summary
from agent_rooms.tui.colors import kind_tag_style, sender_color
from agent_rooms.tui.text import local_time

KNOWN_KINDS = ("chat", "task", "review_request", "changes_requested", "approved", "done", "system")
STATUS_STYLES = {"active": "$success", "paused": "$warning", "closed": "$text-muted"}
STATUS_MARKERS = {"active": "●", "paused": "◐", "closed": "○"}


def kind_class(kind: str) -> str:
    return f"kind-{kind if kind in KNOWN_KINDS else 'chat'}"


def _author(sender: str, alias: str | None) -> list[tuple[str, str]]:
    color = sender_color(sender)
    parts = [(sender, f"bold {color}")]
    return [*parts, (f"/{alias}", color)] if alias else parts


def _tag(kind: str) -> list[str | tuple[str, str]]:
    style = kind_tag_style(kind)
    label = kind.replace("_", " ")
    return [" ", (f" {label} ", style), " "] if " on " in style else ["  ", (label, style), "  "]


def message_content(message: Message, now: datetime | None = None, tz: tzinfo | None = None) -> Content:
    parts: list[str | tuple[str, str] | Content] = [(f"#{message.id}", "dim"), "  "]
    parts += _author(message.sender, message.alias)
    parts += _tag(message.kind)
    if message.reply_to is not None:
        parts += [(f"↪ #{message.reply_to}", "dim"), "  "]
    parts += [(local_time(message.created_at, now, tz), "dim"), "\n", Content(message.text)]
    return Content.assemble(*parts)


def room_line(room: Summary, *, selected: bool, width: int) -> Content:
    marker = STATUS_MARKERS.get(room.status, "○")
    count = str(room.message_count)
    available = max(1, width - 2 - len(count) - 1)
    slug = room.slug if cell_len(room.slug) <= available else room.slug[: available - 1] + "…"
    padding = " " * max(1, width - 2 - cell_len(slug) - len(count))
    line = Content.assemble(
        (marker, STATUS_STYLES.get(room.status, "dim")),
        " ",
        (slug, "bold" if selected else ""),
        padding,
        (count, "dim"),
    )
    return line.stylize("on $primary 25%") if selected else line


def member_content(member: Member) -> Content:
    dot, state = ("●", "listening") if member.listening else ("○", "not listening")
    is_lead = member.role == "lead"
    role = ("★ lead" if is_lead else member.role, "bold $accent" if is_lead else "dim")
    return Content.assemble(
        (dot, "$success" if member.listening else "dim"),
        " ",
        *_author(member.agent, member.alias),
        "\n  ",
        role,
        (f" · {state}", "dim"),
    )


def header_content(room: Summary) -> Content:
    return Content.assemble(
        (room.slug, "bold"),
        "  ",
        (room.status, STATUS_STYLES.get(room.status, "dim")),
        "  ",
        (room.topic, "$text-muted"),
    )
