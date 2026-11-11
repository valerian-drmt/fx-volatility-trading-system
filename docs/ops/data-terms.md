# Data sources and terms

What the system takes from whom, what it keeps, what each provider's terms ask
for, and the decisions on record (#307, audit 10#13, release gate §8). The
attribution strings below are the ones the desk and the README show
(`frontend/src/voldesk/components/DataAttribution.tsx`).

## Sources

| Source | How | What is stored | Attribution shown |
|---|---|---|---|
| Interactive Brokers (paper account) | `ib_async` to IB Gateway, **delayed** market data (`reqMarketDataType(3)`, execution `4`) | ticks (Redis, TTL), vol surfaces and fits, positions, orders, fills, account values (Postgres) | rail: "Market data: IB, delayed · paper"; README |
| FRED (Federal Reserve Bank of St. Louis) | `GET /fred/releases/dates` with an API key | release **dates** of whitelisted US releases in `events` | the FRED notice under Macro events and in the README |
| Federal Reserve (FOMC) | meeting dates, hardcoded from federalreserve.gov | rows in `events` | "Federal Reserve" under Macro events |
| ECB | monetary-policy meeting dates, hardcoded from ecb.europa.eu | rows in `events` | "ECB" |
| Bank of England | MPC dates, hardcoded from bankofengland.co.uk | rows in `events` | "Bank of England" |
| Eurostat | HICP / GDP release schedule computed from Eurostat's published rules | rows in `events` | "Eurostat" |
| ONS | CPI / GDP release schedule computed from the ONS calendar rules | rows in `events` | the Open Government Licence v3.0 statement |

None of the calendar sources is scraped; only FRED is called at run time.

## Decisions

### D3, attribution: decided 2026-11-10

Every source is named where its data appears: the macro-events panel carries
the calendar sources, the FRED notice ("This product uses the FRED® API but is
not endorsed or certified by the Federal Reserve Bank of St. Louis.") and the
OGL v3.0 statement for ONS; the rail says the market data is IB, delayed, on a
paper account; the README has the same list. Done in #307.

### D1, IB-derived data on the public desk: **open, owner decision**

The public read-only desk and the WebSocket streams show IB-derived data to
anyone: the EUR/USD mid / bid / ask (delayed), the option-chain-derived IV
surface, and positions / P&L of the paper account. IB's market-data
agreements restrict redistribution by a non-professional subscriber, delayed
data included (release gate §8.1).

Recommendation: **keep raw quotes behind the trader login, keep derived
analytics public.** Concretely: `/ws/ticks`, the bars endpoint and the top-bar
quote become trader-only; the IV surface (a model output), signals, regime,
greeks and the paper book stay public with the "delayed · paper" label. It is
the cheapest stance that removes the raw-quote redistribution, keeps the
public demo meaningful, and is one auth dependency on three routes plus the
socket. Alternatives: take the whole deployment private (no public demo), or
obtain a redistribution entitlement from IB (cost, paperwork).

### D2, FRED storage: **open, owner decision**

The FRED terms prohibit storing FRED content in a database; the events
pipeline persists FRED release dates in `events` (release gate §8.2).

Recommendation: **stop persisting FRED rows; fetch them into a Redis cache
with a 24 h TTL and merge them at read time.** The regime gate and the
macro-events panel keep the same data, nothing FRED-sourced lands in Postgres
or in the backups, and the other calendars (all hardcoded or computed) are
unaffected. Alternative: drop FRED and hardcode the handful of US releases the
whitelist uses (CPI, NFP, GDP, PCE), like the ECB / BoE / FOMC dates.

## How to change a source

Add the row to the table above in the same PR as the code, with the
attribution its terms ask for, and add the string to `DataAttribution.tsx`.
