import { z } from "zod";
import { errorOutSchema, type CheckResult } from "./schemas";
import { getSupabaseAccessToken, supabaseAuthEnabled } from "../auth/client";

const MANAGER_STORAGE_KEY = "shiftguard.managerId";
const DEFAULT_MANAGER_ID = "m-001";
let activeManagerId = readStoredManager();

function readStoredManager(): string {
  try {
    return window.sessionStorage.getItem(MANAGER_STORAGE_KEY) ?? DEFAULT_MANAGER_ID;
  } catch {
    return DEFAULT_MANAGER_ID;
  }
}

export function setRequestManagerId(managerId: string): void {
  activeManagerId = managerId;
  try {
    window.sessionStorage.setItem(MANAGER_STORAGE_KEY, managerId);
  } catch {
    // The in-memory value still keeps requests consistent in restricted browsers.
  }
}

export function getRequestManagerId(): string {
  return activeManagerId;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code: string;
  readonly checks: CheckResult[] | null;

  constructor(
    status: number,
    detail: string,
    code: string,
    checks: CheckResult[] | null = null,
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.checks = checks;
  }
}

export async function apiFetch<TSchema extends z.ZodTypeAny>(
  path: string,
  init: RequestInit,
  schema: TSchema,
): Promise<z.output<TSchema>>;
export async function apiFetch<T = unknown>(path: string, init?: RequestInit): Promise<T>;
export async function apiFetch(
  path: string,
  init: RequestInit = {},
  schema?: z.ZodTypeAny,
): Promise<unknown> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (supabaseAuthEnabled) {
    try {
      const accessToken = await getSupabaseAccessToken();
      if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    } catch {
      throw new ApiError(401, "Your sign-in session could not be refreshed. Sign in again.", "unauthorized");
    }
  } else {
    headers.set("X-Manager-Id", getRequestManagerId());
  }
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "The API could not be reached. Check that the backend is running.", "network_error");
  }

  const raw = await response.text();
  let data: unknown = null;
  if (raw) {
    try {
      data = JSON.parse(raw) as unknown;
    } catch {
      data = raw;
    }
  }
  if (!response.ok) {
    const error = errorOutSchema.safeParse(data);
    if (error.success) {
      throw new ApiError(response.status, error.data.detail, error.data.code, error.data.checks ?? null);
    }
    throw new ApiError(response.status, `Request failed (${response.status}).`, "http_error");
  }

  if (schema) {
    const parsed = schema.safeParse(data);
    if (!parsed.success) {
      throw new ApiError(
        502,
        "The API response did not match the expected data format. Refresh and try again.",
        "response_validation_error",
      );
    }
    return parsed.data;
  }
  return data;
}
