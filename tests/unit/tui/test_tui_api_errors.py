import json
from collections.abc import Callable

import httpx2
import pytest

from agent_rooms.tui.api import ApiError, RoomsApi

SUMMARY = {
    "slug": "r1",
    "topic": "Topic",
    "status": "active",
    "max_messages": 60,
    "message_count": 1,
    "members": [{"agent": "claude", "alias": "a", "role": "writer", "listening": True}],
    "created_at": "2026-10-27T10:00:00.000Z",
}
MESSAGE = {
    "id": 7,
    "room": "r1",
    "sender": "human",
    "alias": None,
    "kind": "chat",
    "text": "hi",
    "reply_to": None,
    "created_at": "2026-10-27T10:00:01.000Z",
}
Handler = Callable[[httpx2.Request], httpx2.Response]


TOKEN = "tok-123"


def api_with(handler: Handler, token: str | None = TOKEN) -> RoomsApi:
    return RoomsApi("http://rooms.test", token, transport=httpx2.MockTransport(handler))


def json_response(status: int, body: object) -> httpx2.Response:
    return httpx2.Response(
        status, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )


@pytest.mark.anyio
async def test_server_error_bodies_become_api_errors() -> None:
    api = api_with(lambda _: json_response(404, {"error": "room_not_found", "message": "No room 'x'."}))
    with pytest.raises(ApiError) as caught:
        await api.history("x")
    assert (caught.value.status, caught.value.code, caught.value.message) == (
        404,
        "room_not_found",
        "No room 'x'.",
    )
    assert str(caught.value) == "No room 'x'."


@pytest.mark.anyio
async def test_401_is_recognised_as_unauthorized() -> None:
    api = api_with(
        lambda _: json_response(401, {"error": "unauthorized", "message": "Missing or invalid bearer token."})
    )
    with pytest.raises(ApiError) as caught:
        await api.list_rooms()
    assert caught.value.unauthorized
    assert not caught.value.offline


@pytest.mark.anyio
async def test_a_non_json_error_falls_back_to_the_status_code() -> None:
    api = api_with(lambda _: httpx2.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ApiError) as caught:
        await api.list_rooms()
    assert (caught.value.status, caught.value.code) == (502, "http_502")
    assert "502" in caught.value.message
    assert "<html>" not in caught.value.message


@pytest.mark.anyio
async def test_an_unreachable_server_is_reported_as_offline() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    api = api_with(refuse)
    with pytest.raises(ApiError) as caught:
        await api.list_rooms()
    assert caught.value.offline
    assert (caught.value.status, caught.value.code) == (0, "network")


@pytest.mark.anyio
async def test_a_timeout_is_reported_as_offline() -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(ApiError) as caught:
        await api_with(slow).list_rooms()
    assert caught.value.offline


@pytest.mark.anyio
async def test_a_malformed_success_body_is_a_bad_response() -> None:
    api = api_with(lambda _: json_response(200, {"unexpected": True}))
    with pytest.raises(ApiError) as caught:
        await api.list_rooms()
    assert caught.value.code == "bad_response"


@pytest.mark.anyio
async def test_without_a_token_nothing_is_sent() -> None:
    sent: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return json_response(200, [])

    api = api_with(record, token=None)
    with pytest.raises(ApiError) as caught:
        await api.list_rooms()
    assert caught.value.unauthorized
    assert sent == []


@pytest.mark.anyio
async def test_the_token_goes_in_the_authorization_header_only() -> None:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return json_response(200, [SUMMARY])

    api = api_with(record)
    rooms = await api.list_rooms()
    assert seen[0].headers["authorization"] == "Bearer tok-123"
    assert "tok-123" not in str(seen[0].url)
    assert rooms[0].members[0].listening is True
    assert "tok-123" not in repr(api)


@pytest.mark.anyio
async def test_requests_use_the_documented_paths_and_bodies() -> None:
    seen: list[tuple[str, str, bytes]] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.method, request.url.raw_path.decode(), request.content))
        if request.method == "DELETE":
            return httpx2.Response(204)
        if request.url.path.endswith("/messages") and request.method == "GET":
            return json_response(200, {"room": SUMMARY, "messages": [MESSAGE]})
        if request.url.path.endswith("/messages"):
            return json_response(201, MESSAGE)
        return json_response(201 if request.url.path == "/api/rooms" else 200, SUMMARY)

    api = api_with(record)
    await api.create_room("r1", "Topic")
    await api.history("r1", after=5, limit=50)
    await api.post("r1", "hi", "task")
    await api.set_status("r1", "paused")
    await api.delete_room("r1")
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/api/rooms"),
        ("GET", "/api/rooms/r1/messages?after=5&limit=50"),
        ("POST", "/api/rooms/r1/messages"),
        ("POST", "/api/rooms/r1/status"),
        ("DELETE", "/api/rooms/r1"),
    ]
    assert json.loads(seen[0][2]) == {"slug": "r1", "topic": "Topic"}
    assert json.loads(seen[2][2]) == {"text": "hi", "kind": "task"}
    assert json.loads(seen[3][2]) == {"status": "paused"}


@pytest.mark.anyio
async def test_room_names_are_path_escaped() -> None:
    seen: list[str] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path.decode())
        return httpx2.Response(204)

    await api_with(record).delete_room("a/b?c")
    assert seen == ["/api/rooms/a%2Fb%3Fc"]


@pytest.mark.anyio
async def test_everything_from_the_server_is_cleaned_of_terminal_controls() -> None:
    hostile = dict(MESSAGE, text="ok\x1b]0;pwned\x07 [bold red]x[/]", sender="ev\x1b[2Jil")
    api = api_with(lambda _: json_response(200, {"room": SUMMARY, "messages": [hostile]}))
    thread = await api.history("r1")
    assert thread.messages[0].text == "ok]0;pwned [bold red]x[/]"
    assert thread.messages[0].sender == "ev[2Jil"


@pytest.mark.anyio
async def test_server_error_messages_are_cleaned_too() -> None:
    api = api_with(lambda _: json_response(400, {"error": "invalid_input", "message": "bad\x1b[31m input"}))
    with pytest.raises(ApiError) as caught:
        await api.create_room("x", "y")
    assert "\x1b" not in caught.value.message


def test_the_token_can_be_replaced_and_dropped() -> None:
    api = api_with(lambda _: json_response(200, []), token=None)
    assert not api.has_token
    api.set_token("abc")
    assert api.has_token
    api.set_token(None)
    assert not api.has_token


@pytest.mark.anyio
async def test_probe_checks_a_candidate_token_without_adopting_it() -> None:
    seen: list[str] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers["authorization"])
        return json_response(200, [SUMMARY])

    api = api_with(record, token=None)
    rooms = await api.probe("candidate-token")
    assert [room.slug for room in rooms] == ["r1"]
    assert seen == ["Bearer candidate-token"]
    assert not api.has_token


@pytest.mark.anyio
async def test_probe_reports_a_rejected_candidate_as_unauthorized() -> None:
    api = api_with(lambda _: json_response(401, {"error": "unauthorized", "message": "no"}), token="current")
    with pytest.raises(ApiError) as caught:
        await api.probe("wrong")
    assert caught.value.unauthorized
    assert api.has_token
