from agent_rooms.tui.commands import Completion, complete

SLUGS = ["auth-refactor", "auth-docs", "billing"]


def test_a_unique_command_prefix_completes_with_a_trailing_space_when_it_takes_arguments() -> None:
    assert complete("/ro", SLUGS) == Completion("/room ", ())
    assert complete("/ta", SLUGS) == Completion("/task ", ())


def test_a_command_without_arguments_completes_without_trailing_space() -> None:
    assert complete("/he", SLUGS) == Completion("/help", ())
    assert complete("/qu", SLUGS) == Completion("/quit", ())


def test_an_ambiguous_command_prefix_extends_to_the_common_prefix_and_lists_options() -> None:
    result = complete("/lo", SLUGS)
    assert result == Completion("/log", ("/login", "/logout"))


def test_a_lone_slash_lists_every_command() -> None:
    result = complete("/", SLUGS)
    assert result is not None
    assert result.line == "/"
    assert "/room" in result.options
    assert len(result.options) == 11


def test_room_slugs_complete_after_room_and_delete() -> None:
    assert complete("/room bi", SLUGS) == Completion("/room billing", ())
    assert complete("/delete bi", SLUGS) == Completion("/delete billing", ())


def test_ambiguous_slugs_extend_to_the_common_prefix() -> None:
    assert complete("/room a", SLUGS) == Completion("/room auth-", ("auth-refactor", "auth-docs"))


def test_nothing_completes_for_plain_text_unknown_prefixes_or_other_arguments() -> None:
    assert complete("hello", SLUGS) is None
    assert complete("/zzz", SLUGS) is None
    assert complete("/room zzz", SLUGS) is None
    assert complete("/new bi", SLUGS) is None
    assert complete("/room billing extra", SLUGS) is None
    assert complete("", SLUGS) is None
