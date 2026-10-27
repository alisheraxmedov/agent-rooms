import json
import logging
from pathlib import Path

import pytest

from agent_rooms.infrastructure.token_registry import JsonFileTokenRegistry

CLAUDE = "claude-token-abcdefghijklmnopqrstuvwxyz"
HUMAN = "human-token-abcdefghijklmnopqrstuvwxyz0"


def write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "agents.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def test_loads_agent_to_token_mapping(tmp_path: Path) -> None:
    registry = JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "human": HUMAN}))
    assert registry.agent_for(CLAUDE) == "claude"
    assert registry.agent_for(HUMAN) == "human"


def test_unknown_or_empty_token_returns_none(tmp_path: Path) -> None:
    registry = JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "human": HUMAN}))
    assert registry.agent_for("x" * 30) is None
    assert registry.agent_for("") is None
    assert registry.agent_for(CLAUDE[:-1]) is None


@pytest.mark.parametrize("name", ["Claude", "1claude", "", "has space", "a" * 33, "system"])
def test_rejects_invalid_agent_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="agent name"):
        JsonFileTokenRegistry.from_file(write(tmp_path, {name: CLAUDE}))


def test_rejects_short_tokens_without_leaking_them(tmp_path: Path) -> None:
    short = "short-secret-token"
    with pytest.raises(ValueError, match="at least 24") as excinfo:
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": short}))
    assert short not in str(excinfo.value)


def test_rejects_duplicate_tokens_without_leaking_them(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share the same token") as excinfo:
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "codex": CLAUDE}))
    assert CLAUDE not in str(excinfo.value)


def test_rejects_non_string_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": 12345}))


@pytest.mark.parametrize("content", ["[]", '"text"', "42", "null"])
def test_rejects_non_object_json(tmp_path: Path, content: str) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        JsonFileTokenRegistry.from_file(write(tmp_path, content))


def test_rejects_malformed_json(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        JsonFileTokenRegistry.from_file(write(tmp_path, "{nope"))


def test_missing_file_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        JsonFileTokenRegistry.from_file(tmp_path / "absent.json")


def test_warns_when_human_is_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE}))
    assert "human" in caplog.text
    assert CLAUDE not in caplog.text


def test_no_warning_when_human_present(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "human": HUMAN}))
    assert caplog.text == ""
