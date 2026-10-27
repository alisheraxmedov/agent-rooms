from textual.content import Content
from textual.widgets import Static

from agent_rooms.tui.api import Summary
from agent_rooms.tui.render import header_content, member_content, room_line

DEFAULT_WIDTH = 28


def _dim(text: str) -> Content:
    return Content.styled(text, "dim")


class RoomList(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]
        self._rooms: list[Summary] = []
        self._selected: str | None = None

    def show(self, rooms: list[Summary], selected: str | None) -> None:
        self._rooms, self._selected = rooms, selected
        self._redraw()

    def on_resize(self) -> None:
        self._redraw()

    def _redraw(self) -> None:
        if not self._rooms:
            self.update(_dim("No rooms yet.\n/new <slug> <topic>"))
            return
        width = self.content_size.width or DEFAULT_WIDTH
        lines = [room_line(room, selected=room.slug == self._selected, width=width) for room in self._rooms]
        self.update(Content("\n").join(lines))


class MembersPanel(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]

    def show(self, summary: Summary | None) -> None:
        if summary is None:
            self.update("")
        elif not summary.members:
            self.update(_dim("Nobody has joined yet."))
        else:
            self.update(Content("\n").join(member_content(member) for member in summary.members))


class RoomHeader(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]

    def show(self, summary: Summary | None, *, selected: str | None, missing: bool) -> None:
        if summary is not None:
            self.update(header_content(summary))
        elif missing:
            self.update(Content.styled("This room no longer exists.", "$warning"))
        elif selected is not None:
            self.update(_dim("Loading…"))
        else:
            self.update(
                _dim("No room selected. /room <slug> to open one, /new <slug> <topic> to create one.")
            )


class StatusLine(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]
        self.text = ""
        self.is_error = False

    def show(self, text: str, kind: str = "hint") -> None:
        self.text = text
        self.is_error = kind == "error"
        self.set_classes(kind)
        self.update(Content(text))


class ConnectionBadge(Static):
    def __init__(self, **kwargs: object) -> None:
        super().__init__("", markup=False, **kwargs)  # type: ignore[arg-type]

    def show(self, state: str) -> None:
        label, style = {
            "connected": ("● connected", "$success"),
            "offline": ("○ offline, retrying…", "$warning"),
        }.get(state, ("○ signed out", "dim"))
        self.update(Content.styled(label, style))
