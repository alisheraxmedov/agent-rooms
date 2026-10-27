from typing import ClassVar


class DomainError(Exception):
    code: ClassVar[str]


class InvalidInput(DomainError):
    code = "invalid_input"


class RoomNotFound(DomainError):
    code = "room_not_found"


class RoomAlreadyExists(DomainError):
    code = "room_exists"


class RoomNotActive(DomainError):
    code = "room_not_active"


class NotAMember(DomainError):
    code = "not_a_member"


class KindNotAllowed(DomainError):
    code = "kind_not_allowed"


class GuardTripped(DomainError):
    code = "guard_tripped"
