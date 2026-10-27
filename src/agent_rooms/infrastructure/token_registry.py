import hmac
import json
import logging
import re
from pathlib import Path
from typing import Self

from agent_rooms.domain.models import HUMAN, SYSTEM

logger = logging.getLogger(__name__)

_AGENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_MIN_TOKEN_LENGTH = 24


def _validate_name(name: str) -> None:
    if name == SYSTEM:
        raise ValueError(f"Token file: agent name '{SYSTEM}' is reserved.")
    if not _AGENT_NAME.fullmatch(name):
        raise ValueError(
            f"Token file: invalid agent name {name!r}; use 1-32 chars of lowercase letters, digits, "
            "'_' and '-', starting with a letter."
        )


def _validate_token(name: str, token: object) -> str:
    if not isinstance(token, str):
        raise ValueError(f"Token file: the token for '{name}' must be a string.")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise ValueError(
            f"Token file: the token for '{name}' must be at least {_MIN_TOKEN_LENGTH} characters."
        )
    return token


class JsonFileTokenRegistry:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._entries = [(agent, token.encode("utf-8")) for agent, token in tokens.items()]

    @classmethod
    def from_file(cls, path: Path) -> Self:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ValueError(f"Token file not found: {path}") from None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError(f"Token file {path} is not valid JSON.") from None
        if not isinstance(data, dict):
            raise ValueError(f"Token file {path} must contain a JSON object {{agent: token}}.")
        tokens = cls._validated(data)
        if HUMAN not in tokens:
            logger.warning(
                "Token file %s has no '%s' entry; the web UI and REST admin will be unusable.", path, HUMAN
            )
        return cls(tokens)

    @staticmethod
    def _validated(data: dict[object, object]) -> dict[str, str]:
        tokens: dict[str, str] = {}
        owners: dict[str, str] = {}
        for name, value in data.items():
            assert isinstance(name, str)  # noqa: S101 - JSON object keys are always strings
            _validate_name(name)
            token = _validate_token(name, value)
            if token in owners:
                raise ValueError(f"Token file: agents '{owners[token]}' and '{name}' share the same token.")
            owners[token] = name
            tokens[name] = token
        return tokens

    def agent_for(self, token: str) -> str | None:
        candidate = token.encode("utf-8")
        found: str | None = None
        for agent, known in self._entries:
            if hmac.compare_digest(candidate, known):
                found = agent
        return found
