import { z } from "zod";
import { errorOutSchema, type CheckResult } from "./schemas";
import { getSupabaseAccessToken, supabaseAuthEnabled } from "../auth/client";

import { BRAND } from "../brand";
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

export interface ApiStreamEvent {
  event: string;
  data: unknown;
}

async function requestHeaders(init: RequestInit, accept: string): Promise<Headers> {
  const headers = new Headers(init.headers);
  headers.set("Accept", accept);
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
  return headers;
}

async function readErrorResponse(response: Response): Promise<never> {
  const raw = await response.text();
  let data: unknown = null;
  if (raw) {
    try {
      data = JSON.parse(raw) as unknown;
    } catch {
      data = raw;
    }
  }
  const error = errorOutSchema.safeParse(data);
  if (error.success) {
    throw new ApiError(response.status, error.data.detail, error.data.code, error.data.checks ?? null);
  }
  throw new ApiError(response.status, `Request failed (${response.status}).`, "http_error");
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
  const headers = await requestHeaders(init, "application/json");

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

export async function apiStream<TSchema extends z.ZodTypeAny>(
  path: string,
  init: RequestInit,
  schema: TSchema,
  onEvent: (event: ApiStreamEvent) => void,
): Promise<z.output<TSchema>> {
  const headers = await requestHeaders(init, "text/event-stream");
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "The API could not be reached. Check that the backend is running.", "network_error");
  }
  if (!response.ok) return readErrorResponse(response);
  if (!response.body) {
    throw new ApiError(502, "The API did not open an answer stream. Refresh and try again.", "stream_unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalData: unknown;
  const dispatch = (frame: string) => {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return;
    const rawData = dataLines.join("\n");
    let data: unknown = rawData;
    try {
      data = JSON.parse(rawData) as unknown;
    } catch {
      // Keep plain event data available to callers if a future event uses text.
    }
    if (event === "error") {
      const detail =
        typeof data === "object" && data !== null && "detail" in data && typeof data.detail === "string"
          ? data.detail
          : `${BRAND.name} could not complete that request.`;
      throw new ApiError(502, detail, "assistant_stream_error");
    }
    if (event === "complete") finalData = data;
    onEvent({ event, data });
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let delimiter = buffer.indexOf("\n\n");
      while (delimiter >= 0) {
        dispatch(buffer.slice(0, delimiter));
        buffer = buffer.slice(delimiter + 2);
        delimiter = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  } finally {
    reader.releaseLock();
  }

  if (finalData === undefined) {
    throw new ApiError(502, `The answer stream ended before ${BRAND.name} completed the response.`, "incomplete_stream");
  }
  const parsed = schema.safeParse(finalData);
  if (!parsed.success) {
    throw new ApiError(502, "The streamed answer did not match the expected format.", "response_validation_error");
  }
  return parsed.data;
}
