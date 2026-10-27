from typing import Any

import pytest

pytestmark = pytest.mark.integration

CAP_NOTICE = (
    "Message limit reached (60 agent messages since the last resume). Room paused; a human must resume it."
)
REVIEW_NOTICE = (
    "Review-round limit reached (5 changes_requested since the last resume). "
    "Room paused; a human must resume it."
)


async def setup_room(call: Any, slug: str = "r1") -> None:
    await call("claude", "create_room", slug=slug, topic="Pair work")
    await call("claude", "join_room", room=slug, role="writer")
    await call("codex", "join_room", room=slug, role="reviewer")


async def room_status(rest: Any, slug: str = "r1") -> str:
    rooms = (await rest("human", "GET", "/api/rooms")).json()
    return str(next(room["status"] for room in rooms if room["slug"] == slug))


async def system_notices(rest: Any, slug: str = "r1") -> list[str]:
    messages = (await rest("human", "GET", f"/api/rooms/{slug}/messages", params={"limit": 500})).json()
    return [m["text"] for m in messages["messages"] if m["sender"] == "system"]


@pytest.mark.anyio
async def test_the_61st_agent_post_is_rejected_and_pauses_the_room(
    call: Any, call_error: Any, rest: Any
) -> None:
    await setup_room(call)
    for i in range(60):
        await call("claude", "post", room="r1", text=f"m{i}")
    assert await room_status(rest) == "active"
    error = await call_error("claude", "post", room="r1", text="m60")
    assert error == f"Error executing tool post: guard_tripped: {CAP_NOTICE}"
    assert await room_status(rest) == "paused"
    assert await system_notices(rest) == [CAP_NOTICE]
    assert "room_not_active: " in await call_error("codex", "post", room="r1", text="late")


@pytest.mark.anyio
async def test_human_posts_do_not_count_towards_the_cap(call: Any, rest: Any) -> None:
    await setup_room(call)
    for i in range(3):
        await rest("human", "POST", "/api/rooms/r1/messages", json={"text": f"h{i}"})
    for i in range(60):
        await call("claude", "post", room="r1", text=f"m{i}")
    assert await room_status(rest) == "active"


@pytest.mark.anyio
async def test_resume_resets_the_budget(call: Any, call_error: Any, rest: Any) -> None:
    await setup_room(call)
    for i in range(60):
        await call("claude", "post", room="r1", text=f"m{i}")
    await call_error("claude", "post", room="r1", text="over")
    resumed = await rest("human", "POST", "/api/rooms/r1/status", json={"status": "active"})
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    result = await call("claude", "post", room="r1", text="fresh budget")
    assert result["room_status"] == "active"


@pytest.mark.anyio
async def test_the_5th_changes_requested_is_stored_then_pauses(call: Any, rest: Any) -> None:
    await setup_room(call)
    for i in range(4):
        result = await call("codex", "post", room="r1", text=f"fix {i}", kind="changes_requested")
        assert result == {"id": result["id"], "room_status": "active", "notice": None}
    fifth = await call("codex", "post", room="r1", text="fix 4", kind="changes_requested")
    assert fifth["room_status"] == "paused"
    assert fifth["notice"] == REVIEW_NOTICE
    assert await room_status(rest) == "paused"
    messages = (await rest("human", "GET", "/api/rooms/r1/messages")).json()["messages"]
    assert [m["text"] for m in messages if m["kind"] == "changes_requested"][-1] == "fix 4"
    assert await system_notices(rest) == [REVIEW_NOTICE]


@pytest.mark.anyio
async def test_done_is_stored_then_closes_the_room(call: Any, call_error: Any, rest: Any) -> None:
    await setup_room(call)
    result = await call("claude", "post", room="r1", text="all merged", kind="done")
    assert result["room_status"] == "closed"
    assert result["notice"] == "claude posted done. Room closed."
    assert await room_status(rest) == "closed"
    assert "room_not_active: " in await call_error("codex", "post", room="r1", text="too late")
    assert "room_not_active: " in await call_error("codex", "listen", room="r1")


@pytest.mark.anyio
async def test_done_notice_names_the_alias(call: Any) -> None:
    await call("claude", "create_room", slug="r1", topic="t")
    await call("claude", "join_room", room="r1", role="writer", alias="w1")
    result = await call("claude", "post", room="r1", text="finished", kind="done", alias="w1")
    assert result["notice"] == "claude/w1 posted done. Room closed."


@pytest.mark.anyio
async def test_role_to_kind_policy_through_mcp(call: Any, call_error: Any) -> None:
    await setup_room(call)
    await call("opencode", "join_room", room="r1", role="observer")
    for agent, kind in [
        ("claude", "approved"),
        ("claude", "changes_requested"),
        ("codex", "review_request"),
        ("codex", "done"),
        ("opencode", "task"),
        ("opencode", "done"),
    ]:
        error = await call_error(agent, "post", room="r1", text="x", kind=kind)
        assert "kind_not_allowed: " in error, (agent, kind)
    for agent, kind in [("claude", "task"), ("codex", "task"), ("codex", "approved"), ("opencode", "chat")]:
        await call(agent, "post", room="r1", text="x", kind=kind)
