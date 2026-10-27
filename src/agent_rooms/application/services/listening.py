import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from agent_rooms.application.dto import (
    LISTEN_INSTRUCTION,
    WAIT_TIMEOUT_HINT,
    ListenAck,
    NoWake,
    WaitResult,
    Wake,
)
from agent_rooms.application.limits import Limits, clamp
from agent_rooms.application.polling import poll_until
from agent_rooms.application.ports import Clock, UnitOfWork
from agent_rooms.application.prompts import build_wake_prompt
from agent_rooms.application.services._lookup import require_member, require_room
from agent_rooms.domain.errors import RoomNotActive
from agent_rooms.domain.models import Member, Message, RoomStatus


class Listening:
    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        limits: Limits,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._limits = limits
        self._sleep = sleep
        self._monotonic = monotonic

    def listen(
        self, agent: str, room: str, alias: str = "", minutes: int = 30, session: str | None = None
    ) -> ListenAck:
        found = require_room(self._uow, room)
        require_member(self._uow, room, agent, alias)
        if found.status is not RoomStatus.ACTIVE:
            raise RoomNotActive(f"Room '{room}' is {found.status}; there is nothing to listen for.")
        until = self._clock.now() + timedelta(minutes=clamp(minutes, 1, self._limits.listen_max_minutes))
        self._uow.members.set_listening(room, agent, alias, until, session)
        return ListenAck(room, until, LISTEN_INSTRUCTION)

    async def wait(
        self,
        agent: str,
        room: str,
        alias: str = "",
        timeout_s: int = 30,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> WaitResult:
        require_room(self._uow, room)
        require_member(self._uow, room, agent, alias)

        def check() -> WaitResult | None:
            current = require_room(self._uow, room)
            member = require_member(self._uow, room, agent, alias)
            if self._uow.messages.unread_for(member, 1):
                delivered = self._deliver_unread(room, agent, alias)
                if delivered:
                    return WaitResult(delivered, None)
            if current.status is not RoomStatus.ACTIVE:
                return WaitResult((), f"Room '{room}' is {current.status}. Stop waiting.")
            return None

        result = await self._poll(check, clamp(timeout_s, 0, self._limits.wait_max_s), should_stop)
        return WaitResult((), WAIT_TIMEOUT_HINT) if result is None else result

    async def next_for_listener(
        self,
        agent: str,
        timeout_s: int = 540,
        alias: str | None = None,
        session: str | None = None,
        should_stop: Callable[[], Awaitable[bool]] | None = None,
    ) -> Wake | NoWake:
        def check() -> Wake | NoWake | None:
            candidates = self._uow.members.listening(agent, self._clock.now(), alias=alias, session=session)
            if not candidates:
                return NoWake("idle")
            for member in candidates:
                if not self._uow.messages.unread_for(member, 1):
                    continue
                wake = self._wake_if_unread(member)
                if wake is not None:
                    return wake
            return None

        result: Wake | NoWake | None = await self._poll(
            check, clamp(timeout_s, 0, self._limits.listen_next_max_s), should_stop
        )
        return NoWake("timeout") if result is None else result

    def _deliver_unread(self, room: str, agent: str, alias: str) -> tuple[Message, ...]:
        with self._uow.transaction():
            member = require_member(self._uow, room, agent, alias)
            unread = self._uow.messages.unread_for(member, self._limits.read_max)
            if unread:
                self._uow.members.advance_cursor(room, agent, alias, unread[-1].id)
        return tuple(unread)

    def _wake_if_unread(self, candidate: Member) -> Wake | None:
        with self._uow.transaction():
            member = self._uow.members.get(candidate.room, candidate.agent, candidate.alias)
            if member is None or not member.is_listening(self._clock.now()):
                return None
            unread = self._uow.messages.unread_for(member, self._limits.read_max)
            if not unread:
                return None
            self._uow.members.advance_cursor(member.room, member.agent, member.alias, unread[-1].id)
            self._uow.members.set_listening(member.room, member.agent, member.alias, None, None)
            room = require_room(self._uow, member.room)
        return Wake(member.room, member.alias, tuple(unread), build_wake_prompt(room, member, unread))

    async def _poll[T](
        self,
        check: Callable[[], T | None],
        timeout_s: int,
        should_stop: Callable[[], Awaitable[bool]] | None,
    ) -> T | None:
        return await poll_until(
            check,
            timeout_s=timeout_s,
            interval_s=self._limits.poll_interval_s,
            monotonic=self._monotonic,
            sleep=self._sleep,
            should_stop=should_stop,
        )
