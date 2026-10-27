import asyncio
import time
from collections.abc import Awaitable, Callable


async def poll_until[T](
    check: Callable[[], T | None],
    *,
    timeout_s: float,
    interval_s: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    should_stop: Callable[[], Awaitable[bool]] | None = None,
) -> T | None:
    # ponytail: 0.5s DB poll, switch to asyncio.Event per room if latency matters
    # should_stop is asked right before every further check() as well (not only after an empty one):
    # a message that arrives while the sleep ran must not be consumed on behalf of a gone client.
    deadline = monotonic() + timeout_s
    while True:
        result = check()
        if result is not None:
            return result
        if should_stop is not None and await should_stop():
            return None
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        await sleep(min(interval_s, remaining))
        if should_stop is not None and await should_stop():
            return None
