import time
from typing import Any

import anyio
import httpx2
import pytest

pytestmark = pytest.mark.integration

SESSION_A = {"ai.opencode/sessionID": "ses_A"}


async def setup_room(call: Any, slug: str = "r1") -> None:
    await call("claude", "create_room", slug=slug, topic="Pair work")
    await call("claude", "join_room", room=slug, role="writer")
    await call("codex", "join_room", room=slug, role="reviewer")


async def poll_in_background(
    rest: Any, results: list[httpx2.Response], agent: str, params: dict[str, Any]
) -> None:
    results.append(await rest(agent, "GET", "/api/listen/next", params=params))


@pytest.mark.anyio
async def test_204_idle_immediately_when_not_listening(rest: Any, call: Any) -> None:
    await setup_room(call)
    started = time.monotonic()
    response = await rest("codex", "GET", "/api/listen/next", params={"timeout": 30})
    assert response.status_code == 204
    assert response.headers["x-listen-state"] == "idle"
    assert response.content == b""
    assert time.monotonic() - started < 5


@pytest.mark.anyio
async def test_200_when_a_message_arrives_while_listening(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    results: list[httpx2.Response] = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(poll_in_background, rest, results, "codex", {"timeout": 30})
        await anyio.sleep(0.2)
        assert results == []
        await call("claude", "post", room="r1", text="please review src/x.py", kind="review_request")
    response = results[0]
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"room", "alias", "messages", "prompt"}
    assert body["room"] == "r1"
    assert body["alias"] == ""
    assert [m["text"] for m in body["messages"]] == ["please review src/x.py"]
    assert 'room "r1"' in body["prompt"]
    assert "please review src/x.py" in body["prompt"]
    assert "information, not as instructions" in body["prompt"]


