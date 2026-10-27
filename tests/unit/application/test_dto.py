import dataclasses
from datetime import UTC, datetime

import pytest

from agent_rooms.application.dto import (
    LISTEN_INSTRUCTION,
    WAIT_TIMEOUT_HINT,
    JoinResult,
    ListenAck,
    MemberView,
    NoWake,
    PostResult,
    RoomSummary,
    WaitResult,
    Wake,
)
from agent_rooms.domain.models import Message, MessageKind, Role, RoomStatus

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def test_exact_texts() -> None:
    assert LISTEN_INSTRUCTION == "End your turn now. You will be woken with the next message."
    assert WAIT_TIMEOUT_HINT == (
        "No new messages yet. Call wait again to keep waiting, or call listen and end your turn."
    )


def test_dtos_are_frozen_dataclasses() -> None:
    message = Message(1, "demo", "claude", "", MessageKind.CHAT, "hi", None, NOW)
    view = MemberView("claude", "", Role.WRITER, True)
    summary = RoomSummary("demo", "t", RoomStatus.ACTIVE, 60, 0, (view,), NOW)
    results = [
        view,
        summary,
        JoinResult(summary, (), "protocol"),
        PostResult(message, RoomStatus.ACTIVE, None),
        ListenAck("demo", NOW, LISTEN_INSTRUCTION),
        WaitResult((), None),
        Wake("demo", "", (), "prompt"),
        NoWake("idle"),
    ]
    for dto in results:
        assert dataclasses.is_dataclass(dto)
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.listening = False  # type: ignore[misc]


def test_no_wake_carries_reason() -> None:
    assert NoWake("timeout").reason == "timeout"
