from datetime import UTC, timedelta

from agent_rooms.infrastructure.clock import SystemClock


def test_now_is_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo is UTC