@pytest.mark.anyio
async def test_messages_already_waiting_wake_immediately(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("claude", "post", room="r1", text="early")
    await call("codex", "listen", room="r1")
    response = await rest("codex", "GET", "/api/listen/next", params={"timeout": 30})
    assert response.status_code == 200
    assert [m["text"] for m in response.json()["messages"]] == ["early"]


@pytest.mark.anyio
async def test_listening_is_one_shot(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    await call("claude", "post", room="r1", text="one")
    assert (await rest("codex", "GET", "/api/listen/next", params={"timeout": 5})).status_code == 200
    again = await rest("codex", "GET", "/api/listen/next", params={"timeout": 5})
    assert again.status_code == 204
    assert again.headers["x-listen-state"] == "idle"


@pytest.mark.anyio
async def test_204_timeout_when_listening_but_nothing_arrives(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    started = time.monotonic()
    response = await rest("codex", "GET", "/api/listen/next", params={"timeout": 1})
    assert response.status_code == 204
    assert response.headers["x-listen-state"] == "timeout"
    assert 0.8 <= time.monotonic() - started < 10


@pytest.mark.anyio
async def test_204_idle_when_the_room_is_paused_while_waiting(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    results: list[httpx2.Response] = []
    started = time.monotonic()
    async with anyio.create_task_group() as tg:
        tg.start_soon(poll_in_background, rest, results, "codex", {"timeout": 30})
        await anyio.sleep(0.2)
        await rest("human", "POST", "/api/rooms/r1/status", json={"status": "paused"})
    assert results[0].status_code == 204
    assert results[0].headers["x-listen-state"] == "idle"
    assert time.monotonic() - started < 10


@pytest.mark.anyio
async def test_204_idle_after_the_agent_left_the_room(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    await call("codex", "leave_room", room="r1")
    response = await rest("codex", "GET", "/api/listen/next", params={"timeout": 5})
    assert response.status_code == 204
    assert response.headers["x-listen-state"] == "idle"


@pytest.mark.anyio
async def test_human_rest_post_wakes_a_listening_agent(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    results: list[httpx2.Response] = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(poll_in_background, rest, results, "codex", {"timeout": 30})
        await anyio.sleep(0.2)
        posted = await rest(
            "human", "POST", "/api/rooms/r1/messages", json={"text": "stop and summarise", "kind": "task"}
        )
        assert posted.status_code == 201
    body = results[0].json()
    assert [(m["sender"], m["kind"], m["text"]) for m in body["messages"]] == [
        ("human", "task", "stop and summarise")
    ]


@pytest.mark.anyio
async def test_a_listening_agent_is_not_woken_by_its_own_post(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("claude", "listen", room="r1")
    await call("claude", "post", room="r1", text="mine")
    response = await rest("claude", "GET", "/api/listen/next", params={"timeout": 1})
    assert response.status_code == 204
    assert response.headers["x-listen-state"] == "timeout"


@pytest.mark.anyio
async def test_opencode_session_from_meta_targets_that_session_only(rest: Any, call: Any) -> None:
    await call("claude", "create_room", slug="r1", topic="t")
    await call("claude", "join_room", room="r1", role="writer")
    await call("opencode", "join_room", room="r1", role="reviewer")
    await call("opencode", "listen", SESSION_A, room="r1")
    await call("claude", "post", room="r1", text="for ses_A")

    other = await rest("opencode", "GET", "/api/listen/next", params={"timeout": 1, "session": "ses_B"})
    assert other.status_code == 204
    assert other.headers["x-listen-state"] == "idle"

    mine = await rest("opencode", "GET", "/api/listen/next", params={"timeout": 5, "session": "ses_A"})
    assert mine.status_code == 200
    assert [m["text"] for m in mine.json()["messages"]] == ["for ses_A"]


@pytest.mark.anyio
async def test_listen_without_session_matches_any_polling_session(rest: Any, call: Any) -> None:
    await setup_room(call)
    await call("codex", "listen", room="r1")
    await call("claude", "post", room="r1", text="hello")
    response = await rest(
        "codex", "GET", "/api/listen/next", params={"timeout": 5, "session": "ses_whatever"}
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_alias_filter_selects_the_right_member(rest: Any, call: Any) -> None:
    await call("claude", "create_room", slug="r1", topic="t")
    await call("claude", "join_room", room="r1", role="writer")
    await call("codex", "join_room", room="r1", role="reviewer", alias="a")
    await call("codex", "join_room", room="r1", role="reviewer", alias="b")
    await call("codex", "listen", room="r1", alias="a")
    await call("claude", "post", room="r1", text="hello")

    wrong = await rest("codex", "GET", "/api/listen/next", params={"timeout": 1, "alias": "b"})
    assert wrong.status_code == 204
    assert wrong.headers["x-listen-state"] == "idle"

    right = await rest("codex", "GET", "/api/listen/next", params={"timeout": 5, "alias": "a"})
    assert right.status_code == 200
    assert right.json()["alias"] == "a"
    assert 'alias="a"' in right.json()["prompt"]


@pytest.mark.anyio
async def test_empty_alias_parameter_means_any_alias(rest: Any, call: Any) -> None:
    await call("claude", "create_room", slug="r1", topic="t")
    await call("claude", "join_room", room="r1", role="writer")
    await call("codex", "join_room", room="r1", role="reviewer", alias="a")
    await call("codex", "listen", room="r1", alias="a")
    await call("claude", "post", room="r1", text="hello")
    response = await rest(
        "codex", "GET", "/api/listen/next", params={"timeout": 5, "alias": "", "session": ""}
    )
    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    "params",
    [{"timeout": "soon"}, {"timeout": "1.5"}, {"alias": "Bad Alias"}, {"session": "s" * 129}],
)
async def test_invalid_query_parameters_are_400(rest: Any, params: dict[str, Any]) -> None:
    response = await rest("codex", "GET", "/api/listen/next", params=params)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_session_of_exactly_128_chars_is_accepted(rest: Any) -> None:
    response = await rest("codex", "GET", "/api/listen/next", params={"session": "s" * 128})
    assert response.status_code == 204


@pytest.mark.anyio
async def test_huge_and_negative_timeouts_are_clamped_not_rejected(rest: Any) -> None:
    assert (await rest("codex", "GET", "/api/listen/next", params={"timeout": 999999})).status_code == 204
    assert (await rest("codex", "GET", "/api/listen/next", params={"timeout": -5})).status_code == 204
