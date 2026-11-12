/**
 * In-place patching of REST slices from execution-engine push messages
 * (hot/cold T3, #367). Pure functions, unit-tested:
 *
 *  - `patchPositions`     : `positions_changed` -> the Open positions rows
 *  - `patchSpotOrderRows` : `spot_order_changed` -> the raw `/trade/spot-orders` rows
 *  - `patchAccountRaw`    : `account_snapshot`   -> the raw `/portfolio/account` payload
 *
 * Every function returns its input *by identity* when the message changes
 * nothing, so a memoised consumer does not re-render on a no-op tick.
 */
import type { SpotOrderRow } from "../../../api/endpoints";
import type { AccountSnapshotMsg, PositionsChangedMsg, SpotOrderChangedMsg } from "../../../hooks/streams";
import type { Position } from "../core";
import { adaptPositions } from "./trade";

function samePosition(a: Position, b: Position): boolean {
  for (const k of Object.keys(a) as (keyof Position)[]) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

/** Replace the rows the message upserted (matched on id), append the new
 * ones, drop the closed ids. Rows the message does not name keep their object
 * identity. */
export function patchPositions(positions: Position[], msg: PositionsChangedMsg, now: number): Position[] {
  const incoming = adaptPositions(msg.rows, now);
  const closed = new Set(msg.closed.map((c) => String(c.id)));
  if (incoming.length === 0 && closed.size === 0) return positions;
  const byId = new Map(incoming.map((p) => [p.id, p]));
  let changed = false;
  const out: Position[] = [];
  for (const p of positions) {
    if (closed.has(p.id)) {
      changed = true;
      continue;
    }
    const pushed = byId.get(p.id);
    if (pushed === undefined) {
      out.push(p);
      continue;
    }
    // A push carries the broker mirror row, not the position line (#461): the
    // row keeps the line identity it got from the last read.
    const next: Position =
      pushed.lineId === undefined && p.lineId !== undefined
        ? { ...pushed, lineId: p.lineId, lineStatus: p.lineStatus, netted: p.netted ?? false }
        : pushed;
    byId.delete(p.id);
    if (samePosition(p, next)) out.push(p);
    else {
      out.push(next);
      changed = true;
    }
  }
  for (const p of byId.values()) {
    if (closed.has(p.id)) continue;
    out.push(p);
    changed = true;
  }
  return changed ? out : positions;
}

const SPOT_ROWS_CAP = 30;

/** Upsert one spot order row (matched on id), newest first, capped like the
 * REST read. A message older than the row we hold (same id, earlier
 * `state_updated_at`) is ignored. */
export function patchSpotOrderRows(rows: SpotOrderRow[] | null, msg: SpotOrderChangedMsg): SpotOrderRow[] | null {
  const base = rows ?? [];
  const row = msg.row;
  const idx = base.findIndex((r) => r.id === row.id);
  if (idx >= 0) {
    const held = base[idx]!;
    if (sameSpotRow(held, row)) return rows;
    if (Date.parse(row.state_updated_at ?? "") < Date.parse(held.state_updated_at ?? "")) return rows;
    const out = base.slice();
    out[idx] = row;
    return out;
  }
  const out = [row, ...base]
    .sort((a, b) => (Date.parse(b.created_at) - Date.parse(a.created_at)) || (b.id - a.id))
    .slice(0, SPOT_ROWS_CAP);
  return out;
}

function sameSpotRow(a: SpotOrderRow, b: SpotOrderRow): boolean {
  for (const k of Object.keys(b) as (keyof SpotOrderRow)[]) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

interface AccountRaw {
  latest?: { timestamp?: string | null } | null;
}

/** Swap `latest` in the `/portfolio/account` payload when the message is
 * newer; `prev_24h`, buying power and the rest of the payload are kept. */
export function patchAccountRaw(raw: unknown, msg: AccountSnapshotMsg): unknown {
  const o = (raw ?? {}) as AccountRaw;
  const held = Date.parse(o.latest?.timestamp ?? "");
  const next = Date.parse(msg.latest.timestamp ?? msg.timestamp);
  if (Number.isFinite(held) && Number.isFinite(next) && next <= held) return raw;
  return { ...(raw as object), latest: msg.latest };
}
