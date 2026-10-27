import keyring.errors
import pytest

from agent_rooms.tui.credentials import ACCOUNT, ENV_VAR, SERVICE, CredentialsError, TokenStore
from keyring_fake import MemoryKeyring


class BrokenKeyring(MemoryKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        raise keyring.errors.KeyringError("backend unavailable")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.KeyringError("backend unavailable")


def test_no_token_anywhere() -> None:
    store = TokenStore(environ={}, backend=MemoryKeyring())
    assert store.load() is None
    assert store.source() is None


def test_the_environment_wins_over_the_keyring() -> None:
    backend = MemoryKeyring()
    backend.set_password(SERVICE, ACCOUNT, "from-keyring")
    store = TokenStore(environ={ENV_VAR: "  from-env \n"}, backend=backend)
    assert store.load() == "from-env"
    assert store.source() == "env"


def test_a_blank_environment_variable_is_ignored() -> None:
    backend = MemoryKeyring()
    backend.set_password(SERVICE, ACCOUNT, "from-keyring")
    store = TokenStore(environ={ENV_VAR: "   "}, backend=backend)
    assert store.load() == "from-keyring"
    assert store.source() == "keyring"


def test_save_writes_to_the_agent_rooms_human_entry() -> None:
    backend = MemoryKeyring()
    TokenStore(environ={}, backend=backend).save("secret-token")
    assert backend.items == {("agent-rooms", "human"): "secret-token"}


def test_delete_removes_the_saved_token_and_tolerates_a_missing_one() -> None:
    backend = MemoryKeyring()
    store = TokenStore(environ={}, backend=backend)
    store.save("secret-token")
    store.delete()
    assert store.load() is None
    store.delete()


def test_delete_never_touches_the_environment() -> None:
    environ = {ENV_VAR: "from-env"}
    store = TokenStore(environ=environ, backend=MemoryKeyring())
    store.delete()
    assert environ == {ENV_VAR: "from-env"}
    assert store.load() == "from-env"


def test_an_unusable_keyring_reads_as_no_token_and_fails_loudly_on_save() -> None:
    store = TokenStore(environ={}, backend=BrokenKeyring())
    assert store.load() is None
    with pytest.raises(CredentialsError, match="keyring"):
        store.save("secret-token")


def test_credential_errors_do_not_leak_the_token() -> None:
    store = TokenStore(environ={}, backend=BrokenKeyring())
    with pytest.raises(CredentialsError) as caught:
        store.save("secret-token")
    assert "secret-token" not in str(caught.value)


def test_delete_failure_on_a_broken_keyring_is_reported() -> None:
    with pytest.raises(CredentialsError):
        TokenStore(environ={}, backend=BrokenKeyring()).delete()


def test_the_default_backend_is_whatever_keyring_has_configured() -> None:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    TokenStore(environ={}).save("secret-token")
    assert backend.items[(SERVICE, ACCOUNT)] == "secret-token"
