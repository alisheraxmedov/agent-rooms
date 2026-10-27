from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from agent_rooms.tui.commands import COMMANDS

KEYS = (
    ("Enter", "send the line"),
    ("↑ / ↓", "recall earlier input; pick a suggestion"),
    ("Tab", "insert the selected suggestion"),
    ("PgUp / PgDn", "scroll the thread"),
    ("Esc", "close suggestions; cancel a prompt"),
    ("Ctrl+C", "quit"),
)


def help_content() -> Content:
    width = max(len(spec.usage) for spec in COMMANDS)
    parts: list[str | tuple[str, str]] = [("Commands\n", "bold")]
    for spec in COMMANDS:
        parts += [(spec.usage.ljust(width + 2), "$primary"), spec.description, "\n"]
    parts += [("\nKeys\n", "bold")]
    for key, meaning in KEYS:
        parts += [(key.ljust(width + 2), "$primary"), meaning, "\n"]
    parts += [
        ("\nType / to see the commands. Plain text posts a chat message; // sends a leading slash.", "dim")
    ]
    return Content.assemble(*parts)


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS: ClassVar[str] = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical {
        width: 84; max-width: 96%; height: auto; max-height: 90%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(help_content(), markup=False)
            yield Static(Content.styled("Press any key to close.", "dim"), markup=False)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        self.dismiss()
