import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from lifespan import run_lifespan

from agent_rooms import bootstrap
from agent_rooms.__main__ import main
from agent_rooms.application.limits import Limits
from agent_rooms.bootstrap import CloseOnShutdown, create_app
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from agent_rooms.presentation.http.auth import audience_of
from agent_rooms.presentation.http.rest import build_routes
from agent_rooms.settings import Settings

HUMAN_TOKEN = "human-test-token-0123456789"


def write_tokens(tmp_path: Path) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"human": HUMAN_TOKEN, "claude": "claude-test-token-0123456789"}))
    return path


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


@pytest.mark.anyio
async def test_the_closer_runs_after_the_inner_lifespan_has_finished() -> None:
    events: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        events.append("inner shutdown")
        await send({"type": "lifespan.shutdown.complete"})

    app = CloseOnShutdown(inner, lambda: events.append("closed"))
    inbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    outbox: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    task = asyncio.create_task(app({"type": "lifespan"}, inbox.get, outbox.put))  # type: ignore[arg-type]
    await inbox.put({"type": "lifespan.startup"})
    await outbox.get()
    assert events == []
    await inbox.put({"type": "lifespan.shutdown"})
    await asyncio.wait_for(task, 5)

    assert events == ["inner shutdown", "closed"]


@pytest.mark.anyio
async def test_the_closer_also_runs_when_the_inner_lifespan_fails() -> None:
    closed: list[bool] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("startup failed")

    with pytest.raises(RuntimeError):
        await CloseOnShutdown(inner, lambda: closed.append(True))({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert closed == [True]


@pytest.mark.anyio
async def test_the_closer_leaves_other_scopes_alone() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    closed: list[bool] = []
    await CloseOnShutdown(inner, lambda: closed.append(True))({"type": "http"}, None, None)  # type: ignore[arg-type]
    assert (seen, closed) == (["http"], [])


def test_create_app_closes_the_database_when_wiring_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []
    real_close = SqliteUnitOfWork.close

    def recording_close(self: SqliteUnitOfWork) -> None:
        closed.append(True)
        real_close(self)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("tool registration failed")

    monkeypatch.setattr(SqliteUnitOfWork, "close", recording_close)
    monkeypatch.setattr(bootstrap, "register_tools", boom)

    with pytest.raises(RuntimeError, match="tool registration failed"):
        create_app(Settings(db_path=tmp_path / "rooms.db", tokens_file=write_tokens(tmp_path)))

    assert closed == [True]


def test_every_declared_route_has_an_entry_in_the_access_policy() -> None:
    nothing = cast(Any, None)
    specs = build_routes(rooms=nothing, messaging=nothing, listening=nothing, limits=Limits())
    assert specs
    for spec in specs:
        assert audience_of(spec.path.replace("{room}", "r1")) is not None, spec.path


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
