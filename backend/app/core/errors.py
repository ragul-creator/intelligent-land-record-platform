"""Stable, non-sensitive application error responses."""

from fastapi import HTTPException, status


class ApiError(HTTPException):
    """An expected application error with a stable public code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code


def not_found(code: str = "NOT_FOUND", message: str = "The requested resource was not found.") -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, code, message)


def forbidden(code: str = "FORBIDDEN", message: str = "You are not allowed to perform this action.") -> ApiError:
    return ApiError(status.HTTP_403_FORBIDDEN, code, message)
