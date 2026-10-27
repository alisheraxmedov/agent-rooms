from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = pytest.mark.integration

SUMMARY_KEYS = {"slug", "topic", "status", "max_messages", "message_count", "members", "created_at"}
MESSAGE_KEYS = {"id", "room", "sender", "alias", "kind", "text", "reply_to", "created_at"}


async def make_room(rest: Any, slug: str = "r1", topic: str = "Topic") -> dict[str, Any]:
    response = await rest("human", "POST", "/api/rooms", json={"slug": slug, "topic": topic})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.mark.anyio
async def test_list_is_empty_on_a_fresh_server(rest: Any) -> None:
    response = await rest("human", "GET", "/api/rooms")
    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_create_returns_201_summary_and_appears_in_list(rest: Any) -> None:
    created = await make_room(rest, "alpha", "  Build the thing  ")
    assert set(created) == SUMMARY_KEYS
    assert created["slug"] == "alpha"
    assert created["topic"] == "Build the thing"
    assert created["status"] == "active"
    assert created["max_messages"] == 60
    assert created["message_count"] == 0
    assert created["members"] == []
    assert created["created_at"].endswith("Z")
    listed = (await rest("human", "GET", "/api/rooms")).json()
    assert [room["slug"] for room in listed] == ["alpha"]


@pytest.mark.anyio
async def test_duplicate_room_is_409_room_exists(rest: Any) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms", json={"slug": "r1", "topic": "again"})
    assert response.status_code == 409
    assert response.json()["error"] == "room_exists"
    assert response.json()["message"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"slug": "Bad Slug", "topic": "x"},
        {"slug": "ok", "topic": "   "},
        {"slug": "ok"},
        {"topic": "x"},
        {"slug": 5, "topic": "x"},
        {"slug": "ok", "topic": ["x"]},
        {"slug": None, "topic": "x"},
    ],
)
async def test_create_validates_fields_as_400(rest: Any, payload: dict[str, Any]) -> None:
    response = await rest("human", "POST", "/api/rooms", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
@pytest.mark.parametrize("content", [b"{not json", b"", b"[1, 2]", b'"text"', b"\xff\xfe"])
async def test_malformed_or_non_object_json_is_400(rest: Any, content: bytes) -> None:
    response = await rest(
        "human", "POST", "/api/rooms", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_body_over_64_kib_is_413(rest: Any) -> None:
    body = b'{"slug": "big", "topic": "' + b"x" * 70_000 + b'"}'
    response = await rest(
        "human", "POST", "/api/rooms", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


@pytest.mark.anyio
async def test_chunked_body_over_64_kib_is_413(rest: Any) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"slug": "big", "topic": "'
        for _ in range(70):
            yield b"x" * 1000
        yield b'"}'

    response = await rest(
        "human", "POST", "/api/rooms", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


@pytest.mark.anyio
async def test_body_just_under_the_limit_is_accepted(rest: Any) -> None:
    response = await rest("human", "POST", "/api/rooms", json={"slug": "fits", "topic": "t" * 500})
    assert response.status_code == 201


@pytest.mark.anyio
async def test_messages_returns_room_summary_and_messages(rest: Any) -> None:
    await make_room(rest)
    await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "one"})
    await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "two", "kind": "task"})
    response = await rest("human", "GET", "/api/rooms/r1/messages")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"room", "messages"}
    assert set(body["room"]) == SUMMARY_KEYS
    assert body["room"]["message_count"] == 2
    assert [m["text"] for m in body["messages"]] == ["one", "two"]
    assert set(body["messages"][0]) == MESSAGE_KEYS


@pytest.mark.anyio
async def test_messages_after_and_limit(rest: Any) -> None:
    await make_room(rest)
    for text in ("a", "b", "c", "d"):
        await rest("human", "POST", "/api/rooms/r1/messages", json={"text": text})
    after = (await rest("human", "GET", "/api/rooms/r1/messages", params={"after": 2})).json()
    assert [m["text"] for m in after["messages"]] == ["c", "d"]
    limited = (await rest("human", "GET", "/api/rooms/r1/messages", params={"limit": 1})).json()
    assert [m["text"] for m in limited["messages"]] == ["a"]


