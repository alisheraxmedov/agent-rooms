import logging
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from agent_rooms.application.limits import Limits


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, "").strip()
    return value or default


def _number[T: (int, float)](env: Mapping[str, str], name: str, default: T, parse: Callable[[str], T]) -> T:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return parse(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}.") from None


def _integer(
    env: Mapping[str, str], name: str, default: int, *, minimum: int = 1, maximum: int | None = None
) -> int:
    value = _number(env, name, default, int)
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {bounds}, got {value}.")
    return value


def _seconds(env: Mapping[str, str], name: str, default: float) -> float:
    value = _number(env, name, default, float)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, got {value}.")
    return value


def _log_level(env: Mapping[str, str], default: str) -> str:
    level = _text(env, "ROOMS_LOG_LEVEL", default)
    if level.upper() not in logging.getLevelNamesMapping():
        raise ValueError(f"ROOMS_LOG_LEVEL must be a logging level name such as INFO, got {level!r}.")
    return level


def _hosts(env: Mapping[str, str], default: tuple[str, ...]) -> tuple[str, ...]:
    hosts = tuple(part.strip() for part in env.get("ROOMS_ALLOWED_HOSTS", "").split(",") if part.strip())
    return hosts or default


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("data/rooms.db")
    tokens_file: Path = Path("config/agents.json")
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*")
    log_level: str = "INFO"
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        env = os.environ if env is None else env
        base, limits = cls(), Limits()
        return cls(
            host=_text(env, "ROOMS_HOST", base.host),
            port=_integer(env, "ROOMS_PORT", base.port, maximum=65535),
            db_path=Path(_text(env, "ROOMS_DB_PATH", str(base.db_path))),
            tokens_file=Path(_text(env, "ROOMS_TOKENS_FILE", str(base.tokens_file))),
            allowed_hosts=_hosts(env, base.allowed_hosts),
            log_level=_log_level(env, base.log_level),
            limits=Limits(
                max_text_length=_integer(env, "ROOMS_MAX_TEXT_LENGTH", limits.max_text_length),
                default_max_messages=_integer(env, "ROOMS_DEFAULT_MAX_MESSAGES", limits.default_max_messages),
                review_round_cap=_integer(env, "ROOMS_REVIEW_ROUND_CAP", limits.review_round_cap),
                wait_max_s=_integer(env, "ROOMS_WAIT_MAX_SECONDS", limits.wait_max_s),
                poll_interval_s=_seconds(env, "ROOMS_POLL_INTERVAL_SECONDS", limits.poll_interval_s),
                listen_max_minutes=_integer(env, "ROOMS_LISTEN_MAX_MINUTES", limits.listen_max_minutes),
                listen_next_max_s=_integer(env, "ROOMS_LISTEN_NEXT_MAX_SECONDS", limits.listen_next_max_s),
            ),
        )
