import pytest

from agent_rooms.tui.commands import (
    COMMANDS,
    Delete,
    Help,
    Invalid,
    Login,
    Logout,
    New,
    Quit,
    Room,
    Say,
    SetStatus,
    Task,
    parse,
)


def test_blank_input_is_ignored() -> None:
    assert parse("") is None
    assert parse("   \t ") is None


def test_plain_text_is_a_chat_message() -> None:
    assert parse("  hello there  ") == Say("hello there")


def test_text_is_never_interpreted_as_markup() -> None:
    assert parse("[bold red]x[/] [link=http://e]y[/link]") == Say("[bold red]x[/] [link=http://e]y[/link]")


def test_double_slash_posts_a_literal_leading_slash() -> None:
    assert parse("//usr/bin is where it lives") == Say("/usr/bin is where it lives")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/login", Login()),
        ("/logout", Logout()),
        ("/help", Help()),
        ("/quit", Quit()),
        ("/pause", SetStatus("paused")),
        ("/resume", SetStatus("active")),
        ("/close", SetStatus("closed")),
        ("/room auth-refactor", Room("auth-refactor")),
        ("/task fix the login bug", Task("fix the login bug")),
        ("/new auth-refactor Move auth to OIDC", New("auth-refactor", "Move auth to OIDC")),
        ("/delete", Delete(None)),
        ("/delete old-room", Delete("old-room")),
    ],
)
def test_slash_commands(line: str, expected: object) -> None:
    assert parse(line) == expected


def test_command_names_are_case_insensitive_and_arguments_keep_their_case() -> None:
    assert parse("/TASK Fix THE Bug") == Task("Fix THE Bug")


def test_new_keeps_the_rest_of_the_line_as_the_topic() -> None:
    assert parse("/new  r1   a   topic  with   spaces ") == New("r1", "a   topic  with   spaces")


@pytest.mark.parametrize(
    "line",
    [
        "/room",
        "/room a b",
        "/task",
        "/task   ",
        "/new",
        "/new only-slug",
        "/pause now",
        "/login x",
        "/delete a b",
    ],
)
def test_wrong_arguments_give_a_usage_message(line: str) -> None:
    result = parse(line)
    assert isinstance(result, Invalid)
    assert result.message.startswith("Usage: /")


def test_unknown_command_points_to_help() -> None:
    result = parse("/frobnicate now")
    assert isinstance(result, Invalid)
    assert "/frobnicate" in result.message
    assert "/help" in result.message


def test_command_table_covers_every_command_once() -> None:
    names = [spec.name for spec in COMMANDS]
    assert names == [
        "login",
        "logout",
        "room",
        "new",
        "task",
        "pause",
        "resume",
        "close",
        "delete",
        "help",
        "quit",
    ]
    assert all(spec.usage.startswith("/" + spec.name) and spec.summary for spec in COMMANDS)
