from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    max_text_length: int = 8000
    default_max_messages: int = 60
    review_round_cap: int = 5
    join_history: int = 20
    read_default: int = 50
    read_max: int = 200
    history_max: int = 500
    wait_default_s: int = 30
    wait_max_s: int = 40
    listen_default_minutes: int = 30
    listen_max_minutes: int = 60
    listen_next_default_s: int = 540
    listen_next_max_s: int = 590
    poll_interval_s: float = 0.5


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))
