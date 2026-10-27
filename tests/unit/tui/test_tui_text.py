from datetime import UTC, datetime

import pytest

from agent_rooms.tui.text import clean, local_time


def test_plain_text_is_unchanged() -> None:
    assert (
        clean("hello [bold red]x[/] ünïcode ✓\nsecond line") == "hello [bold red]x[/] ünïcode ✓\nsecond line"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "\x1b]0;pwned\x07",
        "\x1b[2J\x1b[H",
        "\x1b]52;c;ZWNobyBoaQ==\x1b\\",
        "a\x00b\x07c\x08d\x0be\x0cf\x7fg",
        "a\x85b\x9bc",
        "a\u202eb\u2066c\u200bd\ufeffe\u061cf",
    ],
)
def test_terminal_controls_and_invisible_characters_are_removed(hostile: str) -> None:
    cleaned = clean(hostile)
    assert all(ch in "\n" or (ch.isprintable() and ord(ch) not in range(0x80, 0xA0)) for ch in cleaned)
    assert "\x1b" not in cleaned
    assert not set(cleaned) & set("\u202e\u2066\u200b\ufeff\u061c")


def test_tabs_become_spaces_and_carriage_returns_vanish() -> None:
    assert clean("a\tb\r\nc\rd") == "a    b\ncd"


def test_local_time_shows_only_the_clock_for_today() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    assert local_time("2026-10-27T10:05:09.123Z", now, UTC) == "10:05"


def test_local_time_adds_the_date_for_other_days() -> None:
    now = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
    assert local_time("2026-10-25T23:59:00.000Z", now, UTC) == "Oct 25 23:59"


def test_local_time_tolerates_garbage() -> None:
    assert local_time("yesterday-ish") == ""
