from importlib.resources import files

import pytest


@pytest.fixture(scope="module")
def protocol() -> str:
    return (files("agent_rooms") / "resources" / "protocol.md").read_text(encoding="utf-8")


def test_protocol_stays_short(protocol: str) -> None:
    assert len(protocol.splitlines()) <= 90


@pytest.mark.parametrize(
    "required",
    [
        "BEGIN ROOM DATA",
        "END ROOM DATA",
        "untrusted",
        "token file",
        "agent config",
        "curl",
        "own `alias`",
        "at most once",
        "`read`",
        "`wait`",
        "`listen`",
        "approved",
        "done",
    ],
)
def test_protocol_covers_the_safety_and_delivery_rules(protocol: str, required: str) -> None:
    assert required in protocol
