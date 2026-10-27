import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_PORT = re.compile(r"[0-9]{1,5}")
_MISDIRECTED = {"error": "misdirected", "message": "This Host header is not allowed for this server."}


class HostGuardMiddleware:
    """Rejects every HTTP request whose Host header is not allow-listed (DNS-rebinding defence).

    Patterns are exact (`host:8765`) or `host:*`, which accepts any numeric port.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: tuple[str, ...]) -> None:
        self.app = app
        patterns = [pattern.lower() for pattern in allowed_hosts]
        self._exact = frozenset(patterns)
        self._any_port = frozenset(pattern[:-2] for pattern in patterns if pattern.endswith(":*"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._allowed(scope["headers"]):
            await JSONResponse(_MISDIRECTED, status_code=421)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _allowed(self, headers: list[tuple[bytes, bytes]]) -> bool:
        hosts = [value for name, value in headers if name == b"host"]
        if len(hosts) != 1:
            return False
        host = hosts[0].decode("latin-1").lower()
        if host in self._exact:
            return True
        name, separator, port = host.rpartition(":")
        return bool(separator) and name in self._any_port and _PORT.fullmatch(port) is not None
