from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx2

from agent_rooms.tui.text import clean

DEFAULT_TIMEOUT_S = 10.0
HISTORY_PAGE = 200


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    @property
    def unauthorized(self) -> bool:
        return self.status == 401

    @property
    def offline(self) -> bool:
        return self.status == 0 and self.code == "network"


@dataclass(frozen=True, slots=True)
class Member:
    agent: str
    alias: str | None
    role: str
    listening: bool

    @property
    def name(self) -> str:
        return f"{self.agent}/{self.alias}" if self.alias else self.agent


@dataclass(frozen=True, slots=True)
class Summary:
    slug: str
    topic: str
    status: str
    max_messages: int
    message_count: int
    members: tuple[Member, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    room: str
    sender: str
    alias: str | None
    kind: str
    text: str
    reply_to: int | None
    created_at: str

    @property
    def author(self) -> str:
        return f"{self.sender}/{self.alias}" if self.alias else self.sender


@dataclass(frozen=True, slots=True)
class Thread:
    room: Summary
    messages: list[Message]


def _bad_response() -> ApiError:
    return ApiError(0, "bad_response", "The server sent a response this client does not understand.")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    return value


def _str(data: Mapping[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return clean(value)


def _opt_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(key)
    return clean(value) or None


def _int(data: Mapping[str, Any], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(key)
    return value


def _opt_int(data: Mapping[str, Any], key: str) -> int | None:
    return None if data.get(key) is None else _int(data, key)


def _bool(data: Mapping[str, Any], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(key)
    return value


def _member(raw: Any) -> Member:
    data = _mapping(raw)
    return Member(_str(data, "agent"), _opt_str(data, "alias"), _str(data, "role"), _bool(data, "listening"))


def _summary(raw: Any) -> Summary:
    data = _mapping(raw)
    members = data["members"]
    if not isinstance(members, list):
        raise TypeError("members")
    return Summary(
        _str(data, "slug"),
        _str(data, "topic"),
        _str(data, "status"),
        _int(data, "max_messages"),
        _int(data, "message_count"),
        tuple(_member(member) for member in members),
        _str(data, "created_at"),
    )


def _message(raw: Any) -> Message:
    data = _mapping(raw)
    return Message(
        _int(data, "id"),
        _str(data, "room"),
        _str(data, "sender"),
        _opt_str(data, "alias"),
        _str(data, "kind"),
        _str(data, "text"),
        _opt_int(data, "reply_to"),
        _str(data, "created_at"),
    )


def _thread(raw: Any) -> Thread:
    data = _mapping(raw)
    messages = data["messages"]
    if not isinstance(messages, list):
        raise TypeError("messages")
    return Thread(_summary(data["room"]), [_message(message) for message in messages])


def _parse[T](parse: Callable[[Any], T], raw: Any) -> T:
    try:
        return parse(raw)
    except (KeyError, TypeError, ValueError):
        raise _bad_response() from None


def _error_from(response: httpx2.Response) -> ApiError:
    code, message = f"http_{response.status_code}", f"Request failed (HTTP {response.status_code})."
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        if isinstance(body.get("error"), str):
            code = clean(body["error"])
        if isinstance(body.get("message"), str):
            message = clean(body["message"])
    return ApiError(response.status_code, code, message)


class RoomsApi:
    """Async client for the human REST API (spec 7.3). The token is only ever sent as a bearer header."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token or None
        self._client = httpx2.AsyncClient(base_url=self._base_url, transport=transport, timeout=timeout)

    def __repr__(self) -> str:
        return f"RoomsApi(base_url={self._base_url!r}, signed_in={self.has_token})"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def set_token(self, token: str | None) -> None:
        self._token = token or None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _send(
        self, method: str, path: str, *, token: str | None = None, **kwargs: Any
    ) -> httpx2.Response:
        bearer = token or self._token
        if bearer is None:
            raise ApiError(401, "unauthorized", "Not signed in. Type /login.")
        try:
            response = await self._client.request(
                method, path, headers={"Authorization": f"Bearer {bearer}"}, **kwargs
            )
        except httpx2.HTTPError:
            raise ApiError(0, "network", "Cannot reach the server.") from None
        if response.status_code >= 400:
            raise _error_from(response)
        return response

    async def _json(self, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
        response = await self._send(method, path, token=token, **kwargs)
        try:
            return response.json()
        except ValueError:
            raise _bad_response() from None

    async def list_rooms(self) -> list[Summary]:
        return await self._rooms()

    async def probe(self, token: str) -> list[Summary]:
        """Check a candidate token against the server without adopting it."""
        return await self._rooms(token)

    async def _rooms(self, token: str | None = None) -> list[Summary]:
        raw = await self._json("GET", "/api/rooms", token=token)
        if not isinstance(raw, list):
            raise _bad_response()
        return _parse(lambda items: [_summary(item) for item in items], raw)

    async def create_room(self, slug: str, topic: str) -> Summary:
        raw = await self._json("POST", "/api/rooms", json={"slug": slug, "topic": topic})
        return _parse(_summary, raw)

    async def history(self, room: str, after: int = 0, limit: int = HISTORY_PAGE) -> Thread:
        path = f"/api/rooms/{quote(room, safe='')}/messages"
        raw = await self._json("GET", path, params={"after": after, "limit": limit})
        return _parse(_thread, raw)

    async def post(self, room: str, text: str, kind: str = "chat") -> Message:
        path = f"/api/rooms/{quote(room, safe='')}/messages"
        raw = await self._json("POST", path, json={"text": text, "kind": kind})
        return _parse(_message, raw)

    async def set_status(self, room: str, status: str) -> Summary:
        raw = await self._json("POST", f"/api/rooms/{quote(room, safe='')}/status", json={"status": status})
        return _parse(_summary, raw)

    async def set_role(self, room: str, agent: str, alias: str, role: str) -> Summary:
        path = f"/api/rooms/{quote(room, safe='')}/members/role"
        raw = await self._json("POST", path, json={"agent": agent, "alias": alias, "role": role})
        return _parse(_summary, raw)

    async def delete_room(self, room: str) -> None:
        await self._send("DELETE", f"/api/rooms/{quote(room, safe='')}")
