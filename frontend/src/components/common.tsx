import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Severity } from "../api/schemas";

const SEVERITY_MARK: Record<Severity, string> = {
  high: "▲",
  medium: "●",
  low: "○",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`severity-badge severity-${severity}`}>
      <span aria-hidden="true">{SEVERITY_MARK[severity]}</span> {severity}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const label = status.replaceAll("_", " ").toLowerCase();
  return <span className={`status-badge status-${status.toLowerCase()}`}>{label}</span>;
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  const detail = apiError?.detail ?? (error instanceof Error ? error.message : "Something went wrong.");
  const title = apiError?.code === "response_validation_error"
    ? "Response format problem"
    : apiError?.status === 403
      ? "Access denied"
      : apiError?.status === 404
        ? "Record not found"
        : apiError?.status === 409
          ? "This record changed"
          : apiError?.status === 0
            ? "Backend connection problem"
            : "We couldn’t complete that request";

  return (
    <section className="error-banner" role="alert" aria-live="assertive">
      <span className="error-icon" aria-hidden="true">!</span>
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
        {apiError?.checks?.length ? (
          <ul className="inline-check-errors">
            {apiError.checks.filter((check) => !check.passed).map((check) => (
              <li key={`${check.id}-${check.name}`}><b>{check.id} · {check.name}:</b> {check.detail}</li>
            ))}
          </ul>
        ) : null}
      </div>
      {onRetry ? <button type="button" className="button button-quiet" onClick={onRetry}>Retry</button> : null}
    </section>
  );
}

export function LoadingState({ label = "Loading review data…" }: { label?: string }) {
  return <div className="loading-state" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function PageHeading({ eyebrow, title, children }: { eyebrow?: string; title: string; children?: ReactNode }) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
      </div>
      {children ? <div className="page-heading-actions">{children}</div> : null}
    </div>
  );
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden="true">✓</div>
      <h2>{title}</h2>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

export function formatDate(value: string): string {
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "2-digit", year: "numeric" }).format(date);
}

export function formatDateShort(value: string): string {
  const date = new Date(`${value.slice(0, 10)}T12:00:00`);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "2-digit" }).format(date);
}

export function formatTime(value: string): string {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(date);
}

export function formatHours(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return `${hours}h${remaining ? ` ${remaining}m` : ""}`;
}
