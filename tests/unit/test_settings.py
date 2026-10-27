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


@pytest.mark.parametrize(
    "name",
    [
        "ROOMS_MAX_TEXT_LENGTH",
        "ROOMS_DEFAULT_MAX_MESSAGES",
        "ROOMS_REVIEW_ROUND_CAP",
        "ROOMS_WAIT_MAX_SECONDS",
        "ROOMS_LISTEN_MAX_MINUTES",
        "ROOMS_LISTEN_NEXT_MAX_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_integer_limits_must_be_positive(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=rf"{name} must be at least 1"):
        Settings.from_env({name: value})


@pytest.mark.parametrize("value", ["0", "-0.5", "nan", "inf", "-inf"])
def test_poll_interval_must_be_a_positive_finite_number(value: str) -> None:
    with pytest.raises(ValueError, match="ROOMS_POLL_INTERVAL_SECONDS must be a positive"):
        Settings.from_env({"ROOMS_POLL_INTERVAL_SECONDS": value})


@pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
def test_port_must_be_a_valid_tcp_port(value: str) -> None:
    with pytest.raises(ValueError, match="ROOMS_PORT must be between 1 and 65535"):
        Settings.from_env({"ROOMS_PORT": value})


def test_boundary_values_are_accepted() -> None:
    settings = Settings.from_env({"ROOMS_PORT": "65535", "ROOMS_REVIEW_ROUND_CAP": "1"})
    assert (settings.port, settings.limits.review_round_cap) == (65535, 1)


def test_unknown_log_level_names_the_variable() -> None:
    with pytest.raises(ValueError, match="ROOMS_LOG_LEVEL"):
        Settings.from_env({"ROOMS_LOG_LEVEL": "LOUD"})
    assert Settings.from_env({"ROOMS_LOG_LEVEL": "debug"}).log_level == "debug"
