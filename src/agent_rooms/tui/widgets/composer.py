from collections.abc import Callable
from typing import Any, ClassVar

from textual import events
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Input

from agent_rooms.tui.state import InputHistory
from agent_rooms.tui.text import clean
from agent_rooms.tui.widgets.palette import SuggestionPopup


class CommandInput(Input):
    """The single input line: history, suggestions, paste, and PgUp/PgDn to scroll the thread."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "recall_previous", show=False),
        Binding("down", "recall_next", show=False),
        Binding("tab", "complete", show=False),
        Binding("super+v", "paste", show=False),
        Binding("pageup", "app.scroll_thread(-1)", show=False),
        Binding("pagedown", "app.scroll_thread(1)", show=False),
    ]

    class Refresh(Message):
        """Asks the app to recompute the suggestions for the current text."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history = InputHistory()
        self.recall_enabled = True
        self.palette: SuggestionPopup | None = None
        self.quiet_value: str | None = None
        self.paste_source: Callable[[], str | None] | None = None

    @property
    def _palette_open(self) -> bool:
        return self.palette is not None and self.palette.display

    def _replace(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)

    def _recall(self, entry: str) -> None:
        self.quiet_value = entry
        self._replace(entry)

    def action_recall_previous(self) -> None:
        if self.palette is not None and self._palette_open:
            self.palette.move(-1)
        elif self.recall_enabled and (entry := self.history.previous(self.value)) is not None:
            self._recall(entry)

    def action_recall_next(self) -> None:
        if self.palette is not None and self._palette_open:
            self.palette.move(1)
        elif self.recall_enabled and (entry := self.history.following()) is not None:
            self._recall(entry)

    def action_complete(self) -> None:
        item = self.palette.selected_item if self.palette is not None else None
        if item is not None:
            self._replace(item.insert)
        elif self.recall_enabled and self.value.startswith("/"):
            self.quiet_value = None
            self.post_message(self.Refresh())

    async def action_submit(self) -> None:
        item = self.palette.selected_item if self.palette is not None else None
        if item is not None and not item.is_typed(self.value):
            self._replace(item.insert)
            return
        await super().action_submit()

    def insert_pasted(self, text: str) -> None:
        """Insert pasted text at the cursor; a multi-line paste becomes one line joined with spaces."""
        line = " ".join(part.strip() for part in clean(text).split("\n") if part.strip())
        if line:
            start, end = self.selection
            self.replace(line, start, end)

    def _on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        self.insert_pasted(event.text)

    def action_paste(self) -> None:
        text = self.paste_source() if self.paste_source else None
        self.insert_pasted(text or self.app.clipboard)
