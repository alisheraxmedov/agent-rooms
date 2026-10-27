from collections.abc import Callable
from typing import Any, ClassVar

from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Input

from agent_rooms.tui.commands import Completion
from agent_rooms.tui.state import InputHistory


class CommandInput(Input):
    """The single input line: history on ↑/↓, completion on Tab, PgUp/PgDn scroll the thread."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "recall_previous", show=False),
        Binding("down", "recall_next", show=False),
        Binding("tab", "complete", show=False),
        Binding("pageup", "app.scroll_thread(-1)", show=False),
        Binding("pagedown", "app.scroll_thread(1)", show=False),
    ]

    class Hint(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history = InputHistory()
        self.recall_enabled = True
        self.completer: Callable[[str], Completion | None] | None = None

    def _replace(self, value: str) -> None:
        self.value = value
        self.cursor_position = len(value)

    def action_recall_previous(self) -> None:
        if self.recall_enabled and (entry := self.history.previous(self.value)) is not None:
            self._replace(entry)

    def action_recall_next(self) -> None:
        if self.recall_enabled and (entry := self.history.following()) is not None:
            self._replace(entry)

    def action_complete(self) -> None:
        completion = self.completer(self.value) if self.completer else None
        if completion is None:
            return
        self._replace(completion.line)
        if completion.options:
            self.post_message(self.Hint("  ".join(completion.options)))
