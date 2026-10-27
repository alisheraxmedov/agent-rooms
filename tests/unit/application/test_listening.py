import time
from contextlib import AbstractContextManager
from datetime import timedelta

import anyio
import pytest
from fakes import FakeClock, FakeTime

from agent_rooms.application.dto import LISTEN_INSTRUCTION, WAIT_TIMEOUT_HINT, NoWake, WaitResult, Wake
from agent_rooms.application.limits import Limits
from agent_rooms.application.services.listening import Listening
from agent_rooms.application.services.membership import Membership
from agent_rooms.application.services.messaging import Messaging
from agent_rooms.application.services.rooms import RoomAdmin
from agent_rooms.domain.errors import NotAMember, RoomNotActive, RoomNotFound
from agent_rooms.domain.models import Role, RoomStatus
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


class TestListen:
    def test_returns_ack_and_stores_the_window(
        self, listening: Listening, alpha: str, uow: SqliteUnitOfWork, clock: FakeClock
    ) -> None:
        ack = listening.listen("claude", alpha, minutes=10)

        assert ack.room == alpha
        assert ack.listening_until == clock.now() + timedelta(minutes=10)
        assert ack.instruction == LISTEN_INSTRUCTION
        member = uow.members.get(alpha, "claude", "")
        assert member is not None
        assert member.listening_until == ack.listening_until
        assert member.listener_session is None

    def test_default_is_thirty_minutes(self, listening: Listening, alpha: str, clock: FakeClock) -> None:
        assert listening.listen("claude", alpha).listening_until == clock.now() + timedelta(minutes=30)

    @pytest.mark.parametrize(("minutes", "expected"), [(0, 1), (-5, 1), (61, 60), (999, 60)])
    def test_minutes_are_clamped(
        self, listening: Listening, alpha: str, clock: FakeClock, minutes: int, expected: int
    ) -> None:
        assert listening.listen("claude", alpha, minutes=minutes).listening_until == clock.now() + timedelta(
            minutes=expected
        )

    def test_stores_the_session(self, listening: Listening, alpha: str, uow: SqliteUnitOfWork) -> None:
        listening.listen("claude", alpha, session="ses_A")
        member = uow.members.get(alpha, "claude", "")
        assert member is not None
        assert member.listener_session == "ses_A"

    def test_unknown_room(self, listening: Listening) -> None:
        with pytest.raises(RoomNotFound):
            listening.listen("claude", "ghost")

    def test_not_a_member(self, listening: Listening, alpha: str) -> None:
        with pytest.raises(NotAMember):
            listening.listen("stranger", alpha)

    @pytest.mark.parametrize("status", [RoomStatus.PAUSED, RoomStatus.CLOSED])
    def test_requires_an_active_room(
        self, listening: Listening, rooms: RoomAdmin, alpha: str, status: RoomStatus
    ) -> None:
        rooms.set_status(alpha, status)
        with pytest.raises(RoomNotActive):
            listening.listen("claude", alpha)

    def test_alias_is_a_separate_identity(
        self, listening: Listening, membership: Membership, alpha: str, uow: SqliteUnitOfWork
    ) -> None:
        membership.join("claude", alpha, Role.WRITER, alias="fixer")
        listening.listen("claude", alpha, alias="fixer")
        main = uow.members.get(alpha, "claude", "")
        fixer = uow.members.get(alpha, "claude", "fixer")
        assert main is not None
        assert fixer is not None
        assert main.listening_until is None
        assert fixer.listening_until is not None


