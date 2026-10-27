import os
from collections.abc import Mapping

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

ENV_VAR = "ROOMS_HUMAN_TOKEN"
SERVICE = "agent-rooms"
ACCOUNT = "human"


class CredentialsError(Exception):
    pass


class TokenStore:
    """The human token: environment first, then the OS keyring. Never written anywhere else."""

    def __init__(
        self, environ: Mapping[str, str] | None = None, backend: KeyringBackend | None = None
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._backend = backend

    def _keyring(self) -> KeyringBackend:
        return self._backend or keyring.get_keyring()

    def _from_env(self) -> str | None:
        return self._environ.get(ENV_VAR, "").strip() or None

    def _from_keyring(self) -> str | None:
        try:
            return (self._keyring().get_password(SERVICE, ACCOUNT) or "").strip() or None
        except keyring.errors.KeyringError:
            return None

    def load(self) -> str | None:
        return self._from_env() or self._from_keyring()

    def source(self) -> str | None:
        if self._from_env():
            return "env"
        return "keyring" if self._from_keyring() else None

    def save(self, token: str) -> None:
        try:
            self._keyring().set_password(SERVICE, ACCOUNT, token)
        except keyring.errors.KeyringError as error:
            raise CredentialsError(f"The OS keyring is not usable ({type(error).__name__}).") from None

    def delete(self) -> None:
        try:
            self._keyring().delete_password(SERVICE, ACCOUNT)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as error:
            raise CredentialsError(f"The OS keyring is not usable ({type(error).__name__}).") from None
