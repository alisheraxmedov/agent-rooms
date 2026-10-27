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
) -> T | None:
    # ponytail: 0.5s DB poll, switch to asyncio.Event per room if latency matters
    deadline = monotonic() + timeout_s
    while True:
        result = check()
        if result is not None:
            return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        await sleep(min(interval_s, remaining))
