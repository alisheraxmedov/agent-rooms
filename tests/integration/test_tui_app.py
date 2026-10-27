import time
from collections.abc import Callable
from typing import Any

import pytest
from textual.geometry import Region
from textual.pilot import Pilot
from textual.widget import Widget

from agent_rooms.tui.api import RoomsApi
from agent_rooms.tui.app import AgentRoomsApp
from agent_rooms.tui.credentials import ACCOUNT, SERVICE, TokenStore
from agent_rooms.tui.widgets.composer import CommandInput
from agent_rooms.tui.widgets.thread import MessageView
from keyring_fake import MemoryKeyring

pytestmark = pytest.mark.integration

HUMAN_TOKEN = "human-test-token-0123456789"
SIZE = (140, 40)


@pytest.fixture
def mcp_mode() -> str:
    return "auto"


@pytest.fixture
def keys() -> MemoryKeyring:
    return MemoryKeyring()


@pytest.fixture
def make_app(live_server: Any, keys: MemoryKeyring) -> Callable[..., AgentRoomsApp]:
    def _make(token: str | None = HUMAN_TOKEN, url: str | None = None) -> AgentRoomsApp:
        if token:
            keys.set_password(SERVICE, ACCOUNT, token)
        store = TokenStore(environ={}, backend=keys)
        api = RoomsApi(url or live_server.base_url, store.load())
        return AgentRoomsApp(api, store, poll_interval=0.1)

    return _make


def rendered(widget: Widget) -> str:
    size = widget.outer_size
    return "\n".join(
        strip.text.rstrip() for strip in widget.render_lines(Region(0, 0, size.width, max(size.height, 1)))
    )


def thread_text(app: AgentRoomsApp) -> str:
    return "\n".join(rendered(view) for view in app.query(MessageView))


def screen_text(app: AgentRoomsApp) -> str:
    return "\n".join(line.text.rstrip() for line in app.screen._compositor.render_strips())


async def until(pilot: Pilot[None], predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await pilot.pause(0.02)


async def submit(pilot: Pilot[None], app: AgentRoomsApp, line: str) -> None:
    app.query_one(CommandInput).value = line
    await pilot.press("enter")


async def make_room(rest: Any, slug: str = "r1", topic: str = "Topic") -> None:
    response = await rest("human", "POST", "/api/rooms", json={"slug": slug, "topic": topic})
    assert response.status_code == 201, response.text


async def server_messages(rest: Any, slug: str = "r1") -> list[dict[str, Any]]:
    return (await rest("human", "GET", f"/api/rooms/{slug}/messages")).json()["messages"]  # type: ignore[no-any-return]


async def server_room(rest: Any, slug: str = "r1") -> dict[str, Any] | None:
    rooms = (await rest("human", "GET", "/api/rooms")).json()
    return next((room for room in rooms if room["slug"] == slug), None)


@pytest.mark.anyio
async def test_the_layout_shows_rooms_thread_members_and_one_input(
    make_app: Any, rest: Any, call: Any
) -> None:
    await make_room(rest, "alpha", "Build the thing")
    await call("claude", "join_room", room="alpha", role="writer", alias="main")
    await call("claude", "post", room="alpha", text="hello from claude", alias="main")
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.rooms_loaded and app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await until(pilot, lambda: "hello from claude" in thread_text(app))

        screen = screen_text(app)
        assert "Agent Rooms" in screen
        assert "connected" in screen
        assert "alpha" in rendered(app.query_one("#rooms"))
        assert "alpha" in rendered(app.query_one("#room-header"))
        assert "Build the thing" in rendered(app.query_one("#room-header"))
        assert "claude/main" in rendered(app.query_one("#members"))
        assert "writer" in rendered(app.query_one("#members"))
        assert "claude/main" in thread_text(app)
        assert len(app.query(CommandInput)) == 1
        assert not app.query("Button")


@pytest.mark.anyio
async def test_typing_text_and_pressing_enter_posts_a_chat_message(make_app: Any, rest: Any) -> None:
    await make_room(rest)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await pilot.press(*"/room r1", "enter")
        await until(pilot, lambda: app.state.selected == "r1")
        await pilot.press(*"hello team", "enter")
        await until(pilot, lambda: "hello team" in thread_text(app))
        (posted,) = await server_messages(rest)
        assert (posted["sender"], posted["kind"], posted["text"]) == ("human", "chat", "hello team")
        assert app.query_one(CommandInput).value == ""


@pytest.mark.anyio
async def test_task_posts_a_task_message(make_app: Any, rest: Any) -> None:
    await make_room(rest)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        await submit(pilot, app, "/task fix the login bug")
        await until(pilot, lambda: "fix the login bug" in thread_text(app))
        (posted,) = await server_messages(rest)
        assert (posted["kind"], posted["text"]) == ("task", "fix the login bug")
        assert "task" in thread_text(app)


@pytest.mark.anyio
async def test_posting_without_a_room_explains_what_to_do(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "hello?")
        await until(pilot, lambda: "/room" in app.status_text)
        assert app.status_is_error


@pytest.mark.anyio
async def test_room_switches_between_rooms_and_rejects_unknown_ones(make_app: Any, rest: Any) -> None:
    await make_room(rest, "one", "First topic")
    await make_room(rest, "two", "Second topic")
    await rest("human", "POST", "/api/rooms/one/messages", json={"text": "in one"})
    await rest("human", "POST", "/api/rooms/two/messages", json={"text": "in two"})
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["one", "two"] or app.state.slugs == ["two", "one"])
        await submit(pilot, app, "/room one")
        await until(pilot, lambda: "in one" in thread_text(app))
        await submit(pilot, app, "/room two")
        await until(pilot, lambda: "in two" in thread_text(app))
        assert "in one" not in thread_text(app)
        assert "Second topic" in rendered(app.query_one("#room-header"))
        await submit(pilot, app, "/room nowhere")
        await until(pilot, lambda: "nowhere" in app.status_text)
        assert app.status_is_error
        assert app.state.selected == "two"


