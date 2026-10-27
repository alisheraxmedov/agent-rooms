import hashlib
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
_HASH_PREFIX = "sha256:"
_HASHED = re.compile(r"sha256:[0-9a-f]{64}")


def _validate_name(name: str) -> None:
    if name == SYSTEM:
        raise ValueError(f"Token file: agent name '{SYSTEM}' is reserved.")
    if not _AGENT_NAME.fullmatch(name):
        raise ValueError(
            f"Token file: invalid agent name {name!r}; use 1-32 chars of lowercase letters, digits, "
            "'_' and '-', starting with a letter."
        )


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _entry_digest(name: str, value: object) -> tuple[bytes, bool]:
    """Returns the sha256 digest of the agent's token and whether the entry was a legacy plain token."""
    if not isinstance(value, str):
        raise ValueError(f"Token file: the token for '{name}' must be a string.")
    if value.lower().startswith(_HASH_PREFIX):
        if not _HASHED.fullmatch(value):
            raise ValueError(
                f"Token file: the entry for '{name}' must be '{_HASH_PREFIX}' followed by 64 lowercase hex "
                "characters (the sha256 of the token)."
            )
        return bytes.fromhex(value[len(_HASH_PREFIX) :]), False
    if len(value) < _MIN_TOKEN_LENGTH:
        raise ValueError(
            f"Token file: the token for '{name}' must be at least {_MIN_TOKEN_LENGTH} characters."
        )
    return _digest(value), True


class JsonFileTokenRegistry:
    def __init__(self, digests: dict[str, bytes]) -> None:
        self._entries = list(digests.items())

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
        digests = cls._validated(data)
        if HUMAN not in digests:
            logger.warning(
                "Token file %s has no '%s' entry; the web UI and REST admin will be unusable.", path, HUMAN
            )
        return cls(digests)

    @staticmethod
    def _validated(data: dict[object, object]) -> dict[str, bytes]:
        digests: dict[str, bytes] = {}
        owners: dict[bytes, str] = {}
        for name, value in data.items():
            assert isinstance(name, str)  # noqa: S101 - JSON object keys are always strings
            _validate_name(name)
            digest, legacy = _entry_digest(name, value)
            if digest in owners:
                raise ValueError(f"Token file: agents '{owners[digest]}' and '{name}' share the same token.")
            if legacy:
                logger.warning(
                    "Token file: the entry for '%s' is a plain token; store its %s<hex> hash instead "
                    "(run scripts/rooms_setup.py tokens).",
                    name,
                    _HASH_PREFIX,
                )
            owners[digest] = name
            digests[name] = digest
        return digests

    def agent_for(self, token: str) -> str | None:
        candidate = _digest(token)
        found: str | None = None
        for agent, known in self._entries:
            if hmac.compare_digest(candidate, known):
                found = agent
        return found
