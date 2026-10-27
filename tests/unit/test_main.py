import json
from pathlib import Path
from typing import Any

import pytest

from agent_rooms import __main__ as entrypoint


def test_main_runs_uvicorn_with_a_short_graceful_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = tmp_path / "agents.json"
    tokens.write_text(json.dumps({"human": "human-test-token-0123456789"}))
    monkeypatch.setenv("ROOMS_TOKENS_FILE", str(tokens))
    monkeypatch.setenv("ROOMS_DB_PATH", str(tmp_path / "rooms.db"))
    monkeypatch.setenv("ROOMS_HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("ROOMS_PORT", "9001")
    monkeypatch.setenv("ROOMS_LOG_LEVEL", "WARNING")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(entrypoint.uvicorn, "run", lambda _app, **kwargs: calls.append(kwargs))

    entrypoint.main()

    assert calls == [
        {
            "host": "0.0.0.0",  # noqa: S104
            "port": 9001,
            "workers": 1,
            "log_level": "warning",
            "timeout_graceful_shutdown": 5,
        }
    ]
