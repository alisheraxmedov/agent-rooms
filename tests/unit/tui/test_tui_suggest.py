from agent_rooms.tui.api import Member, Summary
from agent_rooms.tui.commands import COMMANDS, ROLE_MEANINGS, ROLES
from agent_rooms.tui.suggest import Suggestion, suggest


def room(slug: str, status: str = "active", topic: str = "") -> Summary:
    return Summary(slug, topic or f"topic of {slug}", status, 60, 0, (), "2026-10-27T10:00:00.000Z")


ROOMS = [
    room("auth-refactor", "active", "Move auth to OIDC"),
    room("auth-docs", "closed", "Write the docs"),
    room("billing", "paused", "Stripe retries"),
    room("old-spike", "closed", "Throwaway"),
]
MEMBERS = [
    Member("claude", "main", "writer", True),
    Member("claude", "second", "observer", False),
    Member("codex", None, "reviewer", False),
]


def names(suggestions: list[Suggestion]) -> list[str]:
    return [item.name for item in suggestions]


def test_a_lone_slash_lists_every_command_in_table_order() -> None:
    assert names(suggest("/")) == [f"/{spec.name}" for spec in COMMANDS]


def test_command_rows_come_from_the_single_command_table() -> None:
    by_name = {item.name: item for item in suggest("/")}
    for spec in COMMANDS:
        item = by_name[f"/{spec.name}"]
        assert item.hint == spec.hint
        assert item.description == spec.description
        assert item.insert == f"/{spec.name} "
    assert by_name["/room"].hint == "<slug>"
    assert by_name["/login"].hint == ""


def test_commands_filter_by_prefix_as_you_type() -> None:
    assert names(suggest("/r")) == ["/room", "/role", "/resume"]
    assert names(suggest("/ro")) == ["/room", "/role"]
    assert names(suggest("/rol")) == ["/role"]
    assert names(suggest("/RO")) == ["/room", "/role"]


def test_a_fully_typed_command_still_matches_itself() -> None:
    assert names(suggest("/help")) == ["/help"]


def test_nothing_is_suggested_for_plain_text_or_unknown_commands() -> None:
    assert suggest("") == []
    assert suggest("hello") == []
    assert suggest(" /room") == []
    assert suggest("/zzz") == []
    assert suggest("//usr") == []


def test_nothing_is_suggested_after_commands_that_take_free_text() -> None:
    assert suggest("/task ") == []
    assert suggest("/new ") == []
    assert suggest("/new slug ") == []
    assert suggest("/pause ") == []


def test_room_arguments_show_slug_status_and_topic() -> None:
    items = suggest("/room ", ROOMS, MEMBERS)
    assert names(items) == ["auth-refactor", "auth-docs", "billing", "old-spike"]
    first = items[0]
    assert (first.hint, first.description) == ("active", "Move auth to OIDC")
    assert first.insert == "/room auth-refactor"
    assert items[2].hint == "paused"


def test_room_arguments_filter_by_prefix() -> None:
    assert names(suggest("/room au", ROOMS)) == ["auth-refactor", "auth-docs"]
    assert names(suggest("/room auth-d", ROOMS)) == ["auth-docs"]
    assert suggest("/room zzz", ROOMS) == []
    assert suggest("/room ", []) == []


def test_room_arguments_stop_after_the_slug() -> None:
    assert suggest("/room billing ", ROOMS) == []


def test_delete_only_offers_closed_rooms() -> None:
    items = suggest("/delete ", ROOMS)
    assert names(items) == ["auth-docs", "old-spike"]
    assert items[0].hint == "closed"
    assert items[0].insert == "/delete auth-docs"
    assert names(suggest("/delete o", ROOMS)) == ["old-spike"]
    assert suggest("/delete b", ROOMS) == []


def test_role_first_argument_lists_members_with_their_current_role() -> None:
    items = suggest("/role ", ROOMS, MEMBERS)
    assert names(items) == ["claude/main", "claude/second", "codex"]
    assert [item.hint for item in items] == ["writer", "observer", "reviewer"]
    assert [item.description for item in items] == ["listening", "not listening", "not listening"]
    assert items[0].insert == "/role claude/main "
    assert names(suggest("/role cl", ROOMS, MEMBERS)) == ["claude/main", "claude/second"]
    assert names(suggest("/role co", ROOMS, MEMBERS)) == ["codex"]
    assert suggest("/role zzz", ROOMS, MEMBERS) == []
    assert suggest("/role ", ROOMS, []) == []


def test_role_second_argument_lists_the_four_roles_with_meanings() -> None:
    items = suggest("/role claude ", ROOMS, MEMBERS)
    assert names(items) == list(ROLES) == ["writer", "reviewer", "observer", "lead"]
    assert [item.description for item in items] == [ROLE_MEANINGS[role] for role in ROLES]
    assert items[0].insert == "/role claude writer"
    assert ROLE_MEANINGS["lead"] == "assigns tasks and may post done"
    assert ROLE_MEANINGS["reviewer"] == "reviews, never edits"


def test_role_names_filter_by_prefix_and_work_for_unknown_members() -> None:
    assert names(suggest("/role claude/main re", ROOMS, MEMBERS)) == ["reviewer"]
    assert names(suggest("/role someone o", ROOMS, [])) == ["observer"]
    assert suggest("/role claude zzz", ROOMS, MEMBERS) == []
    assert suggest("/role claude lead ", ROOMS, MEMBERS) == []


def test_a_suggestion_knows_when_the_line_already_says_it() -> None:
    (help_row,) = suggest("/help")
    assert help_row.is_typed("/help")
    assert help_row.is_typed("/help ")
    (au,) = suggest("/room auth-d", ROOMS)
    assert not au.is_typed("/room auth-d")
    assert au.is_typed("/room auth-docs")
    assert not suggest("/ro")[0].is_typed("/ro")


def test_every_description_is_short() -> None:
    assert all(0 < len(spec.description) <= 40 for spec in COMMANDS)
    assert all(0 < len(text) <= 40 for text in ROLE_MEANINGS.values())
