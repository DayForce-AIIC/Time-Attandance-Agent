"""FastAPI dependencies for request-scoped resources and manager identity."""

import sqlite3
from collections.abc import Generator

from fastapi import Header, Request

from app import repo
from app.clock import Clock
from app.config import Settings
from app.db import get_conn as open_connection
from app.errors import Forbidden, NotFound
from app.llm.base import LLMClient
from app.policies import PolicyBook


def get_settings(request: Request) -> Settings:
    """Return settings owned by the application instance."""
    return request.app.state.settings


def get_conn(request: Request) -> Generator[sqlite3.Connection, None, None]:
    """Open and close one SQLite connection for the current request."""
    conn = open_connection(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_manager(
    request: Request,
    manager_id: str | None = Header(default=None, alias="X-Manager-Id"),
) -> str:
    """Validate the manager header, defaulting to the configured demo manager."""
    identifier = manager_id or request.app.state.settings.default_manager_id
    conn = open_connection(request.app.state.db_path)
    try:
        if repo.get_manager(conn, identifier) is None:
            raise Forbidden("unknown manager")
    finally:
        conn.close()
    return identifier


def demo_manager(
    request: Request,
    manager_id: str | None = Header(default=None, alias="X-Manager-Id"),
) -> str:
    """Hide demo-only operations when controls are disabled, then validate manager."""
    if not request.app.state.settings.enable_demo_controls:
        raise NotFound("demo controls are disabled")
    return current_manager(request, manager_id)


def get_clock(request: Request) -> Clock:
    """Build a business clock from this app's settings."""
    return Clock(request.app.state.settings)


def get_llm(request: Request) -> LLMClient:
    """Return the configured provider wrapped by the fault injector."""
    return request.app.state.llm


def get_book(request: Request) -> PolicyBook:
    """Return the validated policy book configured at application startup."""
    return request.app.state.book
