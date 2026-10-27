import dataclasses

import pytest

from agent_rooms.application.limits import Limits, clamp


def test_defaults_match_spec() -> None:
    limits = Limits()
    assert dataclasses.asdict(limits) == {
        "max_text_length": 8000,
        "default_max_messages": 60,
        "review_round_cap": 5,
        "join_history": 20,
        "read_default": 50,
        "read_max": 200,
        "history_max": 500,
        "wait_default_s": 30,
        "wait_max_s": 40,
        "listen_default_minutes": 30,
        "listen_max_minutes": 60,
        "listen_next_default_s": 540,
        "listen_next_max_s": 590,
        "poll_interval_s": 0.5,
    }


def test_limits_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        Limits().wait_max_s = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [(999, 0, 40, 40), (-5, 0, 40, 0), (7, 0, 40, 7), (0, 0, 40, 0), (40, 0, 40, 40)],
)
def test_clamp(value: int, low: int, high: int, expected: int) -> None:
    assert clamp(value, low, high) == expected
