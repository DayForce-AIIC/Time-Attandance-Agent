"""HTTP routes for the ShiftGuard manager workflow and demo controls."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from psycopg import Error as PostgresError

from app import repo
from app.assistant import answer_attendance_question, answer_workflow_attendance_question
from app.audit import verify_chain
from app.auth import Principal
from app.clock import Clock
from app.config import Settings
from app.db import init_db
from app.decisions import decide
from app.deps import (
    current_manager,
    current_principal,
    demo_manager,
    get_book,
    get_clock,
    get_conn,
    get_hr_review_service,
    get_llm,
    get_settings,
    get_workflow_repository,
    require_hr,
)
from app.detection import run_detection
from app.engine.shifts import build_shifts
from app.errors import Forbidden, NotFound
from app.hr_review import HRReviewService
from app.llm.base import LLMTimeout, LLMUnavailable
from app.llm.ollama import ollama_model_is_available
from app.logging_setup import trace_id_var
from app.metrics import calculate_metrics
from app.organization_policies import (
    resolve_organization_policy_context,
    standard_policy_row_to_output,
)
from app.pipeline import propose
from app.policies import FROZEN_RULE_IDS, PolicyBook, all_passages, get_rule, retrieve_policies
from app.schemas import (
    AssistantAnswer,
    AssistantQuestion,
    AuditEventOut,
    AuditVerifyOut,
    ConversationDeleteOut,
    ConversationDetailOut,
    ConversationMessageOut,
    ConversationSummaryOut,
    CurrentUserOut,
    DecisionIn,
    DecisionOut,
    DetectIn,
    DetectOut,
    EmployeeOut,
    ErrorOut,
    ExceptionDetail,
    ExceptionOut,
    ExceptionStatus,
    FaultMode,
    HealthOut,
    LLMModeIn,
    LLMModeOut,
    MetricsOut,
    OrganizationPolicyCreate,
    OrganizationPolicyOut,
    OrganizationPolicyUpdate,
    PolicyPassage,
    ProposalOut,
    PunchOut,
    ReadinessOut,
    ScheduleOut,
    Severity,
    TimecardOut,
)
from app.seed import seed
from app.workflow_repository import PostgresWorkflowRepository

router = APIRouter(prefix="/api/v1")


@router.get(
    "/me",
    response_model=CurrentUserOut,
    summary="Get the authenticated ShiftGuard user and role",
    tags=["auth"],
)
def current_user(principal: Principal = Depends(current_principal)) -> CurrentUserOut:
    """Return the backend-verified user profile used to authorize API requests."""
    return CurrentUserOut(
        user_id=principal.user_id,
        organization_id=(str(principal.organization_id) if principal.organization_id else None),
        role=principal.role,
        manager_ref=principal.manager_ref,
        email=principal.email,
    )


@router.post(
    "/assistant/query",
    response_model=AssistantAnswer,
    summary="Answer a manager's read-only attendance question",
    tags=["assistant"],
    responses={
        403: {"model": ErrorOut},
        429: {"model": ErrorOut},
        503: {"model": ErrorOut},
    },
)
def assistant_query(
    body: AssistantQuestion,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    llm=Depends(get_llm),
    book: PolicyBook = Depends(get_book),
    settings: Settings = Depends(get_settings),
    principal: Principal = Depends(current_principal),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> AssistantAnswer:
    """Answer attendance questions only when the configured Ollama model is available."""
    if settings.llm_provider != "ollama":
        raise HTTPException(
            status_code=503,
            detail="Ask ShiftGuard requires Ollama. Switch from mock mode and restart the backend.",
        )
    if not ollama_model_is_available(settings):
        request.app.state.operational_alerts.report_failure(
            "ollama",
            "configured_model_unavailable",
            trace_id=trace_id_var.get(),
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"The configured Ollama model '{settings.ollama_model}' is unavailable. "
                "Start Ollama and make sure the model is installed, then try again."
            ),
        )
    try:
        if settings.auth_mode == "supabase":
            if principal.role != "HR" or principal.organization_id is None:
                raise Forbidden("HR role required")
            if workflow_repository is None:
                raise HTTPException(status_code=503, detail="Conversation storage is unavailable")
            user_id = UUID(principal.user_id)
            history: list[dict[str, str]] = []
            if body.conversation_id is not None:
                if (
                    workflow_repository.get_conversation(
                        body.conversation_id, principal.organization_id, user_id
                    )
                    is None
                ):
                    raise NotFound("conversation was not found")
                prior_messages = workflow_repository.list_conversation_messages(
                    body.conversation_id,
                    principal.organization_id,
                    user_id,
                    limit=8,
                )
                history = [
                    {"role": str(message["role"]), "content": str(message["content"])[:1200]}
                    for message in prior_messages
                ]
            answer = answer_workflow_attendance_question(
                workflow_repository,
                principal.organization_id,
                body.question.strip(),
                llm,
                resolve_organization_policy_context(
                    workflow_repository, principal.organization_id, book
                ),
                history,
            )
            request.app.state.operational_alerts.report_success("ollama")
            conversation_id = workflow_repository.persist_conversation_exchange(
                organization_id=principal.organization_id,
                user_id=user_id,
                conversation_id=body.conversation_id,
                title=body.question.strip(),
                question=body.question.strip(),
                answer=answer.answer,
                model=answer.model,
                source_references=[source.model_dump(mode="json") for source in answer.sources],
            )
            return answer.model_copy(update={"conversation_id": conversation_id})
        answer = answer_attendance_question(conn, manager_id, body.question.strip(), llm, book)
        request.app.state.operational_alerts.report_success("ollama")
        return answer
    except (LLMTimeout, LLMUnavailable) as exc:
        request.app.state.operational_alerts.report_failure(
            "ollama",
            "llm_timeout" if isinstance(exc, LLMTimeout) else "llm_unavailable",
            trace_id=trace_id_var.get(),
        )
        raise HTTPException(
            status_code=503,
            detail="Ollama did not return a valid answer. Check the model and try again.",
        ) from exc


@router.get(
    "/assistant/conversations",
    response_model=list[ConversationSummaryOut],
    summary="List the signed-in HR user's saved conversations",
    tags=["assistant"],
)
def list_assistant_conversations(
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> list[ConversationSummaryOut]:
    """Return conversation summaries scoped to the authenticated HR user."""
    if settings.auth_mode == "demo":
        return []
    if principal.role != "HR" or principal.organization_id is None:
        raise Forbidden("HR role required")
    if workflow_repository is None:
        raise HTTPException(status_code=503, detail="Conversation storage is unavailable")
    rows = workflow_repository.list_conversations(
        principal.organization_id, UUID(principal.user_id), limit=100
    )
    return [ConversationSummaryOut.model_validate(row) for row in rows]


@router.get(
    "/assistant/conversations/{conversation_id}",
    response_model=ConversationDetailOut,
    summary="Reopen one of the signed-in HR user's conversations",
    tags=["assistant"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def assistant_conversation_detail(
    conversation_id: str,
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> ConversationDetailOut:
    """Load one private conversation and its persisted citations."""
    if settings.auth_mode == "demo" or principal.organization_id is None:
        raise NotFound("conversation was not found")
    if principal.role != "HR":
        raise Forbidden("HR role required")
    if workflow_repository is None:
        raise HTTPException(status_code=503, detail="Conversation storage is unavailable")
    parsed_id = _parse_uuid_id(conversation_id, "conversation")
    user_id = UUID(principal.user_id)
    conversation = workflow_repository.get_conversation(
        parsed_id, principal.organization_id, user_id
    )
    if conversation is None:
        raise NotFound("conversation was not found")
    messages = workflow_repository.list_conversation_messages(
        parsed_id, principal.organization_id, user_id, limit=200
    )
    return ConversationDetailOut(
        id=parsed_id,
        title=conversation.get("title"),
        created_at=conversation["created_at"],
        updated_at=conversation["updated_at"],
        messages=[ConversationMessageOut.model_validate(message) for message in messages],
    )


@router.delete(
    "/assistant/conversations/{conversation_id}",
    response_model=ConversationDeleteOut,
    summary="Delete one of the signed-in HR user's conversations",
    tags=["assistant"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def delete_assistant_conversation(
    conversation_id: str,
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> ConversationDeleteOut:
    """Delete a private conversation and its messages for retention control."""
    if settings.auth_mode == "demo" or principal.organization_id is None:
        raise NotFound("conversation was not found")
    if principal.role != "HR":
        raise Forbidden("HR role required")
    if workflow_repository is None:
        raise HTTPException(status_code=503, detail="Conversation storage is unavailable")
    deleted = workflow_repository.delete_conversation(
        _parse_uuid_id(conversation_id, "conversation"),
        principal.organization_id,
        UUID(principal.user_id),
    )
    if not deleted:
        raise NotFound("conversation was not found")
    return ConversationDeleteOut(deleted=True)


@router.get(
    "/health", response_model=HealthOut, summary="Check API and LLM availability", tags=["health"]
)
def health(
    settings: Settings = Depends(get_settings), clock: Clock = Depends(get_clock)
) -> HealthOut:
    """Report API health and whether the configured provider/model is available."""
    reachable = True if settings.llm_provider == "mock" else ollama_model_is_available(settings)
    return HealthOut(
        status="ok",
        provider=settings.llm_provider,
        model=_model_name(settings),
        llm_reachable=reachable,
        now=clock.now().strftime("%Y-%m-%dT%H:%M"),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessOut,
    summary="Check readiness of the database, worker, provider, and model",
    tags=["health"],
)
def readiness(
    settings: Settings = Depends(get_settings),
    conn: sqlite3.Connection = Depends(get_conn),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> JSONResponse:
    """Report operational dependencies for a platform readiness probe."""
    checks = {
        "database": False,
        "analysis_worker": settings.auth_mode == "demo",
        "attendance_provider": True,
        "llm": settings.llm_provider == "mock",
    }
    heartbeat_age: int | None = None
    try:
        if workflow_repository is not None:
            workflow_repository.connection.execute("SELECT 1").fetchone()
        else:
            conn.execute("SELECT 1").fetchone()
    except (PostgresError, sqlite3.Error):
        checks["database"] = False
    else:
        checks["database"] = True

    if (
        checks["database"]
        and workflow_repository is not None
        and settings.auth_organization_id is not None
    ):
        try:
            heartbeat = workflow_repository.get_latest_service_heartbeat(
                settings.auth_organization_id, "analysis_worker"
            )
        except PostgresError:
            checks["analysis_worker"] = False
        else:
            last_seen = heartbeat.get("last_seen_at") if heartbeat else None
            if isinstance(last_seen, datetime):
                aware_last_seen = (
                    last_seen.replace(tzinfo=timezone.utc)
                    if last_seen.tzinfo is None
                    else last_seen
                )
                heartbeat_age = max(
                    0, int((datetime.now(timezone.utc) - aware_last_seen).total_seconds())
                )
                checks["analysis_worker"] = (
                    heartbeat_age <= settings.analysis_worker_heartbeat_timeout_seconds
                )

    if settings.attendance_provider == "http":
        checks["attendance_provider"] = bool(
            settings.attendance_api_base_url
            and settings.attendance_api_token
            and settings.attendance_api_token.get_secret_value().strip()
        )
    if settings.llm_provider == "ollama":
        checks["llm"] = ollama_model_is_available(settings)

    ready = all(checks.values())
    result = ReadinessOut(
        status="ready" if ready else "not_ready",
        checks=checks,
        worker_heartbeat_age_seconds=heartbeat_age,
        now=datetime.now(timezone.utc),
    )
    return JSONResponse(
        status_code=200 if ready else 503,
        content=result.model_dump(mode="json"),
    )


@router.post(
    "/detect", response_model=DetectOut, summary="Run exception detection", tags=["exceptions"]
)
def detect(
    body: DetectIn,
    conn: sqlite3.Connection = Depends(get_conn),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
) -> DetectOut:
    """Run deterministic checks only for the authenticated user's permitted team."""
    if settings.auth_mode != "demo":
        raise HTTPException(
            status_code=409,
            detail="Attendance analysis runs automatically from company events in HR mode.",
        )
    as_of = datetime.strptime(body.as_of, "%Y-%m-%dT%H:%M") if body.as_of else clock.now()
    employees = repo.list_employees(conn)
    if principal.role != "HR" and principal.manager_ref is not None:
        employees = [
            employee
            for employee in employees
            if str(employee["manager_id"]) == principal.manager_ref
        ]
    allowed_ids = {str(employee["id"]) for employee in employees}
    selected_ids = (
        sorted(allowed_ids)
        if body.employee_ids is None
        else sorted(allowed_ids.intersection(body.employee_ids))
    )
    return run_detection(conn, as_of, book, selected_ids)


