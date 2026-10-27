import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agent_rooms.tui.api import ApiError, RoomsApi

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp_mode() -> str:
    return "auto"


@pytest.fixture
async def api(live_server: Any) -> AsyncIterator[RoomsApi]:
    token = live_server.headers("human")["Authorization"].removeprefix("Bearer ")
    client = RoomsApi(live_server.base_url, token)
    yield client
    await client.aclose()


@pytest.mark.anyio
async def test_a_room_lifecycle_through_the_client(api: RoomsApi) -> None:
    assert await api.list_rooms() == []

    created = await api.create_room("alpha", "  Build it  ")
    assert (created.slug, created.topic, created.status, created.message_count) == (
        "alpha",
        "Build it",
        "active",
        0,
    )

    message = await api.post("alpha", "hello", "chat")
    task = await api.post("alpha", "do the thing", "task")
    assert (message.sender, message.kind, message.text) == ("human", "chat", "hello")
    assert task.kind == "task"
    assert task.id > message.id

    thread = await api.history("alpha")
    assert [m.text for m in thread.messages] == ["hello", "do the thing"]
    later = await api.history("alpha", after=message.id)
    assert [m.id for m in later.messages] == [task.id]

    paused = await api.set_status("alpha", "paused")
    assert paused.status == "paused"
    assert (await api.set_status("alpha", "active")).status == "active"

    with pytest.raises(ApiError) as not_closed:
        await api.delete_room("alpha")
    assert (not_closed.value.status, not_closed.value.code) == (409, "room_not_closed")

    await api.set_status("alpha", "closed")
    await api.delete_room("alpha")
    assert await api.list_rooms() == []


@pytest.mark.anyio
async def test_server_errors_carry_the_servers_code_and_message(api: RoomsApi) -> None:
    await api.create_room("alpha", "Topic")
    with pytest.raises(ApiError) as duplicate:
        await api.create_room("alpha", "Again")
    assert (duplicate.value.status, duplicate.value.code) == (409, "room_exists")
    with pytest.raises(ApiError) as invalid:
        await api.create_room("Bad Slug", "Topic")
    assert (invalid.value.status, invalid.value.code) == (400, "invalid_input")
    assert "slug" in invalid.value.message.lower()
    with pytest.raises(ApiError) as missing:
        await api.history("nope")
    assert (missing.value.status, missing.value.code) == (404, "room_not_found")
    with pytest.raises(ApiError) as kind:
        await api.post("alpha", "x", "approved")
    assert kind.value.status in {400, 403}


@pytest.mark.anyio
async def test_agent_messages_and_members_come_back_typed(api: RoomsApi, call: Any) -> None:
    await api.create_room("alpha", "Pair work")
    await call("claude", "join_room", room="alpha", role="writer", alias="main")
    await call(
        "claude", "post", room="alpha", text="done [bold red]x[/]", kind="review_request", alias="main"
    )

    thread = await api.history("alpha")
    (message,) = thread.messages
    assert (message.sender, message.alias, message.kind) == ("claude", "main", "review_request")
    assert message.text == "done [bold red]x[/]"
    assert message.reply_to is None
    assert message.created_at.endswith("Z")
    (member,) = thread.room.members
    assert (member.agent, member.alias, member.role, member.listening) == ("claude", "main", "writer", False)
    assert [room.members for room in await api.list_rooms()] == [thread.room.members]


@pytest.mark.anyio
async def test_a_wrong_token_is_unauthorized(live_server: Any) -> None:
    client = RoomsApi(live_server.base_url, "definitely-not-a-token-0123456789")
    with pytest.raises(ApiError) as caught:
        await client.list_rooms()
    await client.aclose()
    assert caught.value.unauthorized


@pytest.mark.anyio
async def test_an_agent_token_is_forbidden_from_the_human_api(live_server: Any) -> None:
    token = live_server.headers("claude")["Authorization"].removeprefix("Bearer ")
    client = RoomsApi(live_server.base_url, token)
    with pytest.raises(ApiError) as caught:
        await client.list_rooms()
    await client.aclose()
    assert (caught.value.status, caught.value.code) == (403, "forbidden")


@pytest.mark.anyio
async def test_a_closed_port_is_offline() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = RoomsApi(f"http://127.0.0.1:{port}", "whatever-token-0123456789")
    with pytest.raises(ApiError) as caught:
        await client.list_rooms()
    await client.aclose()
    assert caught.value.offline


@pytest.mark.anyio
async def test_roles_are_assigned_and_a_new_lead_demotes_the_old_one(api: RoomsApi, call: Any) -> None:
    await api.create_room("alpha", "Pair work")
    await call("claude", "join_room", room="alpha", role="writer", alias="main")
    await call("codex", "join_room", room="alpha", role="reviewer")
    await call("opencode", "join_room", room="alpha", role="observer")

    def roles(summary: Any) -> dict[str, str]:
        return {member.name: member.role for member in summary.members}

    summary = await api.set_role("alpha", "codex", "", "writer")
    assert roles(summary) == {"claude/main": "writer", "codex": "writer", "opencode": "observer"}

    summary = await api.set_role("alpha", "claude", "main", "lead")
    assert roles(summary)["claude/main"] == "lead"
    summary = await api.set_role("alpha", "opencode", "", "lead")
    assert roles(summary) == {"claude/main": "observer", "codex": "writer", "opencode": "lead"}

    texts = [m.text for m in (await api.history("alpha")).messages if m.kind == "system"]
    assert any("claude/main" in text and "lead → observer" in text for text in texts)


@pytest.mark.anyio
async def test_role_errors_carry_the_servers_code_and_message(api: RoomsApi, call: Any) -> None:
    await api.create_room("alpha", "Topic")
    await call("claude", "join_room", room="alpha", role="writer")
    with pytest.raises(ApiError) as stranger:
        await api.set_role("alpha", "codex", "", "reviewer")
    assert (stranger.value.status, stranger.value.code) == (409, "not_a_member")
    with pytest.raises(ApiError) as invalid:
        await api.set_role("alpha", "claude", "", "boss")
    assert (invalid.value.status, invalid.value.code) == (400, "invalid_input")
    with pytest.raises(ApiError) as missing:
        await api.set_role("nope", "claude", "", "lead")
    assert (missing.value.status, missing.value.code) == (404, "room_not_found")
