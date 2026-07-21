// Typed fetch wrapper. The whole project (UI + API) lives under the deploy base
// (import.meta.env.BASE_URL, e.g. "/fx-volatility-trading-system/"), so API calls
// are prefixed with it: Nginx routes <base>/api → api:8000 (prod) and the Vite
// proxy forwards <base>/api in dev. In tests/at the root BASE_URL is "/" → empty
// prefix → "/api/...". Override the whole base via VITE_API_BASE_URL.
const BASE_URL =
  import.meta.env["VITE_API_BASE_URL"] ?? import.meta.env.BASE_URL.replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    readonly body: unknown,
  ) {
    super(`API ${status} ${url}`);
    this.name = "ApiError";
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export interface RequestOptions {
  query?: Record<string, string | number | boolean | undefined>;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${BASE_URL}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

function buildInit(base: RequestInit, signal: AbortSignal | undefined): RequestInit {
  // Always send cookies so the httpOnly auth cookie (fxvol_auth) rides along
  // on write requests once the trader has logged in.
  const init: RequestInit = { ...base, credentials: "include" };
  return signal ? { ...init, signal } : init;
}

/**
 * Base-prefixed raw fetch for callers that handle the Response themselves
 * (dev pages, status badge). Same BASE_URL as the typed client, so these
 * calls survive the deploy subpath; cookies ride along for the /dev auth gate.
 */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${BASE_URL}${path}`, { ...init, credentials: "include" });
}

/**
 * `apiFetch` + status check + JSON decode, for callers that only want the body.
 *
 * Prefer this over raw `apiFetch` when the response is JSON: `apiFetch` resolves
 * on a non-2xx like any `fetch`, so a caller that goes straight to `.json()`
 * silently decodes the error payload (`{"detail": "…"}`) and reads `undefined`
 * out of it. That is how a logged-out /dev used to blank the console — the 401
 * body has no `tables` key, and the next render called `.find` on `undefined`.
 */
export async function apiFetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init);
  const body = await parseBody(res);
  if (!res.ok) throw new ApiError(res.status, `${BASE_URL}${path}`, body);
  return body as T;
}

export async function apiGet<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = buildUrl(path, opts.query);
  const res = await fetch(url, buildInit({ method: "GET" }, opts.signal));
  const body = await parseBody(res);
  if (!res.ok) throw new ApiError(res.status, url, body);
  return body as T;
}

export async function apiPost<T>(
  path: string,
  payload: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.query);
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  const res = await fetch(url, buildInit(init, opts.signal));
  const body = await parseBody(res);
  if (!res.ok) throw new ApiError(res.status, url, body);
  return body as T;
}

export async function apiPut<T>(
  path: string,
  payload: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.query);
  const init: RequestInit = {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
  const res = await fetch(url, buildInit(init, opts.signal));
  const body = await parseBody(res);
  if (!res.ok) throw new ApiError(res.status, url, body);
  return body as T;
}
