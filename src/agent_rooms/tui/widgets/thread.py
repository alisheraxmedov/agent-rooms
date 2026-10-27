import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message as TextualMessage
from textual.widgets import Static

from agent_rooms.tui.api import Message
from agent_rooms.tui.render import kind_class, message_content


class MessageView(Static):
    def __init__(self, message: Message) -> None:
        super().__init__(message_content(message), markup=False, classes=kind_class(message.kind))
        self.message = message


class Thread(VerticalScroll, can_focus=False):
    class ReachedBottom(TextualMessage):
        pass

    @property
    def at_bottom(self) -> bool:
        return self.scroll_y >= self.max_scroll_y - 1

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self.at_bottom:
            self.post_message(self.ReachedBottom())


class ThreadPane(Vertical):
    """Messages of the selected room. Follows the bottom; when scrolled up it only counts what arrived."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.unseen = 0
        self.room: str | None = None
        self._lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Thread(id="thread")
        yield Static("", id="new-indicator", markup=False)

    @property
    def thread(self) -> Thread:
        return self.query_one(Thread)

    def _show_unseen(self) -> None:
        indicator = self.query_one("#new-indicator", Static)
        indicator.update(Content(f"↓ {self.unseen} new · PgDn") if self.unseen else "")
        indicator.display = bool(self.unseen)

    def on_mount(self) -> None:
        self._show_unseen()

    def on_thread_reached_bottom(self) -> None:
        self.unseen = 0
        self._show_unseen()

    async def reset(self, text: str, room: str | None = None) -> None:
        async with self._lock:
            self.room = room
            self.unseen = 0
            self._show_unseen()
            await self.thread.remove_children()
            await self.thread.mount(Static(Content(text), id="placeholder", markup=False))

    async def show_empty(self, room: str, text: str) -> None:
        async with self._lock:
            if room == self.room:
                for placeholder in self.thread.query("#placeholder").results(Static):
                    placeholder.update(Content(text))

    async def add(self, room: str, messages: list[Message], *, announce: bool) -> None:
        async with self._lock:
            if not messages or room != self.room:
                return
            follow = not announce or self.thread.at_bottom
            await self.thread.query("#placeholder").remove()
            await self.thread.mount(*(MessageView(message) for message in messages))
            if follow:
                self.call_after_refresh(self.thread.scroll_end, animate=False)
            else:
                self.unseen += len(messages)
                self._show_unseen()
