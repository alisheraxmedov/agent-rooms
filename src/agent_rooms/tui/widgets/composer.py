from typing import Any, ClassVar

from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Input

from agent_rooms.tui.state import InputHistory
from agent_rooms.tui.widgets.palette import SuggestionPopup


class CommandInput(Input):
    """The single input line: history on ↑/↓, completion on Tab, PgUp/PgDn scroll the thread."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "recall_previous", show=False),
        Binding("down", "recall_next", show=False),
        Binding("tab", "complete", show=False),
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
