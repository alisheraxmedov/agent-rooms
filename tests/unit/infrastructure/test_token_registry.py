import hashlib
import hmac
import json
import logging
from pathlib import Path

import pytest

from agent_rooms.infrastructure.token_registry import JsonFileTokenRegistry

CLAUDE = "claude-token-abcdefghijklmnopqrstuvwxyz"
HUMAN = "human-token-abcdefghijklmnopqrstuvwxyz0"


def hashed(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def test_no_warning_when_human_present_and_every_entry_is_hashed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": hashed(CLAUDE), "human": hashed(HUMAN)}))
    assert caplog.text == ""


def test_hashed_entries_authenticate_the_plain_token(tmp_path: Path) -> None:
    registry = JsonFileTokenRegistry.from_file(
        write(tmp_path, {"claude": hashed(CLAUDE), "human": hashed(HUMAN)})
    )
    assert registry.agent_for(CLAUDE) == "claude"
    assert registry.agent_for(HUMAN) == "human"
    assert registry.agent_for(hashed(CLAUDE)) is None
    assert registry.agent_for(hashlib.sha256(CLAUDE.encode()).hexdigest()) is None
    assert registry.agent_for("x" * 30) is None


def test_hashed_and_legacy_entries_can_be_mixed(tmp_path: Path) -> None:
    registry = JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "human": hashed(HUMAN)}))
    assert registry.agent_for(CLAUDE) == "claude"
    assert registry.agent_for(HUMAN) == "human"


def test_legacy_plain_entries_warn_naming_the_agent_never_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "human": hashed(HUMAN)}))
    assert "claude" in caplog.text
    assert CLAUDE not in caplog.text
    assert "human" not in caplog.text.replace("sha256", "")


@pytest.mark.parametrize(
    "value",
    [
        "sha256:",
        "sha256:abc",
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "sha256:" + "a" * 65,
        "SHA256:" + "a" * 64,
    ],
)
def test_rejects_malformed_hash_entries(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="sha256") as excinfo:
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": value}))
    payload = value.split(":", 1)[1]
    assert not payload or payload not in str(excinfo.value)


def test_rejects_the_same_token_in_hashed_and_plain_form_without_leaking(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share the same token") as excinfo:
        JsonFileTokenRegistry.from_file(write(tmp_path, {"claude": CLAUDE, "codex": hashed(CLAUDE)}))
    assert CLAUDE not in str(excinfo.value)


def test_lookup_compares_against_every_entry_in_constant_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = JsonFileTokenRegistry.from_file(
        write(tmp_path, {"claude": hashed(CLAUDE), "human": hashed(HUMAN), "codex": hashed("c" * 30)})
    )
    seen: list[bytes] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        seen.append(right)
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert registry.agent_for(CLAUDE) == "claude"
    assert len(seen) == 3
