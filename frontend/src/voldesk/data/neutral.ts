/**
 * Honest empty states (remediation 05 WI-2): all-zero account / greeks
 * literals used when a live slice has no data yet. Zeros + the per-panel
 * FreshBadge "no data" are the agreed neutral rendering for scalar panels —
 * never fabricated numbers. Where a zero would read as a *claim* (net liq,
 * buying power…), the views render "—" instead.
 */
import type { AccountState, Greeks } from "./core";

export const EMPTY_ACCOUNT: AccountState = Object.freeze({
  netLiq: 0,
  dNetLiq: 0,
  cash: 0,
  dCash: 0,
  unrealized: 0,
  dayPnl: 0,
  dayPnlPct: 0,
  realized: 0,
  marginInit: 0,
  marginMaint: 0,
  marginInitPct: 0,
  marginMaintPct: 0,
  excessLiq: 0,
  cushion: 0,
  nPositions: 0,
  dPositions: 0,
  buyingPower: 0,
  availableFunds: 0,
});

export const EMPTY_GREEKS: Greeks = Object.freeze({
  delta: 0,
  gamma: 0,
  theta: 0,
  vega: 0,
  vanna: 0,
  volga: 0,
  charm: 0,
  var1d99: 0,
  var1d95: 0,
  beta: 0,
  dDelta24h: 0,
  dVega24h: 0,
  dVanna24h: 0,
  dVolga24h: 0,
  netDelta: 0,
  netGamma: 0,
  netVega: 0,
  netTheta: 0,
  netVanna: 0,
  netVolga: 0,
  netNominal: 0,
  netUnreal: 0,
});
