import pytest

from agent_rooms.application.polling import poll_until


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.anyio
async def test_returns_immediately_when_first_check_succeeds() -> None:
    fake = FakeTime()
    result = await poll_until(
        lambda: "ready", timeout_s=10, interval_s=0.5, monotonic=fake.monotonic, sleep=fake.sleep
    )
    assert result == "ready"
    assert fake.sleeps == []


@pytest.mark.anyio
async def test_returns_value_on_nth_check() -> None:
    fake = FakeTime()
    calls = 0

    def check() -> int | None:
        nonlocal calls
        calls += 1
        return calls if calls == 4 else None

    result = await poll_until(check, timeout_s=10, interval_s=0.5, monotonic=fake.monotonic, sleep=fake.sleep)
    assert result == 4
    assert fake.sleeps == [0.5, 0.5, 0.5]


@pytest.mark.anyio
async def test_returns_none_after_timeout() -> None:
    fake = FakeTime()
    calls = 0

    def check() -> None:
        nonlocal calls
        calls += 1

    result = await poll_until(check, timeout_s=2, interval_s=0.5, monotonic=fake.monotonic, sleep=fake.sleep)
    assert result is None
    assert fake.now == 2.0
    assert calls == 5


@pytest.mark.anyio
async def test_zero_timeout_checks_exactly_once() -> None:
    fake = FakeTime()
    calls = 0

    def check() -> None:
        nonlocal calls
        calls += 1

    result = await poll_until(check, timeout_s=0, interval_s=0.5, monotonic=fake.monotonic, sleep=fake.sleep)
    assert result is None
    assert calls == 1
    assert fake.sleeps == []


@pytest.mark.anyio
async def test_last_sleep_never_overshoots_the_deadline() -> None:
    fake = FakeTime()
    await poll_until(lambda: None, timeout_s=1.2, interval_s=0.5, monotonic=fake.monotonic, sleep=fake.sleep)
    assert fake.now == pytest.approx(1.2)
    assert fake.sleeps == pytest.approx([0.5, 0.5, 0.2])
