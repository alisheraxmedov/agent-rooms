import sqlite3

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS rooms(
  slug TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','paused','closed')),
  max_messages INTEGER NOT NULL DEFAULT 60,
  budget_start_id INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(
  room TEXT NOT NULL REFERENCES rooms(slug) ON DELETE CASCADE,
  agent TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('writer','reviewer','observer')),
  last_read_id INTEGER NOT NULL DEFAULT 0,
  listening_until TEXT NULL,
  listener_session TEXT NULL,
  joined_at TEXT NOT NULL,
  PRIMARY KEY (room, agent, alias));
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


def migrate(connection: sqlite3.Connection) -> None:
    for statement in _DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
    if connection.execute("SELECT 1 FROM schema_version").fetchone() is None:
        connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
