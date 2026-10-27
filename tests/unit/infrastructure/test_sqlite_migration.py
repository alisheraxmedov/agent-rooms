import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from agent_rooms.domain.models import Role
from agent_rooms.infrastructure.sqlite.schema import SCHEMA_VERSION, migrate
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

V1_DDL = """
CREATE TABLE rooms(
  slug TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','paused','closed')),
  max_messages INTEGER NOT NULL DEFAULT 60,
  budget_start_id INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE members(
  room TEXT NOT NULL REFERENCES rooms(slug) ON DELETE CASCADE,
  agent TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('writer','reviewer','observer')),
  last_read_id INTEGER NOT NULL DEFAULT 0,
  listening_until TEXT NULL,
  listener_session TEXT NULL,
  joined_at TEXT NOT NULL,
  PRIMARY KEY (room, agent, alias));
CREATE TABLE messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room TEXT NOT NULL REFERENCES rooms(slug) ON DELETE CASCADE,
  sender TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN
    ('chat','task','review_request','changes_requested','approved','done','system')),
  text TEXT NOT NULL,
  reply_to INTEGER NULL REFERENCES messages(id),
  created_at TEXT NOT NULL);
CREATE INDEX idx_messages_room_id ON messages(room, id);
CREATE TABLE schema_version(version INTEGER NOT NULL);
INSERT INTO schema_version(version) VALUES (1);
"""

MEMBER_ROWS = [
    ("alpha", "claude", "", "writer", 3, None, None, "2026-10-27T10:00:00.000+00:00"),
    (
        "alpha",
        "codex",
        "strict",
        "reviewer",
        2,
        "2026-10-27T11:00:00.000+00:00",
        "ses_A",
        "2026-10-27T10:01:00.000+00:00",
    ),
    ("alpha", "opencode", "", "observer", 0, None, None, "2026-10-27T10:02:00.000+00:00"),
]


@pytest.fixture
def v1_db(tmp_path: Path) -> Path:
    path = tmp_path / "rooms.db"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.executescript(V1_DDL)
    connection.execute(
        "INSERT INTO rooms VALUES ('alpha', 'Parser', 'active', 60, 0, '2026-10-27T09:00:00.000+00:00')"
    )
    connection.executemany("INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?)", MEMBER_ROWS)
    connection.execute(
        "INSERT INTO messages(room, sender, alias, kind, text, reply_to, created_at) "
        "VALUES ('alpha', 'claude', '', 'chat', 'hello', NULL, '2026-10-27T10:03:00.000+00:00')"
    )
    connection.close()
    return path


def raw_rows(path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_the_v1_fixture_really_rejects_lead(v1_db: Path) -> None:
    connection = sqlite3.connect(v1_db)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO members VALUES ('alpha', 'x', '', 'lead', 0, NULL, NULL, 't')")
    connection.close()


def test_opening_a_v1_database_preserves_every_row_and_bumps_the_version(v1_db: Path) -> None:
    SqliteUnitOfWork.open(v1_db).close()

    assert raw_rows(v1_db, "SELECT * FROM members ORDER BY joined_at") == MEMBER_ROWS
    assert raw_rows(v1_db, "SELECT text FROM messages") == [("hello",)]
    assert raw_rows(v1_db, "SELECT slug FROM rooms") == [("alpha",)]
    assert raw_rows(v1_db, "SELECT version FROM schema_version") == [(SCHEMA_VERSION,)]
    assert SCHEMA_VERSION == 2


def test_a_migrated_database_accepts_the_lead_role(v1_db: Path) -> None:
    unit = SqliteUnitOfWork.open(v1_db)
    try:
        member = unit.members.get("alpha", "claude", "")
        assert member is not None
        unit.members.upsert(replace(member, role=Role.LEAD))
        stored = unit.members.get("alpha", "claude", "")
        assert stored is not None
        assert stored.role is Role.LEAD
    finally:
        unit.close()


def test_a_migrated_database_still_rejects_unknown_roles(v1_db: Path) -> None:
    SqliteUnitOfWork.open(v1_db).close()
    connection = sqlite3.connect(v1_db)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO members VALUES ('alpha', 'x', '', 'boss', 0, NULL, NULL, 't')")
    connection.close()


def test_the_rebuilt_members_table_keeps_its_cascade_and_primary_key(v1_db: Path) -> None:
    unit = SqliteUnitOfWork.open(v1_db)
    try:
        assert unit.rooms.delete("alpha") is True
        assert unit.members.list_for_room("alpha") == []
        assert unit.rooms.get("alpha") is None
    finally:
        unit.close()
    connection = sqlite3.connect(v1_db)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(members)")]
    connection.close()
    assert columns == [
        "room",
        "agent",
        "alias",
        "role",
        "last_read_id",
        "listening_until",
        "listener_session",
        "joined_at",
    ]


def test_indexes_on_members_are_recreated(v1_db: Path) -> None:
    connection = sqlite3.connect(v1_db)
    connection.execute("CREATE INDEX idx_members_agent ON members(agent, listening_until)")
    connection.close()

    SqliteUnitOfWork.open(v1_db).close()

    names = {row[0] for row in raw_rows(v1_db, "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_members_agent" in names


def test_opening_twice_is_a_no_op(v1_db: Path) -> None:
    SqliteUnitOfWork.open(v1_db).close()
    before = raw_rows(v1_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    members_before = raw_rows(v1_db, "SELECT * FROM members ORDER BY joined_at")

    SqliteUnitOfWork.open(v1_db).close()

    assert raw_rows(v1_db, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == before
    assert raw_rows(v1_db, "SELECT * FROM members ORDER BY joined_at") == members_before
    assert raw_rows(v1_db, "SELECT version FROM schema_version") == [(2,)]


def test_a_fresh_database_is_created_at_the_current_version_and_accepts_lead(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    unit = SqliteUnitOfWork.open(path)
    unit.close()
    assert raw_rows(path, "SELECT version FROM schema_version") == [(2,)]
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO rooms VALUES ('a', 't', 'active', 60, 0, 't')")
    connection.execute("INSERT INTO members VALUES ('a', 'x', '', 'lead', 0, NULL, NULL, 't')")
    connection.close()


def test_a_failing_migration_rolls_back_and_leaves_the_v1_database_intact(v1_db: Path) -> None:
    connection = sqlite3.connect(v1_db, isolation_level=None)
    connection.execute("CREATE TABLE members_new(blocker INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        migrate(connection)
    connection.execute("DROP TABLE members_new")
    connection.close()

    assert raw_rows(v1_db, "SELECT * FROM members ORDER BY joined_at") == MEMBER_ROWS
    assert raw_rows(v1_db, "SELECT version FROM schema_version") == [(1,)]
    SqliteUnitOfWork.open(v1_db).close()
    assert raw_rows(v1_db, "SELECT version FROM schema_version") == [(2,)]