@pytest.mark.anyio
async def test_new_creates_a_room_and_switches_to_it(make_app: Any, rest: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/new fresh-room Move auth to OIDC")
        await until(pilot, lambda: app.state.selected == "fresh-room")
        room = await server_room(rest, "fresh-room")
        assert room is not None
        assert room["topic"] == "Move auth to OIDC"
        await until(pilot, lambda: "Move auth to OIDC" in rendered(app.query_one("#room-header")))
        assert "fresh-room" in rendered(app.query_one("#rooms"))


@pytest.mark.anyio
async def test_new_shows_the_servers_validation_message(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/new Bad_Slug some topic")
        await until(pilot, lambda: "slug" in app.status_text.lower())
        assert app.status_is_error
        assert app.state.selected is None


@pytest.mark.anyio
async def test_pause_resume_and_close_change_the_room_status(make_app: Any, rest: Any) -> None:
    await make_room(rest)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        for command, expected in (("/pause", "paused"), ("/resume", "active"), ("/close", "closed")):
            await submit(pilot, app, command)
            if command == "/close":
                await until(pilot, lambda: app.mode == "confirm")
                await submit(pilot, app, "yes")
            await until(
                pilot,
                lambda status=expected: app.state.summary is not None and app.state.summary.status == status,
            )
            room = await server_room(rest)
            assert room is not None
            assert room["status"] == expected
            assert expected in rendered(app.query_one("#room-header"))


@pytest.mark.anyio
async def test_posting_to_a_closed_room_shows_the_servers_message_inline(make_app: Any, rest: Any) -> None:
    await make_room(rest)
    await rest("human", "POST", "/api/rooms/r1/status", json={"status": "closed"})
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        await submit(pilot, app, "anyone there?")
        await until(pilot, lambda: app.status_is_error)
        assert "closed" in app.status_text.lower() or "not active" in app.status_text.lower()
        assert "closed" in screen_text(app).lower()


@pytest.mark.anyio
async def test_delete_needs_a_closed_room_and_the_slug_typed_back(make_app: Any, rest: Any) -> None:
    await make_room(rest, "doomed", "Soon gone")
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["doomed"])
        await submit(pilot, app, "/room doomed")

        await submit(pilot, app, "/delete")
        await until(pilot, lambda: "close" in app.status_text.lower())
        assert app.status_is_error
        assert app.mode == "normal"

        await submit(pilot, app, "/close")
        await until(pilot, lambda: app.mode == "confirm")
        await submit(pilot, app, "doomed")
        await until(pilot, lambda: app.state.summary is not None and app.state.summary.status == "closed")

        await submit(pilot, app, "/delete")
        await until(pilot, lambda: app.mode == "confirm")
        assert "doomed" in screen_text(app)
        await submit(pilot, app, "wrong")
        await until(pilot, lambda: app.mode == "normal")
        assert await server_room(rest, "doomed") is not None

        await submit(pilot, app, "/delete doomed")
        await until(pilot, lambda: app.mode == "confirm")
        await submit(pilot, app, "doomed")
        await until(pilot, lambda: app.state.selected is None)
        assert await server_room(rest, "doomed") is None
        assert app.state.slugs == []


@pytest.mark.anyio
async def test_escape_cancels_the_delete_confirmation(make_app: Any, rest: Any) -> None:
    await make_room(rest, "keep", "Keep me")
    await rest("human", "POST", "/api/rooms/keep/status", json={"status": "closed"})
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["keep"])
        await submit(pilot, app, "/delete keep")
        await until(pilot, lambda: app.mode == "confirm")
        await pilot.press("escape")
        await until(pilot, lambda: app.mode == "normal")
        assert await server_room(rest, "keep") is not None


