from datetime import UTC, datetime

from textual.content import Content

from agent_rooms.tui.api import Member, Message, Summary
from agent_rooms.tui.colors import kind_tag_style, sender_color
from agent_rooms.tui.render import (
    header_content,
    kind_class,
    member_content,
    message_content,
    room_line,
)

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


def message(**overrides: object) -> Message:
    fields: dict[str, object] = {
        "id": 7,
        "room": "r1",
        "sender": "claude",
        "alias": "main",
        "kind": "review_request",
        "text": "please look",
        "reply_to": None,
        "created_at": "2026-10-27T10:05:09.000Z",
    }
    return Message(**{**fields, **overrides})  # type: ignore[arg-type]


def room(slug: str = "alpha", status: str = "active", count: int = 3) -> Summary:
    return Summary(slug, "Some topic", status, 60, count, (), "2026-10-27T10:00:00.000Z")


def _style_of(content: Content, text: str) -> str:
    start = content.plain.index(text)
    return " ".join(str(span.style) for span in content.spans if span.start <= start < span.end)


def test_the_sender_name_is_bold_and_in_the_senders_colour() -> None:
    content = message_content(message(sender="codex", alias=None), NOW, UTC)
    style = _style_of(content, "codex")
    assert "bold" in style
    assert sender_color("codex") in style


def test_an_alias_shares_the_agents_colour() -> None:
    content = message_content(message(sender="claude", alias="main"), NOW, UTC)
    assert sender_color("claude") in _style_of(content, "claude")
    assert sender_color("claude") in _style_of(content, "/main")


def test_the_kind_is_a_coloured_tag_and_chat_stays_neutral() -> None:
    review = message_content(message(kind="review_request"), NOW, UTC)
    chat = message_content(message(kind="chat"), NOW, UTC)
    assert kind_tag_style("review_request") in _style_of(review, "review request")
    assert " on #" not in _style_of(chat, "chat")


def test_id_and_time_are_dim() -> None:
    content = message_content(message(), NOW, UTC)
    assert "dim" in _style_of(content, "#7")
    assert "dim" in _style_of(content, "10:05")


def test_the_body_has_no_styling_at_all() -> None:
    content = message_content(message(text="plain [bold]body[/bold]"), NOW, UTC)
    body_start = content.plain.index("\n") + 1
    assert all(span.end <= body_start for span in content.spans)


def test_a_message_has_a_header_line_and_its_text() -> None:
    content = message_content(message(), NOW, UTC)
    assert content.plain == "#7  claude/main  review request  10:05\nplease look"


def test_a_reply_shows_what_it_answers() -> None:
    content = message_content(message(reply_to=3), NOW, UTC)
    assert content.plain.splitlines()[0] == "#7  claude/main  review request  ↪ #3  10:05"


def test_multiline_text_is_kept_line_for_line() -> None:
    content = message_content(message(text="a\n\n  b"), NOW, UTC)
    assert content.plain.split("\n", 1)[1] == "a\n\n  b"


def test_markup_in_text_is_drawn_literally_and_gets_no_styling() -> None:
    hostile = "[bold red]x[/] [link=https://evil.example]click[/link] [@click=app.quit]q[/]"
    content = message_content(message(text=hostile, sender="[b]mallory[/b]", alias=None), NOW, UTC)
    header, body = content.plain.split("\n", 1)
    assert body == hostile
    assert "[b]mallory[/b]" in header
    assert all(span.end <= len(header) for span in content.spans)


def test_an_unknown_kind_is_shown_but_gets_the_neutral_class() -> None:
    assert kind_class("review_request") == "kind-review_request"
    assert kind_class("surprise") == "kind-chat"
    assert kind_class("[bold]") == "kind-chat"


def test_a_room_line_is_marker_slug_and_a_right_aligned_count_of_exact_width() -> None:
    line = room_line(room("alpha", "active", 12), selected=False, width=20)
    assert line.plain == "● alpha" + " " * 11 + "12"
    assert line.cell_length == 20


def test_room_markers_follow_the_status() -> None:
    assert room_line(room(status="paused"), selected=False, width=20).plain.startswith("◐ ")
    assert room_line(room(status="closed"), selected=False, width=20).plain.startswith("○ ")


def test_a_long_slug_is_truncated_to_fit() -> None:
    line = room_line(room("a" * 40, count=123), selected=True, width=20)
    assert line.cell_length == 20
    assert line.plain.endswith(" 123")
    assert "…" in line.plain


def test_member_lines_show_name_role_and_listening_state() -> None:
    listening = member_content(Member("claude", "main", "writer", True))
    idle = member_content(Member("codex", None, "reviewer", False))
    assert listening.plain == "● claude/main\n  writer · listening"
    assert idle.plain == "○ codex\n  reviewer · not listening"


def test_the_lead_is_marked_and_styled_apart_from_other_roles() -> None:
    lead = member_content(Member("claude", "main", "lead", True))
    writer = member_content(Member("claude", "main", "writer", True))
    assert lead.plain == "● claude/main\n  ★ lead · listening"
    lead_styles = {str(span.style) for span in lead.spans}
    assert lead_styles - {str(span.style) for span in writer.spans}


def test_members_use_the_same_colours_as_senders() -> None:
    content = member_content(Member("codex", None, "reviewer", False))
    assert sender_color("codex") in _style_of(content, "codex")
    assert "bold" in _style_of(content, "codex")


def test_the_room_header_has_slug_status_and_topic_without_markup() -> None:
    content = header_content(
        Summary("alpha", "[red]topic[/]", "paused", 60, 0, (), "2026-10-27T10:00:00.000Z")
    )
    assert content.plain == "alpha  paused  [red]topic[/]"
    assert isinstance(content, Content)
