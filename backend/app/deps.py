"""FastAPI dependencies for request-scoped resources and manager identity."""

import sqlite3
from collections.abc import Generator
from typing import cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import Error as PostgresError

from app import repo
from app.auth import AuthRole, Principal, verify_supabase_access_token
from app.clock import Clock
from app.config import Settings
from app.db import get_conn as open_connection
from app.errors import Forbidden, NotFound
from app.hr_review import HRReviewService
from app.integrations.provider import AttendanceProvider
from app.llm.base import LLMClient
from app.organization_policies import resolve_organization_policy_context
from app.policies import PolicyBook
from app.workflow_repository import PostgresWorkflowRepository

_bearer = HTTPBearer(auto_error=False)


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


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Resolve demo identity locally or verify Supabase identity and profile server-side."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "demo":
        # This selector is honored only in local demo mode. Supabase mode never
        # accepts a client-selected manager identity.
        identifier = request.headers.get("X-Manager-Id") or settings.default_manager_id
        conn = open_connection(request.app.state.db_path)
        try:
            if repo.get_manager(conn, identifier) is None:
                raise Forbidden("unknown demo manager")
        finally:
            conn.close()
        return Principal(
            user_id=f"demo:{identifier}",
            organization_id=None,
            role="manager",
            manager_ref=identifier,
            is_demo=True,
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer access token required")
    claims = verify_supabase_access_token(credentials.credentials, settings)
    user_id = UUID(str(claims["sub"]))
    try:
        workflow_repo = PostgresWorkflowRepository.from_settings(settings)
        try:
            profile = workflow_repo.get_user_profile(user_id)
        finally:
            workflow_repo.close()
    except PostgresError as exc:
        raise HTTPException(
            status_code=503,
            detail="The authorization profile service is temporarily unavailable",
        ) from exc
    if profile is None:
        raise Forbidden("the signed-in user has no ShiftGuard organization profile")

    organization_id = UUID(str(profile["organization_id"]))
    if settings.auth_organization_id is None:
        raise HTTPException(status_code=503, detail="AUTH_ORGANIZATION_ID is not configured")
    if organization_id != settings.auth_organization_id:
        raise Forbidden("user does not belong to this ShiftGuard deployment")

    role = str(profile["role"])
    if role not in {"HR", "manager", "integration_service"}:
        raise Forbidden("user has an unsupported ShiftGuard role")
    if role == "integration_service":
        raise Forbidden("integration service identities cannot use the HR interface")

    manager_ref = str(profile["manager_ref"]) if profile["manager_ref"] is not None else None
    if role == "manager":
        if manager_ref is None:
            raise Forbidden("manager profile is missing its manager reference")
        conn = open_connection(request.app.state.db_path)
        try:
            if repo.get_manager(conn, manager_ref) is None:
                raise Forbidden("manager profile is not mapped to an available team")
        finally:
            conn.close()

    return Principal(
        user_id=str(user_id),
        organization_id=organization_id,
        role=cast(AuthRole, role),
        manager_ref=manager_ref,
        email=str(claims["email"]) if claims.get("email") else None,
    )


def current_manager(principal: Principal = Depends(current_principal)) -> str:
    """Return server-derived manager scope; HR receives all-team scope."""
    if principal.role == "HR":
        return "*"
    if principal.manager_ref is None:
        raise Forbidden("signed-in user has no manager scope")
    return principal.manager_ref


def demo_manager(
    request: Request,
    principal: Principal = Depends(current_principal),
) -> str:
    """Keep demo controls out of authenticated Supabase mode."""
    if request.app.state.settings.auth_mode != "demo":
        raise NotFound("demo controls are unavailable in authenticated mode")
    if not request.app.state.settings.enable_demo_controls:
        raise NotFound("demo controls are disabled")
    return principal.manager_ref or request.app.state.settings.default_manager_id


def require_hr(principal: Principal = Depends(current_principal)) -> Principal:
    """Require the HR role for organization-wide reporting and administration."""
    if not principal.is_demo and principal.role != "HR":
        raise Forbidden("HR role required")
    return principal


def get_clock(request: Request) -> Clock:
    """Build a business clock from this app's settings."""
    return Clock(request.app.state.settings)


def get_llm(request: Request) -> LLMClient:
    """Return the configured provider wrapped by the fault injector."""
    return request.app.state.llm


def get_book(request: Request) -> PolicyBook:
    """Return the validated policy book configured at application startup."""
    return request.app.state.book


def get_attendance_provider(request: Request) -> AttendanceProvider:
    """Return the company attendance connector configured at application startup."""
    return request.app.state.attendance_provider


def get_workflow_repository(
    request: Request,
) -> Generator[PostgresWorkflowRepository | None, None, None]:
    """Open and close the durable workflow store for an authenticated request."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode != "supabase":
        yield None
        return

    repository: PostgresWorkflowRepository | None = None
    try:
        repository = PostgresWorkflowRepository.from_settings(settings)
        yield repository
    except PostgresError as exc:
        raise HTTPException(
            status_code=503,
            detail="The ShiftGuard workflow database is temporarily unavailable",
        ) from exc
    finally:
        if repository is not None:
            repository.close()


def get_hr_review_service(
    request: Request,
    principal: Principal = Depends(require_hr),
    repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
    provider: AttendanceProvider = Depends(get_attendance_provider),
    book: PolicyBook = Depends(get_book),
) -> HRReviewService | None:
    """Build HR review operations from this request's authenticated organization."""
    if repository is None:
        return None
    if principal.organization_id is None:
        raise Forbidden("HR review requires an organization profile")
    policy_context = resolve_organization_policy_context(
        repository, principal.organization_id, book
    )
    return HRReviewService(
        repository,
        provider,
        principal.organization_id,
        policy_context.book,
        request.app.state.operational_alerts,
        policy_context,
    )
