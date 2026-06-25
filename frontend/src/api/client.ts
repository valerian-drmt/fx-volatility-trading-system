// Typed fetch wrapper. The project lives under the deploy base
// (import.meta.env.BASE_URL, e.g. "/fx-volatility-trading-system/"), so API
// calls are prefixed with it: Nginx routes <base>/api → api:8000 (prod) and the
// Vite proxy forwards <base>/api in dev. At the root / in tests BASE_URL is "/"
// → empty prefix → "/api/...". Override the whole base via VITE_API_BASE_URL.
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
