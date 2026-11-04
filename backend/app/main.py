"""FastAPI application factory and process-wide default application."""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router
from app.clock import Clock
from app.config import Settings, get_settings
from app.db import get_conn, init_db
from app.errors import BadRequest, Forbidden, NotFound, StateConflict, ValidationFailed
from app.llm.factory import make_llm_client
from app.logging_setup import configure_logging, trace_id_var
from app.policies import load_policies
from app.schemas import ErrorOut, FaultMode
from app.seed import seed
from app.detection import run_detection


logger = logging.getLogger("shiftguard.access")
DESCRIPTION = (
    "ShiftGuard helps managers review timecard exceptions with evidence-based suggestions, "
    "policy context, and explicit approval before any payroll record changes.\n\n"
    "The system pairs deterministic timekeeping rules with a constrained LLM proposal layer. "
    "Every proposal is checked, simulated, audited, and routed to a manager; the demo controls "
    "make provider faults visible and reproducible."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create a fully configured ShiftGuard API application."""
    runtime_settings = settings or get_settings()
    configure_logging()
    book = load_policies()
    db_path: str | Path = runtime_settings.db_path
    if str(db_path) == ":memory:":
        db_path = f"file:shiftguard-{uuid.uuid4().hex}?mode=memory&cache=shared"
        database_existed = False
    else:
        database_existed = Path(db_path).expanduser().exists()
    conn = get_conn(db_path)
    try:
        init_db(conn)
        if not database_existed and runtime_settings.auto_seed:
            seed(conn)
            run_detection(conn, Clock(runtime_settings).now(), book)
    except Exception:
        conn.close()
        raise

    application = FastAPI(
        title="ShiftGuard API",
        version="1.0.0",
        description=DESCRIPTION,
    )
    application.state.settings = runtime_settings
    application.state.db_path = db_path
    if str(db_path).startswith("file:") and "mode=memory" in str(db_path):
        application.state.db_keeper = conn
    else:
        conn.close()
    application.state.book = book
    application.state.fault_mode = FaultMode.NONE
    application.state.llm = make_llm_client(
        runtime_settings, lambda: application.state.fault_mode
    )
    application.include_router(router)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        context_token = trace_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(context_token)
        duration_ms = round((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request completed",
            extra={
                "trace_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @application.exception_handler(StateConflict)
    async def state_conflict_handler(_request: Request, exc: StateConflict) -> JSONResponse:
        return _error_response(409, exc.detail, exc.code)

    @application.exception_handler(Forbidden)
    async def forbidden_handler(_request: Request, exc: Forbidden) -> JSONResponse:
        return _error_response(403, exc.detail, exc.code)

    @application.exception_handler(NotFound)
    async def not_found_handler(_request: Request, exc: NotFound) -> JSONResponse:
        return _error_response(404, exc.detail, exc.code)

    @application.exception_handler(BadRequest)
    async def bad_request_handler(_request: Request, exc: BadRequest) -> JSONResponse:
        return _error_response(400, exc.detail, exc.code)

    @application.exception_handler(ValidationFailed)
    async def validation_failed_handler(_request: Request, exc: ValidationFailed) -> JSONResponse:
        return _error_response(422, exc.detail, exc.code, checks=exc.checks)

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del exc
        is_decision = request.url.path.endswith("/decision")
        status_code = 400 if is_decision else 422
        code = "bad_request" if is_decision else "request_validation_failed"
        return _error_response(status_code, "request validation failed", code)

    @application.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        code = {403: "forbidden", 404: "not_found"}.get(exc.status_code, "http_error")
        return _error_response(exc.status_code, str(exc.detail), code)

    return application


def _error_response(
    status_code: int,
    detail: str,
    code: str,
    *,
    checks: list | None = None,
) -> JSONResponse:
    body = ErrorOut(detail=detail, code=code, checks=checks)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


app = create_app()
