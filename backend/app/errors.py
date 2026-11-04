"""Application-level errors shared by service and API layers."""

from app.schemas import CheckResult


class StateConflict(Exception):
    """The requested operation conflicts with the record's current state."""

    code = "state_conflict"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(Exception):
    """The requested application record does not exist."""

    code = "not_found"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class Forbidden(Exception):
    """The current user is not allowed to perform the requested operation."""

    code = "forbidden"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class BadRequest(Exception):
    """The request is invalid for the requested operation."""

    def __init__(self, detail: str, code: str = "bad_request") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


class ValidationFailed(Exception):
    """A correction failed automated safety validation."""

    code = "validation_failed"

    def __init__(self, checks: list[CheckResult]) -> None:
        super().__init__("correction failed validation")
        self.detail = "correction failed validation"
        self.checks = checks