class TestWait:
    @pytest.mark.anyio
    async def test_returns_as_soon_as_a_message_arrives(
        self, listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        async def post_later() -> None:
            await anyio.sleep(0.05)
            messaging.post("codex", alpha, "late reply")

        started = time.monotonic()
        async with anyio.create_task_group() as group:
            group.start_soon(post_later)
            result = await listening.wait("claude", alpha, timeout_s=10)
        elapsed = time.monotonic() - started

        assert [m.text for m in result.messages] == ["late reply"]
        assert result.hint is None
        assert elapsed < 5

    @pytest.mark.anyio
    async def test_returns_waiting_messages_immediately_and_advances_the_cursor(
        self, timed_listening: Listening, messaging: Messaging, alpha: str, fake_time: FakeTime
    ) -> None:
        messaging.post("codex", alpha, "already here")

        result = await timed_listening.wait("claude", alpha)

        assert [m.text for m in result.messages] == ["already here"]
        assert fake_time.elapsed == 0
        assert messaging.read("claude", alpha) == []

    @pytest.mark.anyio
    async def test_own_messages_do_not_wake_it(
        self, timed_listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        messaging.post("claude", alpha, "talking to myself")
        result = await timed_listening.wait("claude", alpha, timeout_s=2)
        assert result.messages == ()
        assert result.hint == WAIT_TIMEOUT_HINT

    @pytest.mark.anyio
    async def test_timeout_returns_the_hint(
        self, timed_listening: Listening, alpha: str, fake_time: FakeTime
    ) -> None:
        result = await timed_listening.wait("claude", alpha, timeout_s=3)
        assert result.messages == ()
        assert result.hint == WAIT_TIMEOUT_HINT
        assert fake_time.elapsed == pytest.approx(3)

    @pytest.mark.anyio
    async def test_zero_timeout_checks_once(
        self, timed_listening: Listening, alpha: str, fake_time: FakeTime
    ) -> None:
        result = await timed_listening.wait("claude", alpha, timeout_s=0)
        assert result.hint == WAIT_TIMEOUT_HINT
        assert fake_time.elapsed == 0

    @pytest.mark.anyio
    async def test_timeout_is_clamped_to_forty_seconds(
        self, uow: SqliteUnitOfWork, clock: FakeClock, fake_time: FakeTime, alpha: str
    ) -> None:
        listening = Listening(uow, clock, Limits(), sleep=fake_time.sleep, monotonic=fake_time.monotonic)

        result = await listening.wait("claude", alpha, timeout_s=999)

        assert result.hint == WAIT_TIMEOUT_HINT
        assert fake_time.elapsed == pytest.approx(40)

    @pytest.mark.anyio
    async def test_paused_room_delivers_the_notice_then_the_stop_hint(
        self, timed_listening: Listening, rooms: RoomAdmin, alpha: str, fake_time: FakeTime
    ) -> None:
        fake_time.on_sleep = lambda: rooms.set_status(alpha, RoomStatus.PAUSED)

        first = await timed_listening.wait("claude", alpha, timeout_s=30)
        second = await timed_listening.wait("claude", alpha, timeout_s=30)

        assert [m.text for m in first.messages] == ["Room paused by human."]
        assert first.hint is None
        assert second.messages == ()
        assert second.hint == "Room 'alpha' is paused. Stop waiting."

    @pytest.mark.anyio
    async def test_unread_messages_win_over_the_inactive_hint(
        self, timed_listening: Listening, messaging: Messaging, rooms: RoomAdmin, alpha: str
    ) -> None:
        messaging.post("codex", alpha, "final words")
        rooms.set_status(alpha, RoomStatus.CLOSED)

        result = await timed_listening.wait("claude", alpha)

        assert [m.text for m in result.messages] == ["final words", "Room closed by human."]
        assert result.hint is None

    @pytest.mark.anyio
    async def test_inactive_room_with_nothing_unread_returns_the_hint_at_once(
        self,
        timed_listening: Listening,
        rooms: RoomAdmin,
        alpha: str,
        messaging: Messaging,
        fake_time: FakeTime,
    ) -> None:
        rooms.set_status(alpha, RoomStatus.CLOSED)
        messaging.read("claude", alpha)

        result = await timed_listening.wait("claude", alpha)

        assert result.messages == ()
        assert result.hint == "Room 'alpha' is closed. Stop waiting."
        assert fake_time.elapsed == 0

    @pytest.mark.anyio
    async def test_unknown_room_and_not_a_member(self, timed_listening: Listening, alpha: str) -> None:
        with pytest.raises(RoomNotFound):
            await timed_listening.wait("claude", "ghost")
        with pytest.raises(NotAMember):
            await timed_listening.wait("stranger", alpha)


class TestNextForListener:
    @pytest.mark.anyio
    async def test_idle_at_once_when_not_listening(
        self, timed_listening: Listening, messaging: Messaging, alpha: str, fake_time: FakeTime
    ) -> None:
        messaging.post("codex", alpha, "nobody is listening")

        assert await timed_listening.next_for_listener("claude") == NoWake("idle")
        assert fake_time.elapsed == 0

    @pytest.mark.anyio
    async def test_wakes_with_the_prompt_advances_the_cursor_and_clears_listening(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        alpha: str,
        uow: SqliteUnitOfWork,
    ) -> None:
        listening.listen("claude", alpha, session="ses_A")
        messaging.post("codex", alpha, "please look at parser.py")

        result = await timed_listening.next_for_listener("claude")

        assert isinstance(result, Wake)
        assert (result.room, result.alias) == (alpha, "")
        assert [m.text for m in result.messages] == ["please look at parser.py"]
        assert result.prompt.startswith(
            '[agent-rooms] 1 new message(s) in room "alpha" for claude (role: writer).'
        )
        assert "please look at parser.py" in result.prompt
        assert result.prompt.endswith("then call `listen` and end your turn to keep listening.")
        member = uow.members.get(alpha, "claude", "")
        assert member is not None
        assert member.last_read_id == result.messages[-1].id
        assert (member.listening_until, member.listener_session) == (None, None)
        assert messaging.read("claude", alpha) == []
        assert await timed_listening.next_for_listener("claude") == NoWake("idle")

    @pytest.mark.anyio
    async def test_prompt_carries_the_alias_hint(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        membership: Membership,
        alpha: str,
    ) -> None:
        membership.join("claude", alpha, Role.WRITER, alias="fixer")
        listening.listen("claude", alpha, alias="fixer")
        messaging.post("codex", alpha, "ping")

        result = await timed_listening.next_for_listener("claude")

        assert isinstance(result, Wake)
        assert result.alias == "fixer"
        assert "for claude/fixer (role: writer)" in result.prompt
        assert '(pass alias="fixer" to every tool)' in result.prompt

    @pytest.mark.anyio
    async def test_wakes_when_a_message_arrives_while_waiting(
        self, listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        listening.listen("claude", alpha)

        async def post_later() -> None:
            await anyio.sleep(0.05)
            messaging.post("codex", alpha, "wake up")

        started = time.monotonic()
        async with anyio.create_task_group() as group:
            group.start_soon(post_later)
            result = await listening.next_for_listener("claude", timeout_s=10)

        assert isinstance(result, Wake)
        assert time.monotonic() - started < 5

    @pytest.mark.anyio
    async def test_idle_when_the_room_is_paused_while_waiting(
        self,
        timed_listening: Listening,
        listening: Listening,
        rooms: RoomAdmin,
        alpha: str,
        fake_time: FakeTime,
    ) -> None:
        listening.listen("claude", alpha)
        fake_time.on_sleep = lambda: rooms.set_status(alpha, RoomStatus.PAUSED)

        assert await timed_listening.next_for_listener("claude", timeout_s=30) == NoWake("idle")
        assert fake_time.elapsed < 1

    @pytest.mark.anyio
    async def test_timeout_when_nothing_arrives(
        self,
        timed_listening: Listening,
        listening: Listening,
        alpha: str,
        fake_time: FakeTime,
        uow: SqliteUnitOfWork,
    ) -> None:
        listening.listen("claude", alpha)

        assert await timed_listening.next_for_listener("claude", timeout_s=5) == NoWake("timeout")

        assert fake_time.elapsed == pytest.approx(5)
        member = uow.members.get(alpha, "claude", "")
        assert member is not None
        assert member.listening_until is not None

    @pytest.mark.anyio
    async def test_timeout_is_clamped_to_590_seconds(
        self, timed_listening: Listening, listening: Listening, alpha: str, fake_time: FakeTime
    ) -> None:
        listening.listen("claude", alpha)

        assert await timed_listening.next_for_listener("claude", timeout_s=99_999) == NoWake("timeout")

        assert fake_time.elapsed == pytest.approx(590)

    @pytest.mark.anyio
    async def test_idle_once_the_listen_window_expires(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        alpha: str,
        clock: FakeClock,
    ) -> None:
        listening.listen("claude", alpha, minutes=1)
        clock.advance(61)
        messaging.post("codex", alpha, "too late")

        assert await timed_listening.next_for_listener("claude") == NoWake("idle")

    @pytest.mark.anyio
    async def test_listen_is_one_shot(
        self, timed_listening: Listening, listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        listening.listen("claude", alpha)
        messaging.post("codex", alpha, "first")
        assert isinstance(await timed_listening.next_for_listener("claude"), Wake)

        messaging.post("codex", alpha, "second")

        assert await timed_listening.next_for_listener("claude") == NoWake("idle")

    @pytest.mark.anyio
    async def test_picks_the_listening_room_that_has_unread_messages(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        rooms: RoomAdmin,
        membership: Membership,
        alpha: str,
        clock: FakeClock,
    ) -> None:
        rooms.create_room("beta", "other")
        membership.join("claude", "beta", Role.WRITER)
        membership.join("codex", "beta", Role.REVIEWER)
        listening.listen("claude", alpha, minutes=5)
        clock.advance(1)
        listening.listen("claude", "beta", minutes=5)
        messaging.post("codex", "beta", "only beta has news")

        result = await timed_listening.next_for_listener("claude")

        assert isinstance(result, Wake)
        assert result.room == "beta"
        assert [m.text for m in result.messages] == ["only beta has news"]

    @pytest.mark.anyio
    async def test_alias_filter_targets_one_identity(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        membership: Membership,
        alpha: str,
    ) -> None:
        membership.join("claude", alpha, Role.WRITER, alias="fixer")
        listening.listen("claude", alpha)
        listening.listen("claude", alpha, alias="fixer")
        messaging.post("codex", alpha, "for everyone")

        result = await timed_listening.next_for_listener("claude", alias="fixer")

        assert isinstance(result, Wake)
        assert result.alias == "fixer"
        assert await timed_listening.next_for_listener("claude", alias="fixer") == NoWake("idle")
        other = await timed_listening.next_for_listener("claude", alias="")
        assert isinstance(other, Wake)
        assert other.alias == ""

    @pytest.mark.anyio
    async def test_session_targeting(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        alpha: str,
        uow: SqliteUnitOfWork,
    ) -> None:
        listening.listen("claude", alpha, session="ses_A")
        messaging.post("codex", alpha, "for ses_A")

        assert await timed_listening.next_for_listener("claude", session="ses_B") == NoWake("idle")

        woken = await timed_listening.next_for_listener("claude", session="ses_A")
        assert isinstance(woken, Wake)

    @pytest.mark.anyio
    async def test_call_without_session_wakes_a_session_bound_listener(
        self, timed_listening: Listening, listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        listening.listen("claude", alpha, session="ses_A")
        messaging.post("codex", alpha, "hello")

        assert isinstance(await timed_listening.next_for_listener("claude"), Wake)

    @pytest.mark.anyio
    async def test_listener_without_session_wakes_any_session(
        self, timed_listening: Listening, listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        listening.listen("claude", alpha)
        messaging.post("codex", alpha, "hello")

        assert isinstance(await timed_listening.next_for_listener("claude", session="ses_B"), Wake)


class CountingUnitOfWork:
    """Delegates to the real unit of work and counts the write transactions that get opened."""

    def __init__(self, inner: SqliteUnitOfWork) -> None:
        self._inner = inner
        self.transactions = 0
        self.rooms = inner.rooms
        self.members = inner.members
        self.messages = inner.messages

    def transaction(self) -> AbstractContextManager[None]:
        self.transactions += 1
        return self._inner.transaction()


class TestDisconnectAwarePolling:
    @pytest.mark.anyio
    async def test_wait_stops_without_consuming_when_the_client_is_gone(
        self, timed_listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        async def disconnected_after_a_message_arrived() -> bool:
            messaging.post("codex", alpha, "arrives while the client is gone")
            return True

        result = await timed_listening.wait(
            "claude", alpha, timeout_s=30, should_stop=disconnected_after_a_message_arrived
        )

        assert result == WaitResult((), WAIT_TIMEOUT_HINT)
        assert [m.text for m in messaging.read("claude", alpha)] == ["arrives while the client is gone"]

    @pytest.mark.anyio
    async def test_next_for_listener_stops_without_consuming_when_the_client_is_gone(
        self,
        timed_listening: Listening,
        listening: Listening,
        messaging: Messaging,
        alpha: str,
        uow: SqliteUnitOfWork,
    ) -> None:
        listening.listen("claude", alpha)

        async def disconnected_after_a_message_arrived() -> bool:
            messaging.post("codex", alpha, "arrives while the client is gone")
            return True

        result = await timed_listening.next_for_listener(
            "claude", timeout_s=30, should_stop=disconnected_after_a_message_arrived
        )

        assert result == NoWake("timeout")
        member = uow.members.get(alpha, "claude", "")
        assert member is not None
        assert member.listening_until is not None
        assert [m.text for m in messaging.read("claude", alpha)] == ["arrives while the client is gone"]

    @pytest.mark.anyio
    async def test_a_present_client_still_gets_its_messages(
        self, timed_listening: Listening, messaging: Messaging, alpha: str
    ) -> None:
        async def connected() -> bool:
            return False

        messaging.post("codex", alpha, "hello")

        result = await timed_listening.wait("claude", alpha, should_stop=connected)

        assert [m.text for m in result.messages] == ["hello"]


class TestQuickReadBeforeWriteLock:
    @pytest.mark.anyio
    async def test_idle_polls_never_open_a_write_transaction(
        self, uow: SqliteUnitOfWork, clock: FakeClock, limits: Limits, fake_time: FakeTime, alpha: str
    ) -> None:
        counting = CountingUnitOfWork(uow)
        service = Listening(counting, clock, limits, sleep=fake_time.sleep, monotonic=fake_time.monotonic)
        service.listen("claude", alpha)

        await service.wait("claude", alpha, timeout_s=1)
        await service.next_for_listener("claude", timeout_s=1)

        assert counting.transactions == 0

    @pytest.mark.anyio
    async def test_delivering_opens_exactly_one_transaction(
        self,
        uow: SqliteUnitOfWork,
        clock: FakeClock,
        limits: Limits,
        messaging: Messaging,
        alpha: str,
    ) -> None:
        counting = CountingUnitOfWork(uow)
        service = Listening(counting, clock, limits)
        messaging.post("codex", alpha, "news")

        result = await service.wait("claude", alpha)

        assert [m.text for m in result.messages] == ["news"]
        assert counting.transactions == 1
