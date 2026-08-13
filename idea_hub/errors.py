"""Application-level error types and codes."""


RATE_LIMITED = "RATE_LIMITED"
INTERNAL = "INTERNAL"
BAD_REQUEST = "BAD_REQUEST"
NOT_FOUND = "NOT_FOUND"
UNAUTHORIZED = "UNAUTHORIZED"


class AppError(Exception):
    """An expected application error with an HTTP status and stable code."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)