@router.get(
    "/exceptions",
    response_model=list[ExceptionOut],
    summary="List the current manager's exceptions",
    tags=["exceptions"],
    responses={403: {"model": ErrorOut}},
)
def list_exceptions(
    status: ExceptionStatus | None = None,
    severity: Severity | None = None,
    rule_id: str | None = None,
    employee_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    book: PolicyBook = Depends(get_book),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> list[ExceptionOut]:
    """Return filtered exceptions owned by the authenticated manager's team."""
    if review is not None:
        return review.list_cases(
            status=status,
            severity=severity,
            rule_id=rule_id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )
    rows = repo.list_exceptions_for_manager(
        conn,
        manager_id,
        status=status.value if status else None,
        severity=severity.value if severity else None,
        rule_id=rule_id,
        employee_id=employee_id,
        limit=limit,
        offset=offset,
    )
    return [_exception_out(row, book) for row in rows]


@router.get(
    "/exceptions/{exception_id}",
    response_model=ExceptionDetail,
    summary="Get exception evidence and review context",
    tags=["exceptions"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def exception_detail(
    exception_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> ExceptionDetail:
    """Return the exception, timecard, policies, latest proposal, and decision."""
    if review is not None:
        return review.get_case_detail(_parse_uuid_case_id(exception_id))
    try:
        numeric_exception_id = int(exception_id)
    except ValueError as exc:
        raise NotFound(f"exception {exception_id} was not found") from exc
    row = repo.get_exception(conn, numeric_exception_id)
    if row is None:
        raise NotFound(f"exception {exception_id} was not found")
    employee = _authorized_employee(conn, str(row["employee_id"]), manager_id)
    timecard = _timecard(
        conn,
        employee,
        clock.now().date(),
        book,
        week_date=date.fromisoformat(str(row["work_date"])),
    )
    proposal_row = repo.get_latest_proposal(conn, numeric_exception_id)
    decision_row = repo.get_latest_decision(conn, numeric_exception_id)
    return ExceptionDetail(
        exception=_exception_out(row, book, employee_name=str(employee["name"])),
        employee=EmployeeOut.model_validate(dict(employee)),
        timecard=timecard,
        policies=retrieve_policies(str(row["rule_id"]), str(employee["jurisdiction"])),
        latest_proposal=_proposal_out(proposal_row) if proposal_row else None,
        decision=_decision_out(decision_row) if decision_row else None,
    )


@router.post(
    "/exceptions/{exception_id}/propose",
    response_model=ProposalOut,
    summary="Generate a validated manager proposal",
    tags=["proposals"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
)
def create_proposal(
    exception_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    llm=Depends(get_llm),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    settings: Settings = Depends(get_settings),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> ProposalOut:
    """Generate, validate, and persist a proposal for manager review."""
    if review is not None:
        raise HTTPException(
            status_code=409,
            detail="Supabase cases are analyzed automatically by the durable analysis worker.",
        )
    try:
        numeric_exception_id = int(exception_id)
    except ValueError as exc:
        raise NotFound(f"exception {exception_id} was not found") from exc
    row = repo.get_exception(conn, numeric_exception_id)
    if row is None:
        raise NotFound(f"exception {exception_id} was not found")
    _authorized_employee(conn, str(row["employee_id"]), manager_id)
    return propose(conn, numeric_exception_id, llm, clock, book, settings)


@router.post(
    "/exceptions/{exception_id}/decision",
    response_model=DecisionOut,
    summary="Approve, edit, or reject a proposal",
    tags=["decisions"],
    responses={
        400: {"model": ErrorOut},
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        422: {"model": ErrorOut},
        429: {"model": ErrorOut},
    },
)
def create_decision(
    exception_id: str,
    body: DecisionIn,
    conn: sqlite3.Connection = Depends(get_conn),
    principal: Principal = Depends(current_principal),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> DecisionOut:
    """Record a manager decision and apply an approved correction."""
    if review is not None:
        try:
            actor_uuid = UUID(principal.user_id)
        except ValueError as exc:
            raise Forbidden("a verified HR user identity is required") from exc
        return review.decide(_parse_uuid_case_id(exception_id), body, actor_uuid)
    try:
        numeric_exception_id = int(exception_id)
    except ValueError as exc:
        raise NotFound(f"exception {exception_id} was not found") from exc
    manager_scope = principal.manager_ref or "*"
    actor_id = principal.manager_ref if principal.is_demo else principal.user_id
    return decide(
        conn,
        numeric_exception_id,
        manager_scope,
        body,
        clock,
        book,
        actor_id=actor_id,
        hr_scope=principal.role == "HR",
    )


@router.get(
    "/exceptions/{exception_id}/audit",
    response_model=list[AuditEventOut],
    summary="List audit events for an exception",
    tags=["audit"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def exception_audit(
    exception_id: str,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> list[AuditEventOut]:
    """Return the append-only audit trail for a manager-owned exception."""
    if review is not None:
        return review.audit_events(_parse_uuid_case_id(exception_id))
    try:
        numeric_exception_id = int(exception_id)
    except ValueError as exc:
        raise NotFound(f"exception {exception_id} was not found") from exc
    row = repo.get_exception(conn, numeric_exception_id)
    if row is None:
        raise NotFound(f"exception {exception_id} was not found")
    _authorized_employee(conn, str(row["employee_id"]), manager_id)
    return [_audit_out(event) for event in repo.list_audit_events(conn, numeric_exception_id)]


@router.post(
    "/decisions/{decision_id}/retry-writeback",
    response_model=DecisionOut,
    summary="Retry an approved company correction using its original idempotency key",
    tags=["decisions"],
    responses={
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
        429: {"model": ErrorOut},
    },
)
def retry_decision_writeback(
    decision_id: str,
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> DecisionOut:
    """Retry a pending/failed writeback without creating a second HR decision."""
    if review is None:
        raise NotFound("company writeback recovery is unavailable in demo mode")
    try:
        parsed_id = UUID(decision_id)
    except ValueError as exc:
        raise NotFound(f"decision {decision_id} was not found") from exc
    return review.retry_writeback(parsed_id)


@router.get(
    "/employees/{employee_id}/timecard",
    response_model=TimecardOut,
    summary="Get an employee's weekly timecard",
    tags=["timecards"],
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def employee_timecard(
    employee_id: str,
    week_start: date | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(current_manager),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    settings: Settings = Depends(get_settings),
) -> TimecardOut:
    """Return an employee's timecard for a selected or current Monday week."""
    if settings.auth_mode != "demo":
        raise NotFound("timecards are loaded from the company source while reviewing a case")
    employee = _authorized_employee(conn, employee_id, manager_id)
    return _timecard(conn, employee, clock.now().date(), book, week_start=week_start)


@router.get(
    "/organization/policies",
    response_model=list[OrganizationPolicyOut],
    summary="List organization policies and implemented rules",
    tags=["policies"],
)
def list_organization_policies(
    principal: Principal = Depends(require_hr),
    settings: Settings = Depends(get_settings),
    book: PolicyBook = Depends(get_book),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> list[OrganizationPolicyOut]:
    """Return the signed-in HR user's editable, tenant-scoped policy catalog."""
    organization_id, repository = _organization_policy_access(
        principal, settings, workflow_repository
    )
    resolve_organization_policy_context(repository, organization_id, book)
    return [
        OrganizationPolicyOut.model_validate(standard_policy_row_to_output(row))
        for row in repository.list_organization_policies(organization_id)
    ]


@router.post(
    "/organization/policies",
    response_model=OrganizationPolicyOut,
    status_code=201,
    summary="Add company-specific attendance guidance",
    tags=["policies"],
)
def create_organization_policy(
    body: OrganizationPolicyCreate,
    principal: Principal = Depends(require_hr),
    settings: Settings = Depends(get_settings),
    book: PolicyBook = Depends(get_book),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> OrganizationPolicyOut:
    """Add a company reference policy used in analysis and Ask ShiftGuard context."""
    organization_id, repository = _organization_policy_access(
        principal, settings, workflow_repository
    )
    resolve_organization_policy_context(repository, organization_id, book)
    invalid_rule_ids = set(body.applies_to_rule_ids) - set(FROZEN_RULE_IDS)
    if invalid_rule_ids:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown implemented rule IDs: {', '.join(sorted(invalid_rule_ids))}",
        )
    row = repository.create_company_policy(
        organization_id=organization_id,
        policy_id=f"COMPANY-{uuid4().hex[:12].upper()}",
        title=body.title.strip(),
        text=body.text.strip(),
        applies_to_rule_ids=list(dict.fromkeys(body.applies_to_rule_ids)),
        actor_id=UUID(principal.user_id),
    )
    return OrganizationPolicyOut.model_validate(standard_policy_row_to_output(row))


@router.patch(
    "/organization/policies/{policy_id}",
    response_model=OrganizationPolicyOut,
    summary="Edit an organization policy or implemented rule",
    tags=["policies"],
)
def update_organization_policy(
    policy_id: str,
    body: OrganizationPolicyUpdate,
    principal: Principal = Depends(require_hr),
    settings: Settings = Depends(get_settings),
    book: PolicyBook = Depends(get_book),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> OrganizationPolicyOut:
    """Edit policy wording, enablement, severity, or custom rule applicability."""
    organization_id, repository = _organization_policy_access(
        principal, settings, workflow_repository
    )
    resolve_organization_policy_context(repository, organization_id, book)
    current = next(
        (
            row
            for row in repository.list_organization_policies(organization_id)
            if str(row["policy_id"]) == policy_id
        ),
        None,
    )
    if current is None:
        raise NotFound(f"policy {policy_id} was not found")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one policy field must be changed")
    kind = str(current["kind"])
    if kind == "standard" and "applies_to_rule_ids" in updates:
        raise HTTPException(
            status_code=422,
            detail="Rule applicability is fixed for implemented rules.",
        )
    if kind == "custom" and "severity" in updates:
        raise HTTPException(
            status_code=422,
            detail="Company guidance does not create or change detector severity.",
        )
    if updates.get("title") is not None:
        updates["title"] = updates["title"].strip()
    if updates.get("text") is not None:
        updates["text"] = updates["text"].strip()
    if updates.get("severity") is not None:
        updates["severity"] = updates["severity"].value
    if "applies_to_rule_ids" in updates:
        if updates["applies_to_rule_ids"] is None:
            raise HTTPException(status_code=422, detail="Rule applicability cannot be null.")
        invalid_rule_ids = set(updates["applies_to_rule_ids"]) - set(FROZEN_RULE_IDS)
        if invalid_rule_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown implemented rule IDs: {', '.join(sorted(invalid_rule_ids))}",
            )
        updates["applies_to_rule_ids"] = list(dict.fromkeys(updates["applies_to_rule_ids"]))
    for field_name in ("title", "text", "severity"):
        if field_name in updates and updates[field_name] is None:
            raise HTTPException(status_code=422, detail=f"{field_name} cannot be null.")
    row = repository.update_organization_policy(
        organization_id=organization_id,
        policy_id=policy_id,
        updates=updates,
        actor_id=UUID(principal.user_id),
    )
    if row is None:
        raise NotFound(f"policy {policy_id} was not found")
    return OrganizationPolicyOut.model_validate(standard_policy_row_to_output(row))


@router.delete(
    "/organization/policies/{policy_id}",
    response_model=OrganizationPolicyOut,
    summary="Archive company attendance guidance",
    tags=["policies"],
)
def archive_organization_policy(
    policy_id: str,
    principal: Principal = Depends(require_hr),
    settings: Settings = Depends(get_settings),
    book: PolicyBook = Depends(get_book),
    workflow_repository: PostgresWorkflowRepository | None = Depends(get_workflow_repository),
) -> OrganizationPolicyOut:
    """Archive custom guidance while preserving its immutable history."""
    organization_id, repository = _organization_policy_access(
        principal, settings, workflow_repository
    )
    resolve_organization_policy_context(repository, organization_id, book)
    current = next(
        (
            row
            for row in repository.list_organization_policies(organization_id)
            if str(row["policy_id"]) == policy_id
        ),
        None,
    )
    if current is None:
        raise NotFound(f"policy {policy_id} was not found")
    if current["kind"] != "custom":
        raise HTTPException(status_code=409, detail="Implemented rules cannot be deleted.")
    row = repository.archive_company_policy(
        organization_id=organization_id,
        policy_id=policy_id,
        actor_id=UUID(principal.user_id),
    )
    if row is None:
        raise NotFound(f"policy {policy_id} was not found")
    return OrganizationPolicyOut.model_validate(standard_policy_row_to_output(row))


@router.get(
    "/policies",
    response_model=list[PolicyPassage],
    summary="List policy passages",
    tags=["policies"],
)
def list_policies() -> list[PolicyPassage]:
    """List the policy passages used by deterministic detection and proposals."""
    return all_passages()


@router.get(
    "/policies/{policy_id}",
    response_model=PolicyPassage,
    summary="Get one policy passage",
    tags=["policies"],
    responses={404: {"model": ErrorOut}},
)
def policy_detail(policy_id: str) -> PolicyPassage:
    """Return a policy passage by its frozen rule identifier."""
    try:
        rule = get_rule(policy_id)
    except KeyError as error:
        raise NotFound(f"policy {policy_id} was not found") from error
    return PolicyPassage(policy_id=rule.id, title=rule.title, text=rule.text)


@router.get(
    "/audit/verify",
    response_model=AuditVerifyOut,
    summary="Verify the audit hash chain",
    tags=["audit"],
)
def audit_verify(
    conn: sqlite3.Connection = Depends(get_conn),
    _principal: Principal = Depends(require_hr),
    review: HRReviewService | None = Depends(get_hr_review_service),
) -> AuditVerifyOut:
    """Recompute and report the integrity of the complete audit chain."""
    if review is not None:
        return review.verify_audit_chain()
    return verify_chain(conn)


@router.get(
    "/metrics",
    response_model=MetricsOut,
    summary="Get workflow and safety metrics",
    tags=["metrics"],
)
def metrics(
    conn: sqlite3.Connection = Depends(get_conn),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
    settings: Settings = Depends(get_settings),
    _principal: Principal = Depends(require_hr),
) -> MetricsOut:
    """Report proposal throughput, review outcomes, savings, and current risk."""
    if settings.auth_mode != "demo":
        raise NotFound("demo metrics are unavailable in authenticated HR mode")
    return calculate_metrics(conn, clock, book, settings)


@router.get(
    "/admin/llm-mode",
    response_model=LLMModeOut,
    summary="Read the demo LLM fault mode",
    tags=["admin"],
)
def get_llm_mode(
    request: Request,
    settings: Settings = Depends(get_settings),
    _manager: str = Depends(demo_manager),
) -> LLMModeOut:
    """Return the active simulation mode when demo controls are enabled."""
    _require_demo_controls(settings)
    return _mode_out(request, settings)


@router.post(
    "/admin/llm-mode",
    response_model=LLMModeOut,
    summary="Change the demo LLM fault mode",
    tags=["admin"],
)
def set_llm_mode(
    body: LLMModeIn,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(demo_manager),
    settings: Settings = Depends(get_settings),
) -> LLMModeOut:
    """Set the active fault mode and record the change in the audit trail."""
    _require_demo_controls(settings)
    previous = request.app.state.fault_mode
    from app.audit import FAULT_MODE_CHANGED, append_event

    append_event(
        conn,
        trace_id=f"admin-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        event_type=FAULT_MODE_CHANGED,
        payload={"manager_id": manager_id, "previous": previous.value, "mode": body.mode.value},
    )
    request.app.state.fault_mode = body.mode
    return _mode_out(request, settings)


@router.post(
    "/admin/reset-demo",
    response_model=DetectOut,
    summary="Reset and reseed the demo database",
    tags=["admin"],
)
def reset_demo(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    manager_id: str = Depends(demo_manager),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
    book: PolicyBook = Depends(get_book),
) -> DetectOut:
    """Recreate the demo data, reset fault injection, and run initial detection."""
    _require_demo_controls(settings)
    del manager_id
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TRIGGER IF EXISTS audit_no_update")
    conn.execute("DROP TRIGGER IF EXISTS audit_no_delete")
    for table in (
        "adjustments",
        "decisions",
        "proposals",
        "audit_log",
        "exceptions",
        "employee_notes",
        "punches",
        "schedules",
        "employees",
        "managers",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    seed(conn)
    request.app.state.fault_mode = FaultMode.NONE
    return run_detection(conn, clock.now(), book)


def _authorized_employee(
    conn: sqlite3.Connection, employee_id: str, manager_id: str
) -> sqlite3.Row:
    employee = repo.get_employee(conn, employee_id)
    if employee is None:
        raise NotFound(f"employee {employee_id} was not found")
    if manager_id != "*" and str(employee["manager_id"]) != manager_id:
        raise Forbidden("employee is not managed by this manager")
    return employee


def _parse_uuid_case_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise NotFound(f"exception {value} was not found") from exc


def _parse_uuid_id(value: str, entity: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise NotFound(f"{entity} was not found") from exc


def _exception_out(
    row: sqlite3.Row, book: PolicyBook, employee_name: str | None = None
) -> ExceptionOut:
    employee_name = employee_name or str(row["employee_name"])
    rule = get_rule(str(row["rule_id"]))
    return ExceptionOut(
        id=int(row["id"]),
        employee_id=str(row["employee_id"]),
        employee_name=employee_name,
        work_date=str(row["work_date"]),
        rule_id=rule.id,
        title=rule.title,
        severity=Severity(str(row["severity"])),
        status=ExceptionStatus(str(row["status"])),
        detected_at=str(row["detected_at"]),
        evidence=json.loads(str(row["evidence_json"])),
        constraints=json.loads(str(row["constraints_json"])),
    )


def _timecard(
    conn: sqlite3.Connection,
    employee: sqlite3.Row,
    as_of_date: date,
    book: PolicyBook,
    *,
    week_date: date | None = None,
    week_start: date | None = None,
) -> TimecardOut:
    reference = week_start or week_date or as_of_date
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    punches, schedules = repo.get_timekeeping_records(conn, str(employee["id"]), monday, sunday)
    shifts = build_shifts(punches, schedules, book.global_params.dup_window_minutes)
    worked = sum(shift.worked_minutes for shift in shifts.values() if shift.complete)
    return TimecardOut(
        employee=EmployeeOut.model_validate(dict(employee)),
        week_start=monday.isoformat(),
        punches=[
            PunchOut(
                id=punch.id,
                employee_id=punch.employee_id,
                work_date=punch.work_date.isoformat(),
                ts=punch.ts.strftime("%Y-%m-%dT%H:%M"),
                kind=punch.kind,
                source=_punch_source(conn, punch.id),
                voided=punch.voided,
            )
            for punch in punches
        ],
        schedules=[
            ScheduleOut(
                id=schedule.id,
                employee_id=schedule.employee_id,
                work_date=schedule.work_date.isoformat(),
                start=schedule.start.strftime("%Y-%m-%dT%H:%M"),
                end=schedule.end.strftime("%Y-%m-%dT%H:%M"),
                planned_break_minutes=schedule.planned_break_minutes,
            )
            for schedule in schedules
        ],
        weekly_worked_minutes=worked,
        weekly_threshold_minutes=book.jurisdictions[str(employee["jurisdiction"])].weekly_ot_hours
        * 60,
    )


def _punch_source(
    conn: sqlite3.Connection, punch_id: int
) -> Literal["device", "manual", "correction"]:
    row = conn.execute("SELECT source FROM punches WHERE id = ?", (punch_id,)).fetchone()
    source = str(row["source"]) if row else "device"
    if source == "manual":
        return "manual"
    if source == "correction":
        return "correction"
    return "device"


def _proposal_out(row: sqlite3.Row) -> ProposalOut:
    from app.schemas import LLMProposal, ProposalSource, ValidationResult

    return ProposalOut(
        id=int(row["id"]),
        exception_id=int(row["exception_id"]),
        trace_id=str(row["trace_id"]),
        source=ProposalSource(str(row["source"])),
        proposal=LLMProposal.model_validate_json(str(row["proposal_json"])),
        validation=ValidationResult.model_validate_json(str(row["validation_json"])),
        attempts=int(row["attempts"]),
        model=str(row["model"]),
        latency_ms=int(row["latency_ms"]),
        created_at=str(row["created_at"]),
    )


def _decision_out(row: sqlite3.Row) -> DecisionOut:
    from app.schemas import DecisionAction, ProposedCorrection

    correction_json = row["final_correction_json"]
    return DecisionOut(
        id=int(row["id"]),
        exception_id=int(row["exception_id"]),
        proposal_id=int(row["proposal_id"]) if row["proposal_id"] is not None else None,
        action=DecisionAction(str(row["action"])),
        manager_id=str(row["manager_id"]),
        note=str(row["note"]) if row["note"] is not None else None,
        final_correction=(
            ProposedCorrection.model_validate_json(str(correction_json))
            if correction_json is not None
            else None
        ),
        decided_at=str(row["decided_at"]),
        resulting_status=ExceptionStatus.RESOLVED
        if row["action"] != "reject"
        else ExceptionStatus.REJECTED,
    )


def _audit_out(row: sqlite3.Row) -> AuditEventOut:
    return AuditEventOut(
        id=int(row["id"]),
        trace_id=str(row["trace_id"]),
        exception_id=int(row["exception_id"]) if row["exception_id"] is not None else None,
        seq=int(row["seq"]),
        event_type=str(row["event_type"]),
        payload=json.loads(str(row["payload_json"])),
        prev_hash=str(row["prev_hash"]),
        hash=str(row["hash"]),
        created_at=str(row["created_at"]),
    )


def _require_demo_controls(settings: Settings) -> None:
    if not settings.enable_demo_controls:
        raise NotFound("demo controls are disabled")


def _organization_policy_access(
    principal: Principal,
    settings: Settings,
    repository: PostgresWorkflowRepository | None,
) -> tuple[UUID, PostgresWorkflowRepository]:
    """Enforce HR-authenticated organization storage for policy administration."""
    if settings.auth_mode != "supabase" or principal.organization_id is None:
        raise NotFound("organization policies are unavailable in demo mode")
    if principal.role != "HR":
        raise Forbidden("HR role required")
    if repository is None:
        raise HTTPException(status_code=503, detail="Organization policy storage is unavailable")
    return principal.organization_id, repository


def _mode_out(request: Request, settings: Settings) -> LLMModeOut:
    return LLMModeOut(
        mode=request.app.state.fault_mode,
        provider=settings.llm_provider,
        model=_model_name(settings),
    )


def _model_name(settings: Settings) -> str:
    return "mock" if settings.llm_provider == "mock" else settings.ollama_model
