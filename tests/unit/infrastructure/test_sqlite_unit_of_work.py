from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_rooms.domain.errors import RoomAlreadyExists
from agent_rooms.domain.models import Member, Message, MessageKind, Role, Room, RoomStatus
from agent_rooms.infrastructure.sqlite.schema import migrate
from agent_rooms.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

T0 = datetime(2026, 10, 27, 10, 0, 0, 123000, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_room(
    slug: str = "alpha", *, created_at: datetime = T0, status: RoomStatus = RoomStatus.ACTIVE
) -> Room:
    return Room(
        slug=slug, topic="t", status=status, max_messages=60, budget_start_id=0, created_at=created_at
    )


def make_member(
    agent: str = "claude",
    *,
    room: str = "alpha",
    alias: str = "",
    role: Role = Role.WRITER,
    joined: float = 0,
) -> Member:
    return Member(
        room=room,
        agent=agent,
        alias=alias,
        role=role,
        last_read_id=0,
        listening_until=None,
        listener_session=None,
        joined_at=at(joined),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "rooms.db"


@pytest.fixture
def uow(db_path: Path) -> Iterator[SqliteUnitOfWork]:
    unit = SqliteUnitOfWork.open(db_path)
    yield unit
    unit.close()


def post(
    uow: SqliteUnitOfWork,
    sender: str = "claude",
    *,
    room: str = "alpha",
    alias: str = "",
    kind: MessageKind = MessageKind.CHAT,
    text: str = "hi",
    reply_to: int | None = None,
    seconds: float = 0,
) -> Message:
    return uow.messages.add(
        room=room, sender=sender, alias=alias, kind=kind, text=text, reply_to=reply_to, created_at=at(seconds)
    )


class TestOpen:
    def test_creates_missing_parent_directory(self, tmp_path: Path) -> None:
        unit = SqliteUnitOfWork.open(tmp_path / "nested" / "dir" / "rooms.db")
        unit.close()
        assert (tmp_path / "nested" / "dir" / "rooms.db").exists()

    def test_wal_and_foreign_keys_are_on(self, uow: SqliteUnitOfWork) -> None:
        assert uow.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert uow.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert uow.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    def test_migrate_is_idempotent(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        migrate(uow.connection)
        migrate(uow.connection)
        assert uow.rooms.get("alpha") is not None
        versions = uow.connection.execute("SELECT version FROM schema_version").fetchall()
        assert len(versions) == 1


class TestRooms:
    def test_add_get_round_trip_keeps_aware_datetime(self, uow: SqliteUnitOfWork) -> None:
        room = make_room()
        uow.rooms.add(room)
        assert uow.rooms.get("alpha") == room
        assert uow.rooms.get("alpha").created_at.tzinfo is not None  # type: ignore[union-attr]

    def test_get_missing_returns_none(self, uow: SqliteUnitOfWork) -> None:
        assert uow.rooms.get("nope") is None

    def test_duplicate_add_raises(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        with pytest.raises(RoomAlreadyExists):
            uow.rooms.add(make_room())

    def test_list_all_orders_by_created_at_then_slug(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room("zeta", created_at=at(0)))
        uow.rooms.add(make_room("beta", created_at=at(5)))
        uow.rooms.add(make_room("alpha", created_at=at(5)))
        assert [r.slug for r in uow.rooms.list_all()] == ["zeta", "alpha", "beta"]

    def test_set_status(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        uow.rooms.set_status("alpha", RoomStatus.PAUSED)
        room = uow.rooms.get("alpha")
        assert room is not None
        assert room.status is RoomStatus.PAUSED

    def test_reset_budget(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        uow.rooms.reset_budget("alpha", 42)
        room = uow.rooms.get("alpha")
        assert room is not None
        assert room.budget_start_id == 42


class TestMembers:
    @pytest.fixture(autouse=True)
    def _room(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        uow.rooms.add(make_room("other"))

    def test_upsert_inserts_then_updates(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member())
        updated = replace(make_member(), role=Role.REVIEWER, last_read_id=7)
        uow.members.upsert(updated)
        assert uow.members.get("alpha", "claude", "") == updated
        assert len(uow.members.list_for_room("alpha")) == 1

    def test_get_missing_returns_none(self, uow: SqliteUnitOfWork) -> None:
        assert uow.members.get("alpha", "ghost", "") is None

    def test_same_agent_with_other_alias_is_a_separate_member(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member())
        uow.members.upsert(make_member(alias="fixer"))
        assert uow.members.get("alpha", "claude", "fixer") is not None
        assert len(uow.members.list_for_room("alpha")) == 2

    def test_list_for_room_orders_by_joined_at(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member("late", joined=10))
        uow.members.upsert(make_member("early", joined=1))
        uow.members.upsert(make_member("x", room="other", joined=0))
        assert [m.agent for m in uow.members.list_for_room("alpha")] == ["early", "late"]

    def test_remove_returns_true_then_false(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member())
        assert uow.members.remove("alpha", "claude", "") is True
        assert uow.members.remove("alpha", "claude", "") is False
        assert uow.members.get("alpha", "claude", "") is None

    def test_set_listening_sets_and_clears_both_fields(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member())
        uow.members.set_listening("alpha", "claude", "", at(60), "ses_A")
        member = uow.members.get("alpha", "claude", "")
        assert member is not None
        assert (member.listening_until, member.listener_session) == (at(60), "ses_A")
        uow.members.set_listening("alpha", "claude", "", None, None)
        member = uow.members.get("alpha", "claude", "")
        assert member is not None
        assert (member.listening_until, member.listener_session) == (None, None)

    def test_clear_listening_in_room_clears_every_member_of_that_room_only(
        self, uow: SqliteUnitOfWork
    ) -> None:
        for room, agent, alias in (("alpha", "claude", ""), ("alpha", "codex", "x"), ("other", "claude", "")):
            uow.members.upsert(make_member(agent, room=room, alias=alias))
            uow.members.set_listening(room, agent, alias, at(60), "ses_A")

        uow.members.clear_listening_in_room("alpha")

        for room, agent, alias, listening in (
            ("alpha", "claude", "", None),
            ("alpha", "codex", "x", None),
            ("other", "claude", "", at(60)),
        ):
            member = uow.members.get(room, agent, alias)
            assert member is not None
            assert member.listening_until == listening
            assert member.listener_session == (None if listening is None else "ses_A")

    def test_advance_cursor_never_moves_backwards(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member())
        uow.members.advance_cursor("alpha", "claude", "", 10)
        uow.members.advance_cursor("alpha", "claude", "", 4)
        member = uow.members.get("alpha", "claude", "")
        assert member is not None
        assert member.last_read_id == 10

    def test_listening_returns_only_unexpired_members_in_active_rooms(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room("paused", status=RoomStatus.PAUSED))
        for room, until in (("alpha", 60), ("other", -1), ("paused", 60)):
            uow.members.upsert(make_member(room=room))
            uow.members.set_listening(room, "claude", "", at(until), None)
        found = uow.members.listening("claude", at(0))
        assert [m.room for m in found] == ["alpha"]

    def test_listening_excludes_other_agents_and_not_listening(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member("codex"))
        uow.members.set_listening("alpha", "codex", "", at(60), None)
        uow.members.upsert(make_member("claude"))
        assert uow.members.listening("claude", at(0)) == []

    def test_listening_alias_filter(self, uow: SqliteUnitOfWork) -> None:
        for alias in ("", "fixer"):
            uow.members.upsert(make_member(alias=alias))
            uow.members.set_listening("alpha", "claude", alias, at(60), None)
        assert len(uow.members.listening("claude", at(0))) == 2
        assert [m.alias for m in uow.members.listening("claude", at(0), alias="fixer")] == ["fixer"]
        assert [m.alias for m in uow.members.listening("claude", at(0), alias="")] == [""]

    def test_listening_session_rule(self, uow: SqliteUnitOfWork) -> None:
        uow.members.upsert(make_member(alias="a"))
        uow.members.upsert(make_member(alias="b"))
        uow.members.upsert(make_member(alias="c"))
        uow.members.set_listening("alpha", "claude", "a", at(60), "ses_A")
        uow.members.set_listening("alpha", "claude", "b", at(60), "ses_B")
        uow.members.set_listening("alpha", "claude", "c", at(60), None)

        def aliases(**kw: str) -> set[str]:
            return {m.alias for m in uow.members.listening("claude", at(0), **kw)}

        assert aliases() == {"a", "b", "c"}
        assert aliases(session="ses_A") == {"a", "c"}
        assert aliases(session="ses_B") == {"b", "c"}
        assert aliases(session="ses_Z") == {"c"}

    def test_listening_is_ordered_by_listening_until(self, uow: SqliteUnitOfWork) -> None:
        for alias, until in (("late", 90), ("soon", 10), ("mid", 50)):
            uow.members.upsert(make_member(alias=alias))
            uow.members.set_listening("alpha", "claude", alias, at(until), None)
        assert [m.alias for m in uow.members.listening("claude", at(0))] == ["soon", "mid", "late"]


class TestMessages:
    @pytest.fixture(autouse=True)
    def _room(self, uow: SqliteUnitOfWork) -> None:
        uow.rooms.add(make_room())
        uow.rooms.add(make_room("other"))

    def test_add_returns_message_with_increasing_ids(self, uow: SqliteUnitOfWork) -> None:
        first = post(uow, text="one", seconds=1)
        second = post(uow, text="two", kind=MessageKind.TASK, reply_to=first.id, seconds=2)
        assert second.id > first.id
        assert (first.room, first.sender, first.kind, first.text, first.created_at) == (
            "alpha",
            "claude",
            MessageKind.CHAT,
            "one",
            at(1),
        )
        assert second.reply_to == first.id
        assert uow.messages.get("alpha", second.id) == second

    def test_get_is_scoped_to_room(self, uow: SqliteUnitOfWork) -> None:
        message = post(uow)
        assert uow.messages.get("other", message.id) is None
        assert uow.messages.get("alpha", 999) is None

    def test_recent_returns_last_n_ascending(self, uow: SqliteUnitOfWork) -> None:
        for i in range(4):
            post(uow, text=str(i))
        post(uow, room="other", text="elsewhere")
        assert [m.text for m in uow.messages.recent("alpha", 2)] == ["2", "3"]

    def test_after_returns_ascending_with_limit(self, uow: SqliteUnitOfWork) -> None:
        ids = [post(uow, text=str(i)).id for i in range(5)]
        assert [m.id for m in uow.messages.after("alpha", ids[1], 10)] == ids[2:]
        assert [m.id for m in uow.messages.after("alpha", 0, 2)] == ids[:2]

    def test_unread_for_excludes_own_identity_only(self, uow: SqliteUnitOfWork) -> None:
        member = make_member("claude")
        uow.members.upsert(member)
        post(uow, "claude", text="mine")
        post(uow, "claude", alias="fixer", text="same agent other alias")
        post(uow, "codex", text="other agent")
        post(uow, "human", text="from human")
        post(uow, "system", kind=MessageKind.SYSTEM, text="notice")
        texts = [m.text for m in uow.messages.unread_for(member, 50)]
        assert texts == ["same agent other alias", "other agent", "from human", "notice"]

    def test_unread_for_respects_cursor_and_limit(self, uow: SqliteUnitOfWork) -> None:
        member = make_member("claude")
        ids = [post(uow, "codex", text=str(i)).id for i in range(5)]
        cursor_member = replace(member, last_read_id=ids[1])
        assert [m.id for m in uow.messages.unread_for(cursor_member, 50)] == ids[2:]
        assert [m.id for m in uow.messages.unread_for(member, 2)] == ids[:2]

    def test_latest_id_is_zero_for_empty_room(self, uow: SqliteUnitOfWork) -> None:
        assert uow.messages.latest_id("alpha") == 0
        last = post(uow)
        assert uow.messages.latest_id("alpha") == last.id
        assert uow.messages.latest_id("other") == 0

    def test_count(self, uow: SqliteUnitOfWork) -> None:
        post(uow)
        post(uow)
        post(uow, room="other")
        assert uow.messages.count("alpha") == 2

    def test_count_agent_posts_since_excludes_human_and_system(self, uow: SqliteUnitOfWork) -> None:
        first = post(uow, "claude")
        post(uow, "codex")
        post(uow, "human")
        post(uow, "system", kind=MessageKind.SYSTEM)
        assert uow.messages.count_agent_posts_since("alpha", 0) == 2
        assert uow.messages.count_agent_posts_since("alpha", first.id) == 1

    def test_count_kind_since(self, uow: SqliteUnitOfWork) -> None:
        first = post(uow, kind=MessageKind.CHANGES_REQUESTED)
        post(uow, kind=MessageKind.CHANGES_REQUESTED)
        post(uow, kind=MessageKind.CHAT)
        assert uow.messages.count_kind_since("alpha", MessageKind.CHANGES_REQUESTED, 0) == 2
        assert uow.messages.count_kind_since("alpha", MessageKind.CHANGES_REQUESTED, first.id) == 1


def _fail_inside(uow: SqliteUnitOfWork, work: Callable[[], None]) -> None:
    try:
        with uow.transaction():
            work()
            raise RuntimeError("boom")
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")


class TestTransactions:
    def test_exception_rolls_back(self, uow: SqliteUnitOfWork) -> None:
        _fail_inside(uow, lambda: uow.rooms.add(make_room()))
        assert uow.rooms.get("alpha") is None

    def test_exception_propagates_to_the_caller(self, uow: SqliteUnitOfWork) -> None:
        def explode() -> None:
            with uow.transaction():
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            explode()

    def test_commit_persists(self, uow: SqliteUnitOfWork) -> None:
        with uow.transaction():
            uow.rooms.add(make_room())
        assert uow.rooms.get("alpha") is not None

    def test_nested_transaction_joins_outer(self, uow: SqliteUnitOfWork) -> None:
        def nested_then_fail() -> None:
            with uow.transaction():
                uow.rooms.add(make_room())
            assert uow.rooms.get("alpha") is not None

        _fail_inside(uow, nested_then_fail)
        assert uow.rooms.get("alpha") is None

    def test_failed_inner_transaction_rolls_back_whole_outer(self, uow: SqliteUnitOfWork) -> None:
        def outer() -> None:
            with uow.transaction():
                uow.rooms.add(make_room("one"))
                with uow.transaction():
                    raise RuntimeError("inner fails")

        with pytest.raises(RuntimeError, match="inner fails"):
            outer()
        assert uow.rooms.list_all() == []

    def test_usable_after_rollback(self, uow: SqliteUnitOfWork) -> None:
        _fail_inside(uow, lambda: None)
        with uow.transaction():
            uow.rooms.add(make_room())
        assert uow.rooms.get("alpha") is not None

    def test_duplicate_inside_transaction_can_be_caught_without_poisoning_it(
        self, uow: SqliteUnitOfWork
    ) -> None:
        uow.rooms.add(make_room())
        with uow.transaction():
            with pytest.raises(RoomAlreadyExists):
                uow.rooms.add(make_room())
            uow.rooms.add(make_room("beta"))
        assert {r.slug for r in uow.rooms.list_all()} == {"alpha", "beta"}


class TestPersistence:
    def test_data_survives_close_and_open(self, db_path: Path) -> None:
        first = SqliteUnitOfWork.open(db_path)
        first.rooms.add(make_room())
        first.members.upsert(make_member())
        message = post(first, text="persisted")
        first.close()

        second = SqliteUnitOfWork.open(db_path)
        try:
            assert second.rooms.get("alpha") == make_room()
            assert second.members.get("alpha", "claude", "") == make_member()
            assert second.messages.get("alpha", message.id) == message
        finally:
            second.close()


class TestDelete:
    def test_deleting_a_room_removes_members_and_messages_including_reply_chains(
        self, uow: SqliteUnitOfWork
    ) -> None:
        uow.rooms.add(make_room())
        uow.rooms.add(make_room("other"))
        uow.members.upsert(make_member())
        uow.members.upsert(make_member(room="other"))
        first = post(uow)
        post(uow, reply_to=first.id)
        post(uow, room="other")

        assert uow.rooms.delete("alpha") is True

        assert uow.rooms.get("alpha") is None
        assert uow.members.list_for_room("alpha") == []
        assert uow.messages.count("alpha") == 0
        assert uow.messages.count("other") == 1
        assert len(uow.members.list_for_room("other")) == 1

    def test_deleting_an_unknown_room_returns_false(self, uow: SqliteUnitOfWork) -> None:
        assert uow.rooms.delete("ghost") is False
