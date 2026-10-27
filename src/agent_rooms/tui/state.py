from dataclasses import dataclass, field

from agent_rooms.tui.api import Message, Summary, Thread

HISTORY_LIMIT = 200


class InputHistory:
    """Shell-style recall of previously submitted lines; keeps the unsent draft while browsing."""

    def __init__(self, limit: int = HISTORY_LIMIT) -> None:
        self._limit = limit
        self._entries: list[str] = []
        self._cursor: int | None = None
        self._draft = ""

    def push(self, line: str) -> None:
        self._cursor = None
        entry = line.strip()
        if entry and (not self._entries or self._entries[-1] != entry):
            self._entries.append(entry)
            del self._entries[: -self._limit]

    def previous(self, current: str) -> str | None:
        if not self._entries:
            return None
        if self._cursor is None:
            self._draft = current
            self._cursor = len(self._entries)
        self._cursor = max(0, self._cursor - 1)
        return self._entries[self._cursor]

    def following(self) -> str | None:
        if self._cursor is None:
            return None
        self._cursor += 1
        if self._cursor >= len(self._entries):
            self._cursor = None
            return self._draft
        return self._entries[self._cursor]


@dataclass(slots=True)
class ViewState:
    rooms: list[Summary] = field(default_factory=list)
    selected: str | None = None
    summary: Summary | None = None
    messages: list[Message] = field(default_factory=list)
    last_id: int = 0
    offline: bool = False
    rooms_loaded: bool = False

    @property
    def slugs(self) -> list[str]:
        return [room.slug for room in self.rooms]

    @property
    def room_missing(self) -> bool:
        return self.selected is not None and self.summary is None and self.rooms_loaded

    def _find(self, slug: str | None) -> Summary | None:
        return next((room for room in self.rooms if room.slug == slug), None)

    def select(self, slug: str) -> None:
        self.selected = slug
        self.summary = self._find(slug)
        self.messages = []
        self.last_id = 0

    def apply_rooms(self, rooms: list[Summary]) -> None:
        self.rooms = rooms
        self.rooms_loaded = True
        self.summary = self._find(self.selected)

    def upsert_room(self, summary: Summary) -> None:
        rooms = list(self.rooms)
        for index, room in enumerate(rooms):
            if room.slug == summary.slug:
                rooms[index] = summary
                break
        else:
            rooms.append(summary)
        self.rooms = rooms
        if self.selected == summary.slug:
            self.summary = summary

    def apply_thread(self, slug: str, thread: Thread) -> list[Message]:
        if slug != self.selected:
            return []
        self.summary = thread.room
        fresh = [message for message in thread.messages if message.id > self.last_id]
        if fresh:
            self.messages.extend(fresh)
            self.last_id = fresh[-1].id
        return fresh

    def remove_room(self, slug: str) -> None:
        self.rooms = [room for room in self.rooms if room.slug != slug]
        if self.selected == slug:
            self.selected = None
            self.summary = None
            self.messages = []
            self.last_id = 0

    def clear(self) -> None:
        self.rooms = []
        self.selected = None
        self.summary = None
        self.messages = []
        self.last_id = 0
        self.rooms_loaded = False
