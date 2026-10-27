import re
from typing import Any

import anyio
import httpx2
import pytest

pytestmark = pytest.mark.integration

ROLE_URL = "/api/rooms/r1/members/role"


async def setup_room(call: Any, rest: Any) -> None:
    created = await rest("human", "POST", "/api/rooms", json={"slug": "r1", "topic": "Lead work"})
    assert created.status_code == 201
    await call("claude", "join_room", room="r1", role="writer")
    await call("codex", "join_room", room="r1", role="reviewer")


async def set_role(rest: Any, agent: str, role: str, alias: str | None = None) -> httpx2.Response:
    body: dict[str, Any] = {"agent": agent, "role": role}
    if alias is not None:
        body["alias"] = alias
    return await rest("human", "POST", ROLE_URL, json=body)


def roles(summary: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(m["agent"], m["alias"]): m["role"] for m in summary["members"]}


async def system_texts(rest: Any) -> list[str]:
    body = (await rest("human", "GET", "/api/rooms/r1/messages")).json()
    return [m["text"] for m in body["messages"] if m["kind"] == "system"]


@pytest.mark.anyio
async def test_an_agent_can_join_as_lead(call: Any, rest: Any) -> None:
    await setup_room(call, rest)

    joined = await call("opencode", "join_room", room="r1", role="lead")

    assert roles(joined["room"])[("opencode", "")] == "lead"