@pytest.mark.anyio
async def test_a_rejected_token_asks_for_login_and_login_saves_the_new_token(
    make_app: Any, rest: Any, keys: MemoryKeyring
) -> None:
    await make_room(rest, "alpha", "Topic")
    app = make_app(token="a-stale-token-that-was-rotated-123")
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: "/login" in app.status_text)
        assert app.status_is_error
        assert not app.api.has_token

        await submit(pilot, app, "/login")
        await until(pilot, lambda: app.mode == "login")
        field = app.query_one(CommandInput)
        assert field.password is True
        await pilot.press(*HUMAN_TOKEN)
        assert HUMAN_TOKEN not in screen_text(app)
        await pilot.press("enter")
        await until(pilot, lambda: app.mode == "normal" and app.state.slugs == ["alpha"])

        assert keys.get_password(SERVICE, ACCOUNT) == HUMAN_TOKEN
        assert HUMAN_TOKEN not in screen_text(app)
        assert HUMAN_TOKEN not in app.status_text
        assert field.password is False
        assert app.api.has_token


@pytest.mark.anyio
async def test_a_wrong_token_at_login_is_not_saved_and_can_be_retried(
    make_app: Any, keys: MemoryKeyring
) -> None:
    app = make_app(token=None)
    async with app.run_test(size=SIZE) as pilot:
        assert "/login" in app.status_text
        await submit(pilot, app, "/login")
        await until(pilot, lambda: app.mode == "login")
        await submit(pilot, app, "nope-nope-nope-nope-nope-nope")
        await until(pilot, lambda: "rejected" in app.status_text.lower())
        assert app.mode == "login"
        assert keys.get_password(SERVICE, ACCOUNT) is None
        assert "nope-nope" not in screen_text(app)
        assert app.query_one(CommandInput).value == ""
        await pilot.press("escape")
        await until(pilot, lambda: app.mode == "normal")


@pytest.mark.anyio
async def test_the_token_never_enters_the_input_history(make_app: Any) -> None:
    app = make_app(token=None)
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/login")
        await until(pilot, lambda: app.mode == "login")
        await submit(pilot, app, HUMAN_TOKEN)
        await until(pilot, lambda: app.mode == "normal")
        await pilot.press("up")
        assert app.query_one(CommandInput).value == "/login"
        await pilot.press("up")
        assert HUMAN_TOKEN not in app.query_one(CommandInput).value


