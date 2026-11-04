"""Run the durable attendance analysis worker outside the FastAPI process."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from uuid import uuid4

from app.analysis_worker import AttendanceAnalysisWorker
from app.config import get_settings
from app.integrations.factory import make_attendance_provider
from app.llm.factory import make_llm_client
from app.logging_setup import configure_logging
from app.policies import POLICY_BOOK
from app.schemas import FaultMode
from app.workflow_repository import PostgresWorkflowRepository


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Process durable attendance analysis jobs from Supabase PostgreSQL."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one available job, then exit",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="exit after processing this many jobs (stops early when the queue is empty)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=settings.analysis_worker_poll_seconds,
        help="delay between empty queue polls",
    )
    args = parser.parse_args()
    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("--max-jobs must be at least 1")
    if args.poll_seconds <= 0 or args.poll_seconds > 60:
        parser.error("--poll-seconds must be greater than 0 and at most 60")
    if settings.auth_mode != "supabase":
        raise SystemExit("Set AUTH_MODE=supabase before running the analysis worker.")
    if settings.auth_organization_id is None or settings.attendance_integration_id is None:
        raise SystemExit(
            "Set AUTH_ORGANIZATION_ID and ATTENDANCE_INTEGRATION_ID before running the worker."
        )

    configure_logging()
    provider = make_attendance_provider(settings)
    repository: PostgresWorkflowRepository | None = None
    processed = 0
    instance_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    last_heartbeat = 0.0
    idle_logged = False
    try:
        repository = PostgresWorkflowRepository.from_settings(settings)
        integration = repository.get_integration(
            settings.attendance_integration_id,
            settings.auth_organization_id,
        )
        if integration is None or integration["status"] != "active":
            raise SystemExit("The configured attendance integration is missing or inactive.")
        llm = make_llm_client(settings, lambda: FaultMode.NONE)
        worker = AttendanceAnalysisWorker(repository, provider, llm, settings, POLICY_BOOK)
        logger = logging.getLogger(__name__)
        log_context = {
            "worker_instance_id": instance_id,
            "organization_id": str(settings.auth_organization_id),
        }
        logger.info("analysis worker started", extra=log_context)

        while True:
            monotonic_now = time.monotonic()
            if monotonic_now - last_heartbeat >= settings.analysis_worker_heartbeat_seconds:
                repository.record_service_heartbeat(
                    settings.auth_organization_id,
                    "analysis_worker",
                    instance_id,
                )
                last_heartbeat = monotonic_now
            claimed = worker.process_one()
            if claimed:
                processed += 1
                idle_logged = False
                if args.once or (args.max_jobs is not None and processed >= args.max_jobs):
                    break
                continue
            if args.once or args.max_jobs is not None:
                break
            if not idle_logged:
                logger.info(
                    "analysis worker waiting for queued attendance events",
                    extra=log_context,
                )
                idle_logged = True
            time.sleep(args.poll_seconds)

        logger.info(
            "analysis worker stopped",
            extra={
                "processed_jobs": processed,
                **log_context,
            },
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info(
            "analysis worker stopped by operator",
            extra={
                "processed_jobs": processed,
                "worker_instance_id": instance_id,
                "organization_id": str(settings.auth_organization_id),
            },
        )
    finally:
        provider.close()
        if repository is not None:
            try:
                repository.remove_service_heartbeat(
                    settings.auth_organization_id,
                    "analysis_worker",
                    instance_id,
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "analysis worker heartbeat cleanup failed",
                    extra={"worker_instance_id": instance_id},
                )
            repository.close()


if __name__ == "__main__":
    main()
