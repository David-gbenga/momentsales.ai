"""RFC 9457 problem+json errors (ADR-0009)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    """Raise anywhere in a request to return a problem+json response.

    `type` is a short stable code (e.g. "plan_limit_reached"); clients switch on it.
    """

    def __init__(
        self,
        status: int,
        type_: str,
        title: str,
        detail: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail
        self.extra = extra

    def to_body(self, instance: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type, "title": self.title, "status": self.status}
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        body.update(self.extra)
        return body


def not_found(what: str = "resource") -> ProblemError:
    return ProblemError(404, "not_found", "Not found", f"The {what} does not exist.")


def forbidden(action: str) -> ProblemError:
    return ProblemError(403, "forbidden", "Not allowed", "Your role cannot do this.", action=action)


def unauthorized(detail: str = "Sign in to continue.") -> ProblemError:
    return ProblemError(401, "unauthorized", "Unauthorized", detail)


def _problem(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem_handler(request: Request, exc: ProblemError) -> JSONResponse:
        return _problem(exc.status, exc.to_body(request.url.path))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        body = {
            "type": "validation_error",
            "title": "Invalid request",
            "status": 422,
            "instance": request.url.path,
            "errors": errors,
        }
        return _problem(422, body)

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = {
            "type": "http_error",
            "title": str(exc.detail),
            "status": exc.status_code,
            "instance": request.url.path,
        }
        return _problem(exc.status_code, body, getattr(exc, "headers", None))
