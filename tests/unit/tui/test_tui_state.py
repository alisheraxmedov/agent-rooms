from agent_rooms.tui.api import Member, Message, Summary, Thread
from agent_rooms.tui.state import InputHistory, ViewState


def summary(slug: str, status: str = "active", count: int = 0, members: tuple[Member, ...] = ()) -> Summary:
    return Summary(slug, f"topic of {slug}", status, 60, count, members, "2026-10-27T10:00:00.000Z")


def message(id_: int, room: str = "r1", text: str = "hi") -> Message:
    return Message(id_, room, "claude", None, "chat", text, None, "2026-10-27T10:00:00.000Z")


def test_a_fresh_state_has_nothing_selected() -> None:
    state = ViewState()
    assert state.selected is None
    assert state.summary is None
    assert state.rooms == []
    assert not state.room_missing


def test_selecting_a_known_room_takes_its_summary_and_resets_the_thread() -> None:
    state = ViewState()
    state.apply_rooms([summary("r1"), summary("r2")])
    state.select("r1")
    state.apply_thread("r1", Thread(summary("r1"), [message(1), message(2)]))
    state.select("r2")
    assert state.selected == "r2"
    assert state.summary == summary("r2")
    assert state.messages == []
    assert state.last_id == 0


def test_apply_thread_returns_only_unseen_messages_and_advances_the_cursor() -> None:
    state = ViewState()
    state.select("r1")
    first = state.apply_thread("r1", Thread(summary("r1"), [message(1), message(2)]))
    again = state.apply_thread("r1", Thread(summary("r1"), [message(2), message(3)]))
    assert [m.id for m in first] == [1, 2]
    assert [m.id for m in again] == [3]
    assert state.last_id == 3
    assert [m.id for m in state.messages] == [1, 2, 3]


def test_a_late_reply_for_another_room_is_ignored() -> None:
    state = ViewState()
    state.select("r2")
    assert state.apply_thread("r1", Thread(summary("r1"), [message(1)])) == []
    assert state.messages == []
    assert state.last_id == 0


def test_apply_thread_refreshes_the_selected_summary() -> None:
    state = ViewState()
    state.select("r1")
    state.apply_thread("r1", Thread(summary("r1", "paused", 3), []))
    assert state.summary is not None
    assert state.summary.status == "paused"


def test_a_room_list_update_refreshes_the_selected_summary() -> None:
    state = ViewState()
    state.apply_rooms([summary("r1")])
    state.select("r1")
    state.apply_rooms([summary("r1", "closed", 9)])
    assert state.summary is not None
    assert (state.summary.status, state.summary.message_count) == ("closed", 9)


def test_a_vanished_selected_room_is_flagged() -> None:
    state = ViewState()
    state.apply_rooms([summary("r1")])
    state.select("r1")
    state.apply_rooms([])
    assert state.room_missing
    assert state.summary is None


def test_a_room_created_locally_can_be_selected_before_the_next_poll() -> None:
    state = ViewState()
    state.apply_rooms([summary("r1")])
    state.upsert_room(summary("new"))
    state.select("new")
    assert [room.slug for room in state.rooms] == ["r1", "new"]
    assert state.summary == summary("new")
    assert not state.room_missing


def test_removing_the_selected_room_deselects_it() -> None:
    state = ViewState()
    state.apply_rooms([summary("r1"), summary("r2")])
    state.select("r1")
    state.remove_room("r1")
    assert state.selected is None
    assert [room.slug for room in state.rooms] == ["r2"]


def test_clear_forgets_everything_but_the_connection_flag() -> None:
    state = ViewState()
    state.offline = True
    state.apply_rooms([summary("r1")])
    state.select("r1")
    state.clear()
    assert (state.rooms, state.selected, state.messages, state.summary) == ([], None, [], None)
    assert state.offline


def test_slugs_follow_the_room_list_order() -> None:
    state = ViewState()
    state.apply_rooms([summary("b"), summary("a")])
    assert state.slugs == ["b", "a"]


def test_history_recalls_older_entries_with_up_and_returns_to_the_draft_with_down() -> None:
    history = InputHistory()
    for line in ("one", "two", "three"):
        history.push(line)
    assert history.previous("draft") == "three"
    assert history.previous("three") == "two"
    assert history.previous("two") == "one"
    assert history.previous("one") == "one"
    assert history.following() == "two"
    assert history.following() == "three"
    assert history.following() == "draft"
    assert history.following() is None


def test_history_ignores_blank_lines_and_consecutive_duplicates() -> None:
    history = InputHistory()
    for line in ("one", "  ", "one", "two", "two"):
        history.push(line)
    assert history.previous("") == "two"
    assert history.previous("two") == "one"
    assert history.previous("one") == "one"


def test_pushing_resets_browsing() -> None:
    history = InputHistory()
    history.push("one")
    history.previous("")
    history.push("two")
    assert history.following() is None
    assert history.previous("") == "two"


def test_an_empty_history_has_nothing_to_recall() -> None:
    assert InputHistory().previous("draft") is None


def test_history_is_bounded() -> None:
    history = InputHistory(limit=3)
    for index in range(10):
        history.push(f"line {index}")
    seen = [history.previous("")]
    while (older := history.previous(seen[-1] or "")) != seen[-1]:
        seen.append(older)
    assert seen == ["line 9", "line 8", "line 7"]
