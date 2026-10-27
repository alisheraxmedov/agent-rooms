import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 27, 10, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class FakeTime:
    """Fake monotonic + sleep; sleeping also advances the wall clock so listen expiry stays consistent."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.elapsed = 0.0
        self.on_sleep: Callable[[], None] | None = None

    def monotonic(self) -> float:
        return self.elapsed

    async def sleep(self, seconds: float) -> None:
        self.elapsed += seconds
        self.clock.advance(seconds)
        if self.on_sleep is not None:
            callback, self.on_sleep = self.on_sleep, None
            callback()


class _SlowUnreadMessages:
    """Delegates to the real repository but lingers after `unread_for` so a racing reader can interleave."""

    def __init__(self, inner: Any, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s

    def unread_for(self, member: Any, limit: int) -> Any:
        found = self._inner.unread_for(member, limit)
        time.sleep(self._delay_s)
        return found

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SlowUnreadUnitOfWork:
    def __init__(self, inner: Any, delay_s: float = 0.002) -> None:
        self.rooms = inner.rooms
        self.members = inner.members
        self.messages = _SlowUnreadMessages(inner.messages, delay_s)
        self.transaction = inner.transaction
