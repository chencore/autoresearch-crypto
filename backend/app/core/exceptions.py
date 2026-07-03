import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _status_phrase(status_code: int) -> str:
    from http import HTTPStatus

    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        return f"status_{status_code}"


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(_status_phrase(exc.status_code), str(exc.detail), exc.status_code)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception while processing %s %s", request.method, request.url.path)
    return error_response("internal_error", "内部错误", 500)
