import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from fakes import FakeClock, SlowUnreadUnitOfWork

from agent_rooms.application.dto import Wake
from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

ROUNDS = 25
DEADLINE_S = 10


@pytest.fixture
def slow_messaging(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits) -> Messaging:
    return Messaging(SlowUnreadUnitOfWork(uow), clock, limits)  # type: ignore[arg-type]


@pytest.fixture
def slow_listening(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits) -> Listening:
    return Listening(SlowUnreadUnitOfWork(uow), clock, limits)  # type: ignore[arg-type]


def ids_delivered_by_racing(*claimers: Callable[[], list[int]]) -> list[int]:
    barrier = threading.Barrier(len(claimers))

    def run(claim: Callable[[], list[int]]) -> list[int]:
        barrier.wait()
        return claim()

    with ThreadPoolExecutor(max_workers=len(claimers)) as pool:
        futures = [pool.submit(run, claim) for claim in claimers]
        return [i for future in futures for i in future.result(timeout=DEADLINE_S)]


def wake_ids(listening: Listening) -> list[int]:
    result = asyncio.run(listening.next_for_listener("claude", timeout_s=0))
    return [m.id for m in result.messages] if isinstance(result, Wake) else []


def wait_ids(listening: Listening) -> list[int]:
    return [m.id for m in asyncio.run(listening.wait("claude", "alpha", timeout_s=0)).messages]


def test_concurrent_reads_deliver_each_message_exactly_once(
    slow_messaging: Messaging, messaging: Messaging, alpha: str
) -> None:
    total = 40
    delivered: list[int] = []
    lock = threading.Lock()
    done = threading.Event()

    def produce() -> list[int]:
        ids = [messaging.post("codex", alpha, f"m{i}").message.id for i in range(total)]
        done.set()
        return ids

    def consume() -> None:
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            finished = done.is_set()
            got = [m.id for m in slow_messaging.read("claude", alpha, limit=3)]
            with lock:
                delivered.extend(got)
            if finished and not got:
                return

    with ThreadPoolExecutor(max_workers=3) as pool:
        producer = pool.submit(produce)
        consumers = [pool.submit(consume), pool.submit(consume)]
        for consumer in consumers:
            consumer.result(timeout=DEADLINE_S + 5)
        expected = producer.result()

    assert sorted(delivered) == expected


def test_wake_racing_read_delivers_each_message_once(
    slow_listening: Listening,
    slow_messaging: Messaging,
    messaging: Messaging,
    listening: Listening,
    alpha: str,
) -> None:
    for round_no in range(ROUNDS):
        listening.listen("claude", alpha)
        posted = messaging.post("codex", alpha, f"round {round_no}").message.id

        delivered = ids_delivered_by_racing(
            lambda: wake_ids(slow_listening),
            lambda: [m.id for m in slow_messaging.read("claude", alpha)],
        )

        assert delivered == [posted], f"round {round_no}"


def test_wait_racing_read_delivers_each_message_once(
    slow_listening: Listening, slow_messaging: Messaging, messaging: Messaging, alpha: str
) -> None:
    for round_no in range(ROUNDS):
        posted = messaging.post("codex", alpha, f"round {round_no}").message.id

        delivered = ids_delivered_by_racing(
            lambda: wait_ids(slow_listening),
            lambda: [m.id for m in slow_messaging.read("claude", alpha)],
        )

        assert delivered == [posted], f"round {round_no}"


def test_two_wakes_racing_deliver_once_and_clear_listening_once(
    slow_listening: Listening, messaging: Messaging, listening: Listening, alpha: str
) -> None:
    for round_no in range(ROUNDS):
        listening.listen("claude", alpha)
        posted = messaging.post("codex", alpha, f"round {round_no}").message.id

        delivered = ids_delivered_by_racing(
            lambda: wake_ids(slow_listening), lambda: wake_ids(slow_listening)
        )

        assert delivered == [posted], f"round {round_no}"