@pytest.mark.anyio
async def test_starting_without_a_token_shows_the_login_hint(make_app: Any) -> None:
    app = make_app(token=None)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.2)
        assert "/login" in app.status_text
        assert "/login" in screen_text(app)
        assert app.state.rooms == []


@pytest.mark.anyio
async def test_logout_forgets_the_saved_token(make_app: Any, keys: MemoryKeyring, rest: Any) -> None:
    await make_room(rest)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/logout")
        await until(pilot, lambda: not app.api.has_token)
        assert keys.get_password(SERVICE, ACCOUNT) is None
        assert app.state.rooms == []
        assert "/login" in app.status_text


@pytest.mark.anyio
async def test_an_unreachable_server_shows_offline_and_keeps_retrying(make_app: Any) -> None:
    app = make_app(url="http://127.0.0.1:9")
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.offline)
        await until(pilot, lambda: "offline, retrying…" in screen_text(app))


@pytest.mark.anyio
async def test_markup_in_room_text_is_drawn_literally(make_app: Any, rest: Any, call: Any) -> None:
    hostile = "[bold red]x[/] [link=https://evil.example]click[/link]"
    await rest("human", "POST", "/api/rooms", json={"slug": "r1", "topic": "[bold]topic[/bold]"})
    await call("claude", "join_room", room="r1", role="writer")
    await call("claude", "post", room="r1", text=hostile)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        await until(pilot, lambda: hostile in thread_text(app))
        assert "[bold]topic[/bold]" in rendered(app.query_one("#room-header"))
        view = app.query_one(MessageView)
        assert "evil.example" not in str(view.content.spans)  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_up_and_down_recall_the_input_history(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        field = app.query_one(CommandInput)
        await submit(pilot, app, "/help")
        await pilot.press("escape")
        await submit(pilot, app, "/room nowhere")
        await until(pilot, lambda: app.status_is_error)
        await pilot.press(*"draft")
        await pilot.press("up")
        assert field.value == "/room nowhere"
        await pilot.press("up")
        assert field.value == "/help"
        await pilot.press("down")
        assert field.value == "/room nowhere"
        await pilot.press("down")
        assert field.value == "draft"


@pytest.mark.anyio
async def test_tab_completes_commands_and_room_slugs(make_app: Any, rest: Any) -> None:
    await make_room(rest, "auth-refactor")
    await make_room(rest, "auth-docs")
    await make_room(rest, "billing")
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: len(app.state.slugs) == 3)
        field = app.query_one(CommandInput)
        await pilot.press(*"/roo", "tab")
        assert field.value == "/room "
        await pilot.press(*"bi", "tab")
        assert field.value == "/room billing"
        field.value = ""
        await pilot.press(*"/room au", "tab")
        assert field.value == "/room auth-"
        assert "auth-docs" in app.status_text
        assert "auth-refactor" in app.status_text
        assert app.focused is field


