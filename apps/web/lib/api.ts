/**
 * Browser API client. Same-origin requests to /api/v1 with the HttpOnly session cookie;
 * state-changing requests echo the CSRF cookie in X-CSRF-Token. Tokens are never stored in
 * localStorage.
 */
import type { components } from "@databasemax/shared-types";

export type Schemas = components["schemas"];

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId: string | null;
  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId ?? null;
  }
}

const BASE = "/api/v1";

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/[.$?*|{}()[\]\\/+^]/g, "\\$&") + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]!) : null;
}

let csrfPromise: Promise<string> | null = null;
async function csrfToken(): Promise<string> {
  const existing = readCookie("dmx_csrf");
  if (existing) return existing;
  csrfPromise ??= fetch(`${BASE}/auth/csrf`, { credentials: "same-origin" })
    .then((r) => r.json())
    .then((j: { csrf_token: string }) => j.csrf_token)
    .finally(() => {
      csrfPromise = null;
    });
  return csrfPromise;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  query?: Query;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = await csrfToken();
  let url = BASE + path;
  if (opts.query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const res = await fetch(url, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown; request_id?: string } })?.error;
    throw new ApiError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? "Something went wrong. Please try again.",
      err?.details,
      err?.request_id ?? res.headers.get("X-Request-ID"),
    );
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}

export function newIdempotencyKey(prefix: string): string {
  const rnd = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random()).slice(2);
  return `${prefix}-${rnd}`;
}
