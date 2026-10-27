import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from agent_rooms.domain.errors import RoomAlreadyExists
from agent_rooms.domain.models import (
    HUMAN,
    SYSTEM,
    Member,
    Message,
    MessageKind,
    Role,
    Room,
    RoomStatus,
)
from agent_rooms.infrastructure.sqlite.schema import migrate

_BUSY_TIMEOUT_MS = 5000


def _to_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def _to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else _to_datetime(value)


def _room(row: sqlite3.Row) -> Room:
    return Room(
        slug=row["slug"],
        topic=row["topic"],
        status=RoomStatus(row["status"]),
        max_messages=row["max_messages"],
        budget_start_id=row["budget_start_id"],
        created_at=_to_datetime(row["created_at"]),
    )


def _member(row: sqlite3.Row) -> Member:
    return Member(
        room=row["room"],
        agent=row["agent"],
        alias=row["alias"],
        role=Role(row["role"]),
        last_read_id=row["last_read_id"],
        listening_until=_optional_datetime(row["listening_until"]),
        listener_session=row["listener_session"],
        joined_at=_to_datetime(row["joined_at"]),
    )


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        room=row["room"],
        sender=row["sender"],
        alias=row["alias"],
        kind=MessageKind(row["kind"]),
        text=row["text"],
        reply_to=row["reply_to"],
        created_at=_to_datetime(row["created_at"]),
    )


class _Database:
    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self.connection = connection
        self.lock = lock

    def run(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.connection.execute(sql, params)

    def one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self.lock:
            row: sqlite3.Row | None = self.connection.execute(sql, params).fetchone()
            return row

    def all(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.connection.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple[object, ...] = ()) -> int:
        row = self.one(sql, params)
        assert row is not None  # noqa: S101 - aggregate queries always return a row
        return int(row[0])


class SqliteRoomRepository:
    def __init__(self, db: _Database) -> None:
        self._db = db

    def add(self, room: Room) -> None:
        try:
            self._db.run(
                "INSERT INTO rooms(slug, topic, status, max_messages, budget_start_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    room.slug,
                    room.topic,
                    room.status.value,
                    room.max_messages,
                    room.budget_start_id,
                    _to_text(room.created_at),
                ),
            )
        except sqlite3.IntegrityError:
            raise RoomAlreadyExists(f"Room '{room.slug}' already exists.") from None

    def get(self, slug: str) -> Room | None:
        row = self._db.one("SELECT * FROM rooms WHERE slug = ?", (slug,))
        return None if row is None else _room(row)

    def list_all(self) -> list[Room]:
        return [_room(row) for row in self._db.all("SELECT * FROM rooms ORDER BY created_at, slug")]

    def set_status(self, slug: str, status: RoomStatus) -> None:
        self._db.run("UPDATE rooms SET status = ? WHERE slug = ?", (status.value, slug))

    def reset_budget(self, slug: str, budget_start_id: int) -> None:
        self._db.run("UPDATE rooms SET budget_start_id = ? WHERE slug = ?", (budget_start_id, slug))

    def delete(self, slug: str) -> bool:
        return self._db.run("DELETE FROM rooms WHERE slug = ?", (slug,)).rowcount > 0


