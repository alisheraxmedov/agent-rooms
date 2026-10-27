from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeClock, FakeTime

from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.models import Role
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


@pytest.fixture
def protocol_text() -> str:
    return "PROTOCOL TEXT"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_time(clock: FakeClock) -> FakeTime:
    return FakeTime(clock)


@pytest.fixture
def limits() -> Limits:
    return Limits(poll_interval_s=0.01)


@pytest.fixture
def uow(tmp_path: Path) -> Iterator[SqliteUnitOfWork]:
    unit = SqliteUnitOfWork.open(tmp_path / "rooms.db")
    yield unit
    unit.close()


@pytest.fixture
def rooms(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits) -> RoomAdmin:
    return RoomAdmin(uow, clock, limits)


@pytest.fixture
def membership(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits, protocol_text: str) -> Membership:
    return Membership(uow, clock, limits, protocol_text)


@pytest.fixture
def messaging(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits) -> Messaging:
    return Messaging(uow, clock, limits)


@pytest.fixture
def listening(uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits) -> Listening:
    return Listening(uow, clock, limits)


@pytest.fixture
def timed_listening(
    uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits, fake_time: FakeTime
) -> Listening:
    return Listening(uow, clock, limits, sleep=fake_time.sleep, monotonic=fake_time.monotonic)


@pytest.fixture
def alpha(rooms: RoomAdmin, membership: Membership) -> str:
    """Room 'alpha' with `claude` (writer) and `codex` (reviewer) joined."""
    rooms.create_room("alpha", "Review the parser")
    membership.join("claude", "alpha", Role.WRITER)
    membership.join("codex", "alpha", Role.REVIEWER)
    return "alpha"
