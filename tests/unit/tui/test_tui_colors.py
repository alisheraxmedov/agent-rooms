from agent_rooms.tui.colors import KIND_TAGS, SENDER_COLORS, kind_tag_style, sender_color

DARK_BACKGROUND = "#121212"
KNOWN = ("claude", "codex", "opencode", "agy", "human", "system")


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str = DARK_BACKGROUND) -> float:
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_every_known_sender_has_its_own_colour() -> None:
    colours = [sender_color(name) for name in KNOWN]
    assert len(set(colours)) == len(KNOWN)
    assert sender_color("human") == "#ffffff"
    assert set(SENDER_COLORS) == set(KNOWN)


def test_colours_are_hex_and_readable_on_a_dark_terminal() -> None:
    for name in (*KNOWN, "stranger", "x", "reviewer-bot"):
        colour = sender_color(name)
        assert len(colour) == 7
        assert colour.startswith("#")
        assert contrast(colour) >= 4.5, name


def test_unknown_senders_get_a_stable_colour_that_never_impersonates_a_known_agent() -> None:
    names = [f"agent-{index}" for index in range(30)]
    first = [sender_color(name) for name in names]
    assert first == [sender_color(name) for name in names]
    assert not set(first) & {sender_color(name) for name in KNOWN}
    assert len(set(first)) >= 3


def test_an_unknown_sender_colour_is_fixed_across_runs() -> None:
    assert sender_color("stranger") == sender_color("stranger")
    assert sender_color("stranger") != sender_color("stranger2") or sender_color("a") != sender_color("b")


def test_review_kinds_have_distinct_tag_styles_and_chat_and_task_are_neutral() -> None:
    distinct = ("review_request", "changes_requested", "approved", "done", "system")
    styles = [kind_tag_style(kind) for kind in distinct]
    assert len(set(styles)) == len(distinct)
    assert all(" on #" in style for style in styles)
    for neutral in ("chat", "task", "something-else"):
        assert " on " not in kind_tag_style(neutral)
    assert set(KIND_TAGS) >= set(distinct)


def test_tag_text_is_readable_on_its_chip() -> None:
    for kind in ("review_request", "changes_requested", "approved", "done", "system"):
        foreground, _, background = kind_tag_style(kind).replace("bold ", "").partition(" on ")
        assert contrast(foreground, background) >= 4.5, kind