@pytest.mark.anyio
async def test_help_lists_every_command_and_closes_on_a_key(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/help")
        await until(pilot, lambda: len(app.screen_stack) > 1)
        text = screen_text(app)
        for command in (
            "/login",
            "/logout",
            "/room",
            "/new",
            "/task",
            "/role",
            "/pause",
            "/resume",
            "/close",
            "/delete",
            "/help",
            "/quit",
        ):
            assert command in text
        await pilot.press("escape")
        await until(pilot, lambda: len(app.screen_stack) == 1)
        assert app.focused is app.query_one(CommandInput)


@pytest.mark.anyio
async def test_quit_and_ctrl_c_exit(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/quit")
        await until(pilot, lambda: not app.is_running)
    other = make_app()
    async with other.run_test(size=SIZE) as pilot:
        await pilot.press("ctrl+c")
        await until(pilot, lambda: not other.is_running)


@pytest.mark.anyio
async def test_new_messages_follow_the_bottom_but_do_not_yank_a_scrolled_view(
    make_app: Any, rest: Any
) -> None:
    await make_room(rest)
    for index in range(40):
        await rest("human", "POST", "/api/rooms/r1/messages", json={"text": f"line {index}\nsecond"})
    app = make_app()
    async with app.run_test(size=(100, 24)) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        thread = app.query_one("#thread")
        await until(pilot, lambda: len(app.query(MessageView)) == 40)
        await until(pilot, lambda: thread.scroll_y >= thread.max_scroll_y - 1 and thread.max_scroll_y > 0)

        await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "newest"})
        await until(pilot, lambda: len(app.query(MessageView)) == 41)
        await until(pilot, lambda: thread.scroll_y >= thread.max_scroll_y - 1)

        await pilot.press("pageup", "pageup")
        scrolled = thread.scroll_y
        assert scrolled < thread.max_scroll_y - 1
        await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "while away"})
        await until(pilot, lambda: len(app.query(MessageView)) == 42)
        await pilot.pause(0.1)
        assert thread.scroll_y == scrolled
        assert "1 new" in screen_text(app)

        for _ in range(10):
            await pilot.press("pagedown")
        await until(pilot, lambda: thread.scroll_y >= thread.max_scroll_y - 1)
        await until(pilot, lambda: "new" not in rendered(app.query_one("#new-indicator")))


async def make_team(rest: Any, call: Any, slug: str = "alpha") -> None:
    await make_room(rest, slug, "Pair work")
    await call("claude", "join_room", room=slug, role="writer", alias="main")
    await call("codex", "join_room", room=slug, role="reviewer")


def roles_in_panel(app: AgentRoomsApp) -> str:
    return rendered(app.query_one("#members"))


@pytest.mark.anyio
async def test_role_changes_a_members_role_and_refreshes_the_members_panel(
    make_app: Any, rest: Any, call: Any
) -> None:
    await make_team(rest, call)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await until(pilot, lambda: "reviewer" in roles_in_panel(app))
        await submit(pilot, app, "/role codex writer")
        await until(pilot, lambda: "reviewer" not in roles_in_panel(app))
        assert roles_in_panel(app).count("writer") == 2
        assert "codex is now writer" in app.status_text
        room = await server_room(rest, "alpha")
        assert room is not None
        assert {(m["agent"], m["role"]) for m in room["members"]} == {
            ("claude", "writer"),
            ("codex", "writer"),
        }
        await until(pilot, lambda: "role: reviewer → writer" in thread_text(app))


@pytest.mark.anyio
async def test_role_accepts_an_agent_with_alias(make_app: Any, rest: Any, call: Any) -> None:
    await make_team(rest, call)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await until(pilot, lambda: app.state.summary is not None and len(app.state.summary.members) == 2)
        await submit(pilot, app, "/role claude/main observer")
        await until(pilot, lambda: "claude/main is now observer" in app.status_text)
        assert app.mode == "normal"


@pytest.mark.anyio
async def test_making_someone_lead_asks_for_yes_and_demotes_the_old_lead(
    make_app: Any, rest: Any, call: Any
) -> None:
    await make_team(rest, call)
    await call("opencode", "join_room", room="alpha", role="lead")
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await until(pilot, lambda: "★ lead" in roles_in_panel(app))

        await submit(pilot, app, "/role codex lead")
        await until(pilot, lambda: app.mode == "confirm")
        assert "codex" in app.status_text
        assert "yes" in app.status_text
        assert "opencode" in app.status_text
        await submit(pilot, app, "no")
        await until(pilot, lambda: app.mode == "normal")
        room = await server_room(rest, "alpha")
        assert room is not None
        assert {m["agent"]: m["role"] for m in room["members"]}["codex"] == "reviewer"

        await submit(pilot, app, "/role codex lead")
        await until(pilot, lambda: app.mode == "confirm")
        await submit(pilot, app, "yes")
        await until(
            pilot, lambda: app.mode == "normal" and "opencode" not in roles_in_panel(app).split("★")[0]
        )
        room = await server_room(rest, "alpha")
        assert room is not None
        assert {m["agent"]: m["role"] for m in room["members"]} == {
            "claude": "writer",
            "codex": "lead",
            "opencode": "observer",
        }
        panel = roles_in_panel(app)
        assert panel.count("★ lead") == 1
        assert "observer" in panel
        await until(pilot, lambda: "lead → observer" in thread_text(app))


