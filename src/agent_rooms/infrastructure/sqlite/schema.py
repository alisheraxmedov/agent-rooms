import sqlite3

SCHEMA_VERSION = 2

_MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS members(
  room TEXT NOT NULL REFERENCES rooms(slug) ON DELETE CASCADE,
  agent TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('writer','reviewer','observer','lead')),
  last_read_id INTEGER NOT NULL DEFAULT 0,
  listening_until TEXT NULL,
  listener_session TEXT NULL,
  joined_at TEXT NOT NULL,
  PRIMARY KEY (room, agent, alias));
"""

_DDL = f"""
CREATE TABLE IF NOT EXISTS rooms(
  slug TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','paused','closed')),
  max_messages INTEGER NOT NULL DEFAULT 60,
  budget_start_id INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
{_MEMBERS_DDL.strip()}
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room TEXT NOT NULL REFERENCES rooms(slug) ON DELETE CASCADE,
  sender TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN
    ('chat','task','review_request','changes_requested','approved','done','system')),
  text TEXT NOT NULL,
  reply_to INTEGER NULL REFERENCES messages(id),
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_room_id ON messages(room, id);
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
"""


def _rebuild_members_for_lead(connection: sqlite3.Connection) -> None:
    """v1 -> v2: SQLite cannot alter a CHECK constraint, so the members table is rebuilt."""
    indexes = [
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'members' AND sql IS NOT NULL"
        )
    ]
    connection.execute(_MEMBERS_DDL.replace("IF NOT EXISTS members(", "members_new("))
    connection.execute(
        "INSERT INTO members_new(room, agent, alias, role, last_read_id, listening_until, "
        "listener_session, joined_at) SELECT room, agent, alias, role, last_read_id, listening_until, "
        "listener_session, joined_at FROM members"
    )
    connection.execute("DROP TABLE members")
    connection.execute("ALTER TABLE members_new RENAME TO members")
    for statement in indexes:
        connection.execute(statement)


def migrate(connection: sqlite3.Connection) -> None:
    """Brings the database to SCHEMA_VERSION in one transaction; safe to run on every start."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in _DDL.split(";"):
            if statement.strip():
                connection.execute(statement)
        row = connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        elif row[0] < 2:
            _rebuild_members_for_lead(connection)
            connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