class SqliteMemberRepository:
    def __init__(self, db: _Database) -> None:
        self._db = db

    def upsert(self, member: Member) -> None:
        self._db.run(
            "INSERT INTO members(room, agent, alias, role, last_read_id, listening_until, "
            "listener_session, joined_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(room, agent, alias) DO UPDATE SET role = excluded.role, "
            "last_read_id = excluded.last_read_id, listening_until = excluded.listening_until, "
            "listener_session = excluded.listener_session, joined_at = excluded.joined_at",
            (
                member.room,
                member.agent,
                member.alias,
                member.role.value,
                member.last_read_id,
                None if member.listening_until is None else _to_text(member.listening_until),
                member.listener_session,
                _to_text(member.joined_at),
            ),
        )

    def get(self, room: str, agent: str, alias: str) -> Member | None:
        row = self._db.one(
            "SELECT * FROM members WHERE room = ? AND agent = ? AND alias = ?", (room, agent, alias)
        )
        return None if row is None else _member(row)

    def list_for_room(self, room: str) -> list[Member]:
        rows = self._db.all("SELECT * FROM members WHERE room = ? ORDER BY joined_at, agent, alias", (room,))
        return [_member(row) for row in rows]

    def remove(self, room: str, agent: str, alias: str) -> bool:
        cursor = self._db.run(
            "DELETE FROM members WHERE room = ? AND agent = ? AND alias = ?", (room, agent, alias)
        )
        return cursor.rowcount > 0

    def set_role(self, room: str, agent: str, alias: str, role: Role) -> None:
        self._db.run(
            "UPDATE members SET role = ? WHERE room = ? AND agent = ? AND alias = ?",
            (role.value, room, agent, alias),
        )

    def set_listening(
        self, room: str, agent: str, alias: str, until: datetime | None, session: str | None
    ) -> None:
        self._db.run(
            "UPDATE members SET listening_until = ?, listener_session = ? "
            "WHERE room = ? AND agent = ? AND alias = ?",
            (None if until is None else _to_text(until), session, room, agent, alias),
        )

    def clear_listening_in_room(self, room: str) -> None:
        self._db.run(
            "UPDATE members SET listening_until = NULL, listener_session = NULL WHERE room = ?", (room,)
        )

    def advance_cursor(self, room: str, agent: str, alias: str, last_read_id: int) -> None:
        self._db.run(
            "UPDATE members SET last_read_id = max(last_read_id, ?) "
            "WHERE room = ? AND agent = ? AND alias = ?",
            (last_read_id, room, agent, alias),
        )

    def listening(
        self, agent: str, now: datetime, *, alias: str | None = None, session: str | None = None
    ) -> list[Member]:
        sql = (
            "SELECT m.* FROM members m JOIN rooms r ON r.slug = m.room "
            "WHERE m.agent = ? AND m.listening_until > ? AND r.status = 'active'"
        )
        params: list[object] = [agent, _to_text(now)]
        if alias is not None:
            sql += " AND m.alias = ?"
            params.append(alias)
        if session is not None:
            sql += " AND (m.listener_session IS NULL OR m.listener_session = ?)"
            params.append(session)
        sql += " ORDER BY m.listening_until, m.room, m.alias"
        return [_member(row) for row in self._db.all(sql, tuple(params))]


class SqliteMessageRepository:
    def __init__(self, db: _Database) -> None:
        self._db = db

    def add(
        self,
        *,
        room: str,
        sender: str,
        alias: str,
        kind: MessageKind,
        text: str,
        reply_to: int | None,
        created_at: datetime,
    ) -> Message:
        cursor = self._db.run(
            "INSERT INTO messages(room, sender, alias, kind, text, reply_to, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (room, sender, alias, kind.value, text, reply_to, _to_text(created_at)),
        )
        assert cursor.lastrowid is not None  # noqa: S101 - set after a successful INSERT
        stored = self.get(room, cursor.lastrowid)
        assert stored is not None  # noqa: S101 - just inserted
        return stored

    def get(self, room: str, message_id: int) -> Message | None:
        row = self._db.one("SELECT * FROM messages WHERE room = ? AND id = ?", (room, message_id))
        return None if row is None else _message(row)

    def recent(self, room: str, limit: int) -> list[Message]:
        rows = self._db.all(
            "SELECT * FROM (SELECT * FROM messages WHERE room = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (room, limit),
        )
        return [_message(row) for row in rows]

    def after(self, room: str, after_id: int, limit: int) -> list[Message]:
        rows = self._db.all(
            "SELECT * FROM messages WHERE room = ? AND id > ? ORDER BY id LIMIT ?",
            (room, after_id, limit),
        )
        return [_message(row) for row in rows]

    def unread_for(self, member: Member, limit: int) -> list[Message]:
        rows = self._db.all(
            "SELECT * FROM messages WHERE room = ? AND id > ? AND NOT (sender = ? AND alias = ?) "
            "ORDER BY id LIMIT ?",
            (member.room, member.last_read_id, member.agent, member.alias, limit),
        )
        return [_message(row) for row in rows]

    def latest_id(self, room: str) -> int:
        return self._db.scalar("SELECT COALESCE(MAX(id), 0) FROM messages WHERE room = ?", (room,))

    def count(self, room: str) -> int:
        return self._db.scalar("SELECT COUNT(*) FROM messages WHERE room = ?", (room,))

    def count_agent_posts_since(self, room: str, after_id: int) -> int:
        return self._db.scalar(
            "SELECT COUNT(*) FROM messages WHERE room = ? AND id > ? AND sender NOT IN (?, ?)",
            (room, after_id, HUMAN, SYSTEM),
        )

    def count_kind_since(self, room: str, kind: MessageKind, after_id: int) -> int:
        return self._db.scalar(
            "SELECT COUNT(*) FROM messages WHERE room = ? AND id > ? AND kind = ?",
            (room, after_id, kind.value),
        )


class SqliteUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()
        self._depth = 0
        db = _Database(connection, self._lock)
        self.rooms = SqliteRoomRepository(db)
        self.members = SqliteMemberRepository(db)
        self.messages = SqliteMessageRepository(db)

    @classmethod
    def open(cls, path: Path) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous=NORMAL")
        migrate(connection)
        return cls(connection)

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            if self._depth > 0:
                yield
                return
            self._connection.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")
            finally:
                self._depth = 0
