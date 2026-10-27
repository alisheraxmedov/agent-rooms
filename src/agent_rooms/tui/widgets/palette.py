from textual.content import Content
from textual.widget import Widget
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from agent_rooms.tui.suggest import Suggestion

MAX_ROWS = 8
CHROME = 5  # border, option padding and scrollbar around the text
GAP = "  "
MAX_DESCRIPTION = 40
MIN_HINT = 8


def _shape_of(items: list[Suggestion]) -> tuple[str, int]:
    tokens = items[0].insert.split()
    return tokens[0], len(tokens)


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


class SuggestionPopup(OptionList, can_focus=False):
    """Slash-command and argument suggestions, drawn as an overlay directly above the input box."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(markup=False, **kwargs)  # type: ignore[arg-type]
        self.items: list[Suggestion] = []
        self._widths = (0, 0)
        self._shape: tuple[str, int] | None = None
        self.display = False

    @property
    def selected_item(self) -> Suggestion | None:
        if not self.display or self.highlighted is None or not self.items:
            return None
        return self.items[self.highlighted]

    def show(self, items: list[Suggestion]) -> None:
        if self.display and items == self.items:
            return
        kept = self.selected_item.insert if self.selected_item else None
        shape = _shape_of(items)
        if shape != self._shape:
            self._widths = (0, 0)
        self._shape = shape
        self._widths = (
            max(self._widths[0], *(len(item.name) for item in items)),
            max(self._widths[1], *(len(item.hint) for item in items)),
        )
        self.items = items
        self._draw(next((i for i, item in enumerate(items) if item.insert == kept), 0))
        self.styles.height = min(len(items), MAX_ROWS) + 2
        self.display = True

    def hide(self) -> None:
        self.display = False
        self.items = []
        self._widths = (0, 0)
        self._shape = None

    def move(self, delta: int) -> None:
        if self.items and self.highlighted is not None:
            self.highlighted = (self.highlighted + delta) % len(self.items)

    def on_resize(self) -> None:
        if self.display and self.items:
            self._draw(self.highlighted or 0)

    def _row(self, item: Suggestion, room: int) -> Content:
        name_width, widest_hint = self._widths
        wanted = min(max(len(row.description) for row in self.items), MAX_DESCRIPTION)
        hint_width = min(widest_hint, max(room - name_width - 2 * len(GAP) - wanted, MIN_HINT))
        used = name_width + len(GAP) + hint_width + len(GAP)
        return Content.assemble(
            (item.name.ljust(name_width), "bold $accent"),
            GAP,
            (_fit(item.hint, hint_width).ljust(hint_width), "dim"),
            GAP,
            _fit(item.description, max(room - used, 0)),
        )

    def _draw(self, selected: int) -> None:
        column = self.parent.size.width if isinstance(self.parent, Widget) else 0
        room = max((column or self.app.size.width) - CHROME, 20)
        self.clear_options()
        self.add_options(Option(self._row(item, room)) for item in self.items)
        self.highlighted = selected