@pytest.mark.anyio
async def test_a_second_lead_is_rejected_over_mcp(call: Any, call_error: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await call("opencode", "join_room", room="r1", role="lead", alias="boss")

    error = await call_error("claude", "join_room", room="r1", role="lead", alias="second")

    assert error.endswith(
        "invalid_input: Room 'r1' already has a lead (opencode/boss). Ask the human to change roles."
    )


@pytest.mark.anyio
async def test_join_room_advertises_lead_and_says_only_the_human_changes_roles(
    describe_server: Any,
) -> None:
    tools = {tool.name: tool for tool in (await describe_server("claude")).tools}
    join = tools["join_room"]
    assert join.input_schema["properties"]["role"]["enum"] == ["writer", "reviewer", "observer", "lead"]
    description = join.description or ""
    assert "lead" in description
    assert "can only be changed later by the human" in " ".join(description.split())
    for role in ("writer", "reviewer", "observer", "lead"):
        assert role in description
    assert not {"set_role", "set_member_role", "change_role"} & set(tools)


@pytest.mark.anyio
async def test_human_changes_a_role_and_gets_the_summary(call: Any, rest: Any) -> None:
    await setup_room(call, rest)

    response = await set_role(rest, "claude", "observer")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert roles(summary) == {("claude", ""): "observer", ("codex", ""): "reviewer"}
    assert summary["message_count"] == 1
    assert await system_texts(rest) == ["Human changed claude's role: writer → observer."]


@pytest.mark.anyio
async def test_alias_defaults_to_empty_and_targets_one_persona(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await call("claude", "join_room", room="r1", role="observer", alias="watcher")

    persona = await set_role(rest, "claude", "reviewer", alias="watcher")
    default = await set_role(rest, "claude", "lead")

    assert persona.status_code == 400
    assert persona.json()["error"] == "invalid_input"
    assert default.status_code == 200
    assert roles(default.json())[("claude", "")] == "lead"
    assert roles(default.json())[("claude", "watcher")] == "observer"


@pytest.mark.anyio
async def test_assigning_lead_demotes_the_previous_lead_with_both_notices(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    assert (await set_role(rest, "claude", "lead")).status_code == 200

    response = await set_role(rest, "codex", "lead")

    assert response.status_code == 200
    assert roles(response.json()) == {("claude", ""): "observer", ("codex", ""): "lead"}
    assert await system_texts(rest) == [
        "Human changed claude's role: writer → lead.",
        "Human changed claude's role: lead → observer.",
        "Human changed codex's role: reviewer → lead.",
    ]


@pytest.mark.anyio
async def test_a_new_member_cannot_join_as_lead_until_the_seat_is_free(
    call: Any, call_error: Any, rest: Any
) -> None:
    await setup_room(call, rest)
    await set_role(rest, "claude", "lead")
    assert "already has a lead (claude)" in await call_error("codex", "join_room", room="r1", role="lead")

    await set_role(rest, "claude", "writer")

    joined = await call("codex", "join_room", room="r1", role="lead")
    assert roles(joined["room"])[("codex", "")] == "lead"


@pytest.mark.anyio
async def test_same_role_is_a_no_op_without_a_message(call: Any, rest: Any) -> None:
    await setup_room(call, rest)

    response = await set_role(rest, "claude", "writer")

    assert response.status_code == 200
    assert response.json()["message_count"] == 0
    assert await system_texts(rest) == []


@pytest.mark.anyio
async def test_a_listening_agent_is_woken_by_the_role_change_notice(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await call("codex", "listen", room="r1")
    results: list[httpx2.Response] = []

    async def poll() -> None:
        results.append(await rest("codex", "GET", "/api/listen/next", params={"timeout": 30}))

    async with anyio.create_task_group() as tg:
        tg.start_soon(poll)
        await anyio.sleep(0.2)
        assert results == []
        assert (await set_role(rest, "codex", "lead")).status_code == 200

    response = results[0]
    assert response.status_code == 200
    body = response.json()
    assert [m["text"] for m in body["messages"]] == ["Human changed codex's role: reviewer → lead."]
    assert "for codex (role: lead)." in body["prompt"]
    assert "Server-verified senders in this batch: system." in body["prompt"]
    assert re.search(r"│ Human changed codex's role: reviewer → lead\.", body["prompt"])
    summary = (await rest("human", "GET", "/api/rooms/r1/messages")).json()["room"]
    assert [m["listening"] for m in summary["members"]] == [False, False]


@pytest.mark.anyio
async def test_the_demoted_lead_is_woken_too_and_sees_observer(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await set_role(rest, "claude", "lead")
    await call("claude", "listen", room="r1")

    await set_role(rest, "codex", "lead")
    response = await rest("claude", "GET", "/api/listen/next", params={"timeout": 5})

    assert response.status_code == 200
    body = response.json()
    assert "for claude (role: observer)." in body["prompt"]
    assert [m["text"] for m in body["messages"]][-2:] == [
        "Human changed claude's role: lead → observer.",
        "Human changed codex's role: reviewer → lead.",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
async def test_agent_tokens_get_403_on_the_role_route(call: Any, rest: Any, agent: str) -> None:
    await setup_room(call, rest)

    response = await rest(agent, "POST", ROLE_URL, json={"agent": "claude", "role": "lead"})

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert (await rest("human", "GET", "/api/rooms/r1/messages")).json()["room"]["message_count"] == 0


@pytest.mark.anyio
async def test_the_role_route_requires_a_token(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    assert (await rest(None, "POST", ROLE_URL, json={"agent": "claude", "role": "lead"})).status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"agent": "claude", "role": "boss"},
        {"agent": "claude", "role": ""},
        {"agent": "claude"},
        {"role": "lead"},
        {"agent": "claude", "role": 5},
        {"agent": 5, "role": "lead"},
        {"agent": "claude", "alias": "Bad Alias", "role": "lead"},
        {"agent": "claude", "alias": 3, "role": "lead"},
    ],
)
async def test_invalid_input_is_400(call: Any, rest: Any, payload: dict[str, Any]) -> None:
    await setup_room(call, rest)

    response = await rest("human", "POST", ROLE_URL, json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"


@pytest.mark.anyio
async def test_malformed_json_is_400(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    response = await rest(
        "human", "POST", ROLE_URL, content=b"{nope", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_a_non_member_is_409(call: Any, rest: Any) -> None:
    await setup_room(call, rest)

    response = await set_role(rest, "opencode", "lead")

    assert response.status_code == 409
    assert response.json()["error"] == "not_a_member"


@pytest.mark.anyio
async def test_an_unknown_room_is_404(rest: Any) -> None:
    response = await rest(
        "human", "POST", "/api/rooms/ghost/members/role", json={"agent": "claude", "role": "lead"}
    )
    assert response.status_code == 404
    assert response.json()["error"] == "room_not_found"


@pytest.mark.anyio
async def test_writer_reviewer_exclusivity_violation_is_400_and_changes_nothing(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await call("codex", "join_room", room="r1", role="observer", alias="watcher")

    response = await set_role(rest, "codex", "writer", alias="watcher")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"
    assert "cannot be both writer and reviewer" in response.json()["message"]
    assert await system_texts(rest) == []


@pytest.mark.anyio
async def test_the_lead_may_post_task_and_close_the_room_with_done(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await set_role(rest, "claude", "lead")

    task = await call("claude", "post", room="r1", text="Split the work", kind="task")
    done = await call("claude", "post", room="r1", text="All merged", kind="done")

    assert task["room_status"] == "active"
    assert done["room_status"] == "closed"
    assert (await rest("human", "GET", "/api/rooms")).json()[0]["status"] == "closed"


@pytest.mark.anyio
async def test_the_lead_may_not_post_review_kinds(call: Any, call_error: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await set_role(rest, "claude", "lead")

    for kind in ("review_request", "changes_requested", "approved"):
        error = await call_error("claude", "post", room="r1", text="x", kind=kind)
        assert "kind_not_allowed" in error
        assert "Role 'lead'" in error


@pytest.mark.anyio
async def test_a_demoted_lead_loses_the_lead_permissions(call: Any, call_error: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await set_role(rest, "claude", "lead")
    await set_role(rest, "codex", "lead")

    error = await call_error("claude", "post", room="r1", text="x", kind="task")

    assert "kind_not_allowed" in error
    assert "Role 'observer'" in error


@pytest.mark.anyio
async def test_role_assignment_works_in_a_paused_room(call: Any, rest: Any) -> None:
    await setup_room(call, rest)
    await rest("human", "POST", "/api/rooms/r1/status", json={"status": "paused"})

    assert (await set_role(rest, "claude", "lead")).status_code == 200
