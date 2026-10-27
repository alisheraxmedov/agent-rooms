from pathlib import Path

import pytest

from agent_rooms.application.limits import Limits
from agent_rooms.settings import Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.db_path == Path("data/rooms.db")
    assert settings.tokens_file == Path("config/agents.json")
    assert settings.allowed_hosts == ("127.0.0.1:*", "localhost:*")
    assert settings.log_level == "INFO"
    assert settings.limits == Limits()


def test_from_empty_env_equals_defaults() -> None:
    assert Settings.from_env({}) == Settings()


def test_from_env_parses_every_variable() -> None:
    env = {
        "ROOMS_HOST": "0.0.0.0",  # noqa: S104
        "ROOMS_PORT": "9001",
        "ROOMS_DB_PATH": "/data/rooms.db",
        "ROOMS_TOKENS_FILE": "/app/config/agents.json",
        "ROOMS_ALLOWED_HOSTS": "rooms.local:*, 127.0.0.1:* ,,",
        "ROOMS_LOG_LEVEL": "DEBUG",
        "ROOMS_MAX_TEXT_LENGTH": "1000",
        "ROOMS_DEFAULT_MAX_MESSAGES": "10",
        "ROOMS_REVIEW_ROUND_CAP": "2",
        "ROOMS_WAIT_MAX_SECONDS": "5",
        "ROOMS_POLL_INTERVAL_SECONDS": "0.25",
        "ROOMS_LISTEN_MAX_MINUTES": "15",
        "ROOMS_LISTEN_NEXT_MAX_SECONDS": "100",
    }
    settings = Settings.from_env(env)
    assert settings.host == "0.0.0.0"  # noqa: S104
    assert settings.port == 9001
    assert settings.db_path == Path("/data/rooms.db")
    assert settings.tokens_file == Path("/app/config/agents.json")
    assert settings.allowed_hosts == ("rooms.local:*", "127.0.0.1:*")
    assert settings.log_level == "DEBUG"
    assert settings.limits == Limits(
        max_text_length=1000,
        default_max_messages=10,
        review_round_cap=2,
        wait_max_s=5,
        poll_interval_s=0.25,
        listen_max_minutes=15,
        listen_next_max_s=100,
    )


def test_blank_values_fall_back_to_defaults() -> None:
    assert Settings.from_env({"ROOMS_PORT": "  ", "ROOMS_HOST": ""}) == Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ROOMS_PORT", "eighty"),
        ("ROOMS_MAX_TEXT_LENGTH", "1.5"),
        ("ROOMS_POLL_INTERVAL_SECONDS", "fast"),
    ],
)
def test_invalid_numbers_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name):
        Settings.from_env({name: value})


def test_from_env_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMS_PORT", "4321")
    assert Settings.from_env().port == 4321
