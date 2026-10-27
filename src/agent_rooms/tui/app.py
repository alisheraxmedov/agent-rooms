import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Static
from textual.worker import Worker

from agent_rooms.tui.api import HISTORY_PAGE, ApiError, RoomsApi
from agent_rooms.tui.commands import (
    Delete,
    Help,
    Invalid,
    Login,
    Logout,
    New,
    Quit,
    Room,
    Say,
    SetRole,
    SetStatus,
    Task,
    parse,
)
from agent_rooms.tui.credentials import CredentialsError, TokenStore
from agent_rooms.tui.state import ViewState
from agent_rooms.tui.suggest import suggest
from agent_rooms.tui.widgets.composer import CommandInput
from agent_rooms.tui.widgets.help import HelpScreen
from agent_rooms.tui.widgets.palette import SuggestionPopup
from agent_rooms.tui.widgets.panels import ConnectionBadge, MembersPanel, RoomHeader, RoomList, StatusLine
from agent_rooms.tui.widgets.thread import ThreadPane

NORMAL, LOGIN, CONFIRM = "normal", "login", "confirm"
HINT = "Enter send · / commands · ↑↓ history · PgUp/PgDn scroll · /help"
NEED_LOGIN = "Token missing or rejected. Type /login to sign in."
NOT_SIGNED_IN = "Not signed in. Type /login."
NO_ROOM = "No room selected. Type /room <slug> to open one or /new <slug> <topic> to create one."
NARROW_WIDTH, TINY_WIDTH = 100, 70


@dataclass(frozen=True, slots=True)
class Confirmation:
    """A pending action that waits for the user to type one of `answers` in the input line."""

    title: str
    placeholder: str
    answers: tuple[str, ...]
    run: Callable[[], Awaitable[None]]
    cancelled: str


class AgentRoomsApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "Agent Rooms"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, api: RoomsApi, store: TokenStore, *, poll_interval: float = 2.0) -> None:
        super().__init__()
        self.api = api
        self.store = store
        self.state = ViewState()
        self.mode = NORMAL
        self._poll_interval = poll_interval
        self._confirmation: Confirmation | None = None
        self._refresh_lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                Content.assemble(("Agent Rooms", "bold"), ("  ", ""), (self.api.base_url, "dim")),
                id="title",
                markup=False,
            )
            yield ConnectionBadge(id="conn")
        with Horizontal(id="body"):
            with Vertical(id="rooms-pane"):
                yield Static("ROOMS", classes="pane-title")
                yield RoomList(id="rooms")
            with Vertical(id="main"):
                yield RoomHeader(id="room-header")
                yield ThreadPane(id="thread-pane")
            with Vertical(id="members-pane"):
                yield Static("MEMBERS", classes="pane-title")
                yield MembersPanel(id="members")
        yield StatusLine(id="status")
        with Horizontal(id="composer"):
            yield Static("\u203a", id="prompt")
            yield CommandInput(id="input")
        yield SuggestionPopup(id="palette")

    @property
    def status_text(self) -> str:
        return self.query_one(StatusLine).text

    @property
    def status_is_error(self) -> bool:
        return self.query_one(StatusLine).is_error

    @property
    def _input(self) -> CommandInput:
        return self.query_one(CommandInput)

    @property
    def _pane(self) -> ThreadPane:
        return self.query_one(ThreadPane)

    async def on_mount(self) -> None:
        self._input.palette = self.query_one(SuggestionPopup)
        self._input.focus()
        self._enter_mode(NORMAL)
        self._apply_width(self.size.width)
        self._render_all()
        if self.api.has_token:
            self._hint()
            self._connection()
            self._refresh_now()
        else:
            await self._signed_out("No token yet. Type /login to sign in.", error=False)
        self.set_interval(self._poll_interval, self._tick)

    async def on_unmount(self) -> None:
        await self.api.aclose()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width(event.size.width)

    def _apply_width(self, width: int) -> None:
        self.screen.set_class(width < NARROW_WIDTH, "-narrow")
        self.screen.set_class(width < TINY_WIDTH, "-tiny")

    def _refresh_palette(self) -> None:
        field, popup = self._input, self.query_one(SuggestionPopup)
        if field.value != field.quiet_value:
            field.quiet_value = None
        items = []
        if self.mode == NORMAL and field.quiet_value is None:
            summary = self.state.summary
            items = suggest(field.value, self.state.rooms, summary.members if summary else ())
        if items:
            popup.show(items)
        else:
            popup.hide()

    def on_input_changed(self, event: CommandInput.Changed) -> None:
        if event.input is self._input:
            self._refresh_palette()

    def on_command_input_refresh(self) -> None:
        self._refresh_palette()

    # status line and connection

    def _status(self, text: str, kind: str) -> None:
        self.query_one(StatusLine).show(text, kind)

    def _hint(self) -> None:
        self._status(HINT, "hint")

    def _info(self, text: str) -> None:
        self._status(text, "info")

    def _error(self, text: str) -> None:
        self._status(text, "error")

    def _connection(self) -> None:
        state = "signed-out" if not self.api.has_token else ("offline" if self.state.offline else "connected")
        self.query_one(ConnectionBadge).show(state)

    # rendering

    def _render_rooms(self) -> None:
        self.query_one(RoomList).show(self.state.rooms, self.state.selected)
        self._refresh_palette()

    def _render_room(self) -> None:
        self.query_one(RoomHeader).show(
            self.state.summary, selected=self.state.selected, missing=self.state.room_missing
        )
        self.query_one(MembersPanel).show(self.state.summary)
        self._refresh_palette()

    def _render_all(self) -> None:
        self._render_rooms()
        self._render_room()
        self._connection()

    # polling

    def _tick(self) -> None:
        if self.api.has_token and not self._refresh_lock.locked():
            self._refresh_now()

    def _refresh_now(self) -> Worker[None]:
        return self.run_worker(self._refresh(), group="refresh", exit_on_error=False)

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            if not self.api.has_token:
                return
            try:
                rooms = await self.api.list_rooms()
                self._back_online()
                self.state.apply_rooms(rooms)
                self._render_rooms()
                self._render_room()
                slug = self.state.selected
                if slug is not None and not self.state.room_missing:
                    await self._load_messages(slug)
            except ApiError as error:
                await self._on_failure(error, quiet=True)

    async def _load_messages(self, slug: str) -> None:
        initial = not self.state.messages
        while True:
            thread = await self.api.history(slug, after=self.state.last_id, limit=HISTORY_PAGE)
            if slug != self.state.selected:
                return
            fresh = self.state.apply_thread(slug, thread)
            self._render_room()
            if not fresh and initial:
                await self._pane.show_empty(slug, "No messages yet.")
            await self._pane.add(slug, fresh, announce=not initial)
            if not fresh or len(thread.messages) < HISTORY_PAGE:
                return

    def _back_online(self) -> None:
        if self.state.offline:
            self.state.offline = False
            self._hint()
        self._connection()

    async def _on_failure(self, error: ApiError, *, quiet: bool = False) -> None:
        if error.unauthorized:
            await self._signed_out(NEED_LOGIN, error=True)
        elif error.offline:
            self.state.offline = True
            self._connection()
            if not quiet:
                self._error("Cannot reach the server. It will retry automatically.")
        elif not (quiet and error.status == 404):
            self._error(error.message)

    async def _signed_out(self, message: str, *, error: bool) -> None:
        self.api.set_token(None)
        self.state.clear()
        self.state.offline = False
        self._render_all()
        await self._pane.reset(NOT_SIGNED_IN)
        self._status(message, "error" if error else "info")

    # input

    def _enter_mode(self, mode: str, *, title: str = "", placeholder: str = "") -> None:
        self.mode = mode
        field, composer = self._input, self.query_one("#composer")
        field.password = mode == LOGIN
        field.recall_enabled = mode == NORMAL
        composer.set_classes(mode if mode != NORMAL else "")
        titles = {
            NORMAL: f"message {self.state.selected}" if self.state.selected else "no room selected",
            LOGIN: "sign in · token is hidden · Esc cancels",
            CONFIRM: f"{title} · Esc cancels",
        }
        composer.border_title = Content(titles[mode])
        field.placeholder = {
            NORMAL: "Message the room, or type / for commands",
            LOGIN: "Paste the human token",
            CONFIRM: placeholder,
        }[mode]
        self._refresh_palette()

    def on_input_submitted(self, event: CommandInput.Submitted) -> None:
        value = event.value
        event.input.value = ""
        if self.mode == NORMAL:
            self._input.history.push(value)
            self._hint()
            handler: Awaitable[None] = self._handle_line(value)
        elif self.mode == LOGIN:
            handler = self._handle_token(value)
        else:
            handler = self._handle_confirmation(value)
        self.run_worker(self._serialized(handler), group="actions", exit_on_error=False)

    async def _serialized(self, work: Awaitable[None]) -> None:
        async with self._action_lock:
            await work

    def action_cancel(self) -> None:
        popup = self.query_one(SuggestionPopup)
        if popup.display:
            self._input.quiet_value = self._input.value
            popup.hide()
        elif self.mode != NORMAL:
            self._confirmation = None
            self._enter_mode(NORMAL)
            self._hint()
        else:
            self._input.value = ""

    def action_scroll_thread(self, direction: int) -> None:
        thread = self._pane.thread
        if direction < 0:
            thread.scroll_page_up(animate=False)
        else:
            thread.scroll_page_down(animate=False)

    # commands

    async def _attempt[T](self, operation: Awaitable[T]) -> T | None:
        try:
            result = await operation
        except ApiError as error:
            await self._on_failure(error)
            return None
        self._back_online()
        return result

    async def _handle_admin(self, command: SetStatus | SetRole | Delete) -> None:
        match command:
            case SetStatus("closed"):
                self._ask_close()
            case SetStatus(status):
                await self._set_status(status)
            case SetRole():
                await self._role(command)
            case Delete(slug):
                self._ask_delete(slug)

    async def _succeeded(self, operation: Awaitable[object]) -> bool:
        async def run() -> bool:
            await operation
            return True

        return await self._attempt(run()) is True

    async def _handle_line(self, line: str) -> None:
        match parse(line):
            case None:
                return
            case Say(text):
                await self._post(text, "chat")
            case Task(text):
                await self._post(text, "task")
            case Login():
                self._enter_mode(LOGIN)
                self._info("Paste the human token and press Enter. It is masked and kept in the OS keyring.")
            case Logout():
                await self._logout()
            case Room(slug):
                await self._switch(slug)
            case New(slug, topic):
                await self._create(slug, topic)
            case SetStatus() | SetRole() | Delete() as admin:
                await self._handle_admin(admin)
            case Help():
                await self.push_screen(HelpScreen())
            case Quit():
                self.exit()
            case Invalid(message):
                self._error(message)

    async def _post(self, text: str, kind: str) -> None:
        slug = self.state.selected
        if slug is None:
            self._error(NO_ROOM)
        elif await self._attempt(self.api.post(slug, text, kind)) is not None:
            self._refresh_now()

    async def _select(self, slug: str) -> None:
        self.state.select(slug)
        self._enter_mode(self.mode)
        self._render_all()
        await self._pane.reset("Loading messages…", slug)
        self._refresh_now()

    async def _switch(self, slug: str) -> None:
        if slug not in self.state.slugs:
            self._error(f"No room named {slug}. Known rooms: {', '.join(self.state.slugs) or 'none'}.")
        else:
            await self._select(slug)

    async def _create(self, slug: str, topic: str) -> None:
        summary = await self._attempt(self.api.create_room(slug, topic))
        if summary is not None:
            self.state.upsert_room(summary)
            await self._select(summary.slug)
            self._info(f"Created room {summary.slug}.")

    async def _set_status(self, status: str, slug: str | None = None) -> None:
        slug = slug or self.state.selected
        if slug is None:
            self._error(NO_ROOM)
            return
        summary = await self._attempt(self.api.set_status(slug, status))
        if summary is not None:
            self.state.upsert_room(summary)
            self._render_rooms()
            self._render_room()
            self._info(f"Room {slug} is now {summary.status}.")
            self._refresh_now()

    def _ask(self, confirmation: Confirmation, prompt: str) -> None:
        self._confirmation = confirmation
        self._enter_mode(CONFIRM, title=confirmation.title, placeholder=confirmation.placeholder)
        self._info(prompt)

    def _ask_close(self) -> None:
        slug = self.state.selected
        if slug is None:
            self._error(NO_ROOM)
            return
        self._ask(
            Confirmation(
                title=f"close {slug} · type yes or its slug",
                placeholder="Type yes or the room slug",
                answers=("yes", slug),
                run=lambda: self._set_status("closed", slug),
                cancelled="Close cancelled.",
            ),
            f"Close {slug}? Agents can no longer post. Type yes or {slug} and press Enter.",
        )

    def _ask_delete(self, slug: str | None) -> None:
        target = slug or self.state.selected
        if target is None:
            self._error(NO_ROOM)
            return
        known = next((room for room in self.state.rooms if room.slug == target), None)
        if known is None:
            self._error(f"No room named {target}.")
        elif known.status != "closed":
            self._error(
                f"Room {target} is {known.status}. Only closed rooms can be deleted; type /close first."
            )
        else:
            self._ask(
                Confirmation(
                    title=f"delete {target} · type its slug",
                    placeholder="Type the room slug",
                    answers=(target,),
                    run=lambda: self._delete(target),
                    cancelled="Delete cancelled.",
                ),
                f"This deletes {target} and all its messages for good. Type {target} and press Enter.",
            )

    async def _delete(self, target: str) -> None:
        if not await self._succeeded(self.api.delete_room(target)):
            return
        self.state.remove_room(target)
        self._render_all()
        if self.state.selected is None:
            await self._pane.reset("Select a room with /room <slug>.")
        self._enter_mode(NORMAL)
        self._info(f"Deleted room {target}.")

    async def _role(self, change: SetRole) -> None:
        slug = self.state.selected
        if slug is None:
            self._error(NO_ROOM)
            return
        if change.role != "lead":
            await self._assign_role(slug, change)
            return
        members = self.state.summary.members if self.state.summary else ()
        current = next((m.name for m in members if m.role == "lead" and m.name != change.member), None)
        demotion = f" {current} becomes an observer." if current else ""
        self._ask(
            Confirmation(
                title=f"make {change.member} lead · type yes",
                placeholder="Type yes to confirm",
                answers=("yes",),
                run=lambda: self._assign_role(slug, change),
                cancelled="Role change cancelled.",
            ),
            f"Make {change.member} the lead of {slug}?{demotion} Type yes and press Enter.",
        )

    async def _assign_role(self, slug: str, change: SetRole) -> None:
        summary = await self._attempt(self.api.set_role(slug, change.agent, change.alias, change.role))
        if summary is None:
            return
        self.state.upsert_room(summary)
        self._render_rooms()
        self._render_room()
        self._info(f"{change.member} is now {change.role} in {slug}.")
        self._refresh_now()

    async def _handle_confirmation(self, value: str) -> None:
        pending, self._confirmation = self._confirmation, None
        self._enter_mode(NORMAL)
        if pending is None or value.strip().lower() not in {answer.lower() for answer in pending.answers}:
            self._info(pending.cancelled if pending else "Cancelled.")
            return
        await pending.run()

    async def _handle_token(self, value: str) -> None:
        token = value.strip()
        if not token:
            self._enter_mode(NORMAL)
            self._hint()
            return
        try:
            rooms = await self.api.probe(token)
        except ApiError as error:
            if error.unauthorized:
                self._error("Token rejected. Paste it again, or press Esc to cancel.")
            elif error.offline:
                self._error("Cannot reach the server, so the token was not saved. Try again, or press Esc.")
            else:
                self._error(error.message)
            return
        self.api.set_token(token)
        self.state.clear()
        self.state.apply_rooms(rooms)
        self._enter_mode(NORMAL)
        self._render_all()
        await self._pane.reset(NO_ROOM)
        try:
            self.store.save(token)
        except CredentialsError as error:
            self._error(f"Signed in for this session only. {error}")
        else:
            note = " ROOMS_HUMAN_TOKEN takes precedence next time." if self.store.source() == "env" else ""
            self._status("Signed in. The token is saved in the OS keyring." + note, "ok")
        self._refresh_now()

    async def _logout(self) -> None:
        try:
            self.store.delete()
        except CredentialsError as error:
            message, kind = f"Signed out, but the saved token could not be removed. {error}", "error"
        else:
            message, kind = "Signed out. The saved token is gone. Type /login to sign in again.", "info"
        if self.store.source() == "env":
            message += " ROOMS_HUMAN_TOKEN is still set in the environment."
        await self._signed_out(message, error=False)
        self._status(message, kind)