@pytest.mark.anyio
@pytest.mark.parametrize("params", [{"after": "x"}, {"after": -1}, {"limit": "many"}, {"limit": "1.5"}])
async def test_messages_rejects_bad_query_params(rest: Any, params: dict[str, Any]) -> None:
    await make_room(rest)
    response = await rest("human", "GET", "/api/rooms/r1/messages", params=params)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_messages_of_unknown_room_is_404(rest: Any) -> None:
    response = await rest("human", "GET", "/api/rooms/nope/messages")
    assert response.status_code == 404
    assert response.json()["error"] == "room_not_found"


@pytest.mark.anyio
async def test_human_post_is_201_message_from_human(rest: Any) -> None:
    await make_room(rest)
    response = await rest(
        "human", "POST", "/api/rooms/r1/messages", json={"text": "hello agents", "kind": "task"}
    )
    assert response.status_code == 201
    message = response.json()
    assert set(message) == MESSAGE_KEYS
    assert message["sender"] == "human"
    assert message["alias"] == ""
    assert message["kind"] == "task"
    assert message["room"] == "r1"
    assert message["reply_to"] is None


@pytest.mark.anyio
async def test_human_post_defaults_to_chat(rest: Any) -> None:
    await make_room(rest)
    message = (await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "hi"})).json()
    assert message["kind"] == "chat"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["approved", "done", "review_request", "changes_requested", "system"])
async def test_human_may_not_post_agent_kinds_403(rest: Any, kind: str) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "x", "kind": kind})
    assert response.status_code == 403
    assert response.json()["error"] == "kind_not_allowed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"text": "x", "kind": "bogus"},
        {"text": "x", "kind": 3},
        {"text": 3},
        {},
        {"text": "   "},
        {"text": "x" * 8001},
    ],
)
async def test_human_post_validates_input_as_400(rest: Any, payload: dict[str, Any]) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms/r1/messages", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_human_post_to_unknown_room_is_404(rest: Any) -> None:
    response = await rest("human", "POST", "/api/rooms/nope/messages", json={"text": "x"})
    assert response.status_code == 404
    assert response.json()["error"] == "room_not_found"


@pytest.mark.anyio
async def test_human_post_to_closed_room_is_409(rest: Any) -> None:
    await make_room(rest)
    await rest("human", "POST", "/api/rooms/r1/status", json={"status": "closed"})
    response = await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "x"})
    assert response.status_code == 409
    assert response.json()["error"] == "room_not_active"


@pytest.mark.anyio
async def test_human_may_post_to_a_paused_room(rest: Any) -> None:
    await make_room(rest)
    await rest("human", "POST", "/api/rooms/r1/status", json={"status": "paused"})
    response = await rest("human", "POST", "/api/rooms/r1/messages", json={"text": "still here"})
    assert response.status_code == 201


@pytest.mark.anyio
async def test_status_change_returns_summary_and_stores_system_notice(rest: Any) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms/r1/status", json={"status": "paused"})
    assert response.status_code == 200
    assert set(response.json()) == SUMMARY_KEYS
    assert response.json()["status"] == "paused"
    messages = (await rest("human", "GET", "/api/rooms/r1/messages")).json()["messages"]
    assert [(m["sender"], m["kind"], m["text"]) for m in messages] == [
        ("system", "system", "Room paused by human.")
    ]
    resumed = await rest("human", "POST", "/api/rooms/r1/status", json={"status": "active"})
    assert resumed.json()["status"] == "active"


@pytest.mark.anyio
async def test_same_status_is_a_no_op(rest: Any) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms/r1/status", json={"status": "active"})
    assert response.status_code == 200
    assert response.json()["message_count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [{"status": "exploded"}, {"status": 1}, {}])
async def test_status_validates_input_as_400(rest: Any, payload: dict[str, Any]) -> None:
    await make_room(rest)
    response = await rest("human", "POST", "/api/rooms/r1/status", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_status_of_unknown_room_is_404(rest: Any) -> None:
    response = await rest("human", "POST", "/api/rooms/nope/status", json={"status": "paused"})
    assert response.status_code == 404


@pytest.mark.anyio
async def test_unknown_path_and_wrong_method(rest: Any) -> None:
    assert (await rest("human", "GET", "/api/rooms/r1/unknown")).status_code == 404
    assert (await rest("human", "DELETE", "/api/rooms")).status_code == 405


@pytest.mark.anyio
async def test_members_appear_in_summary_after_mcp_join(rest: Any, call: Any) -> None:
    await make_room(rest)
    await call("claude", "join_room", room="r1", role="writer", alias="a")
    summary = (await rest("human", "GET", "/api/rooms")).json()[0]
    assert summary["members"] == [{"agent": "claude", "alias": "a", "role": "writer", "listening": False}]