@pytest.mark.anyio
async def test_escape_cancels_the_lead_confirmation(make_app: Any, rest: Any, call: Any) -> None:
    await make_team(rest, call)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await submit(pilot, app, "/role codex lead")
        await until(pilot, lambda: app.mode == "confirm")
        await pilot.press("escape")
        await until(pilot, lambda: app.mode == "normal")
        room = await server_room(rest, "alpha")
        assert room is not None
        assert {m["agent"]: m["role"] for m in room["members"]}["codex"] == "reviewer"


@pytest.mark.anyio
async def test_role_for_a_non_member_shows_the_servers_message(make_app: Any, rest: Any, call: Any) -> None:
    await make_team(rest, call)
    expected = (
        await rest(
            "human",
            "POST",
            "/api/rooms/alpha/members/role",
            json={"agent": "ghost", "alias": "", "role": "writer"},
        )
    ).json()["message"]
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await submit(pilot, app, "/role ghost writer")
        await until(pilot, lambda: app.status_is_error)
        assert app.status_text == expected
        assert app.mode == "normal"


@pytest.mark.anyio
async def test_role_needs_a_selected_room_and_valid_arguments(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/role codex writer")
        await until(pilot, lambda: "/room" in app.status_text)
        assert app.status_is_error
        await submit(pilot, app, "/role codex")
        await until(pilot, lambda: app.status_text.startswith("Usage: /role"))
        await submit(pilot, app, "/role codex boss")
        await until(pilot, lambda: "boss" in app.status_text)


@pytest.mark.anyio
async def test_tab_completes_member_names_and_roles(make_app: Any, rest: Any, call: Any) -> None:
    await make_team(rest, call)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["alpha"])
        await submit(pilot, app, "/room alpha")
        await until(pilot, lambda: app.state.summary is not None and len(app.state.summary.members) == 2)
        field = app.query_one(CommandInput)
        await pilot.press(*"/role co", "tab")
        assert field.value == "/role codex "
        await pilot.press(*"lea", "tab")
        assert field.value == "/role codex lead"
        field.value = ""
        await pilot.press(*"/role cl", "tab")
        assert field.value == "/role claude/main "
        await pilot.press("tab")
        assert "writer" in app.status_text
        assert "observer" in app.status_text


@pytest.mark.anyio
async def test_close_asks_for_confirmation_and_cancel_keeps_the_room_open(make_app: Any, rest: Any) -> None:
    await make_room(rest)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await until(pilot, lambda: app.state.slugs == ["r1"])
        await submit(pilot, app, "/room r1")
        await submit(pilot, app, "/close")
        await until(pilot, lambda: app.mode == "confirm")
        assert "r1" in app.status_text
        await submit(pilot, app, "nope")
        await until(pilot, lambda: app.mode == "normal")
        room = await server_room(rest)
        assert room is not None
        assert room["status"] == "active"

        await submit(pilot, app, "/close")
        await until(pilot, lambda: app.mode == "confirm")
        await pilot.press("escape")
        await until(pilot, lambda: app.mode == "normal")
        room = await server_room(rest)
        assert room is not None
        assert room["status"] == "active"

        await submit(pilot, app, "/close")
        await until(pilot, lambda: app.mode == "confirm")
        await submit(pilot, app, "r1")
        await until(pilot, lambda: app.state.summary is not None and app.state.summary.status == "closed")
        room = await server_room(rest)
        assert room is not None
        assert room["status"] == "closed"


@pytest.mark.anyio
async def test_close_without_a_room_does_not_ask(make_app: Any) -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await submit(pilot, app, "/close")
        await until(pilot, lambda: app.status_is_error)
        assert app.mode == "normal"
