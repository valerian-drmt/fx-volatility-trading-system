/**
 * Turn a failed /dev/logs/* call into something the operator can act on (#378).
 *
 * The page used to print the raw transport error:
 *   `Error: HTTP 503 : {"detail":"loki unreachable : ConnectError"}`
 * which says nothing about WHY (obs profile not started locally, Loki
 * restarting after an OOM on prod) or what to do. The api's proxy answers 503
 * with `loki unreachable : <ExcName>` or `loki returned <status>`; everything
 * else is a normal API error (401 when logged out, 5xx, network).
 */

export type LogsErrorKind = "loki_down" | "auth" | "other";

export interface LogsError {
  kind: LogsErrorKind;
  title: string;
  hint: string;
  detail: string; // the original text, kept small
}

const LOKI_RE = /loki (unreachable|returned)/i;

export function classifyLogsError(status: number | null, text: string): LogsError {
  const detail = text.length > 200 ? text.slice(0, 200) + "…" : text;
  const code = status ?? (/\b(\d{3})\b/.exec(text)?.[1] ? Number(/\b(\d{3})\b/.exec(text)![1]) : null);
  if (code === 401 || /authentication required/i.test(text)) {
    return {
      kind: "auth",
      title: "Login required",
      hint: "The log tail is a trader tool: log in (top right) and the query re-runs.",
      detail,
    };
  }
  if (code === 503 && LOKI_RE.test(text)) {
    return {
      kind: "loki_down",
      title: "Loki is not reachable from the api",
      hint:
        "Locally: the obs profile is opt-in — `docker compose --profile obs up -d` (Loki needs ~45 s to become ready). " +
        "On prod: check `loki` in Stack overview; the container is memory-capped (320 MB) and restarts on its own after an OOM, " +
        "so a few minutes of this is a restart, longer is an incident. Previous lines, if any, are kept below.",
      detail,
    };
  }
  return {
    kind: "other",
    title: code != null ? `Log query failed (HTTP ${code})` : "Log query failed",
    hint: "Retry; if it persists check the api container's logs (`docker logs fxvol-api`).",
    detail,
  };
}
