import functools
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from agent_rooms.domain.errors import DomainError

STATUS_BY_CODE = {
    "invalid_input": 400,
    "room_not_found": 404,
    "room_exists": 409,
    "room_not_active": 409,
    "room_not_closed": 409,
    "not_a_member": 409,
    "kind_not_allowed": 403,
    "guard_tripped": 409,
}

Endpoint = Callable[[Request], Awaitable[Response]]


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": code, "message": message}, status_code=status)


def domain_error_response(error: DomainError) -> JSONResponse:
    return error_response(STATUS_BY_CODE.get(error.code, 500), error.code, str(error))


def api_errors(endpoint: Endpoint) -> Endpoint:
    @functools.wraps(endpoint)
    async def wrapper(request: Request) -> Response:
        try:
            return await endpoint(request)
        except HttpError as error:
            return error_response(error.status, error.code, str(error))
        except DomainError as error:
            return domain_error_response(error)

    return wrapper
