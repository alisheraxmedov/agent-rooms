from collections.abc import Callable
from datetime import UTC, datetime, timedelta


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
