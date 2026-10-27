import asyncio
import json
from pathlib import Path

import pytest

from agent_rooms.__main__ import main
from agent_rooms.bootstrap import create_app
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from agent_rooms.settings import Settings

HUMAN_TOKEN = "human-test-token-0123456789"


def write_tokens(tmp_path: Path) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"human": HUMAN_TOKEN, "claude": "claude-test-token-0123456789"}))
    return path


async def run_lifespan(app: object) -> None:
    inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    outbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))  # type: ignore[operator]
    await inbox.put({"type": "lifespan.startup"})
    assert (await asyncio.wait_for(outbox.get(), 5))["type"] == "lifespan.startup.complete"
    await inbox.put({"type": "lifespan.shutdown"})
    assert (await asyncio.wait_for(outbox.get(), 5))["type"] == "lifespan.shutdown.complete"
    await asyncio.wait_for(task, 5)


@pytest.mark.anyio
async def test_shutdown_closes_the_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    real_close = SqliteUnitOfWork.close

    def recording_close(self: SqliteUnitOfWork) -> None:
        closed.append(True)
        real_close(self)

    monkeypatch.setattr(SqliteUnitOfWork, "close", recording_close)
    app = create_app(Settings(db_path=tmp_path / "rooms.db", tokens_file=write_tokens(tmp_path)))
    assert closed == []
    await run_lifespan(app)
    assert closed == [True]


def test_create_app_fails_fast_on_a_missing_tokens_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Token file not found"):
        create_app(Settings(db_path=tmp_path / "rooms.db", tokens_file=tmp_path / "missing.json"))
    assert not (tmp_path / "rooms.db").exists()


def test_main_exits_with_a_readable_message_on_bad_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROOMS_TOKENS_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setenv("ROOMS_DB_PATH", str(tmp_path / "rooms.db"))
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert str(exit_info.value).startswith("agent-rooms: Token file not found")


def test_main_exits_on_a_non_numeric_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMS_PORT", "eighty")
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert "ROOMS_PORT" in str(exit_info.value)
