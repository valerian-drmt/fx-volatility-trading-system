# IB integration

Every service that touches the market opens its own connection to the IB Gateway
via `ib_insync`. The connection wrapper with backoff is
[`src/shared/ib_connection.py`](../../src/shared/ib_connection.py); the execution
engine's connection is
[`engines/execution/order_executor.py`](../../src/engines/execution/order_executor.py).

## The gateway

The stack runs `gnzsnz/ib-gateway` as the `ib-gateway` container (IB Gateway +
IBC). Engines reach it on the internal port `4002` over the `fxvol-external`
network. Outbound IB traffic is isolated on that network.

## One client ID per service

`ib_insync` requires a distinct `clientId` per socket. Each engine binds its own
via `IB_CLIENT_ID` (`docker-compose.yml`):

| Service | clientId |
|---|---|
| `market-data` | 1 |
| `vol-engine` | 2 |
| `risk-engine` | 3 |
| `execution-engine` | 5 |

The api's own `OrderExecutor` (used by `api.routers.orders`) uses clientId 4. A
cycle engine (market-data/vol/risk) re-checks `ib.isConnected()` at the top of its
loop and reconnects inline; the request-driven execution engine instead runs a
`maintain_ib_connection` watchdog that polls `is_connected()` every 10s and calls
the idempotent `connect()` while down. Polling is deliberate — a torn-socket
`disconnectedEvent` is best-effort, the poll is the guarantee.

## Backoff and observability

`connect_ib_with_backoff(ib, host, port, client_id, ...)` retries with an
exponential backoff (capped at 60s, `shared.backoff`), yielding via
`asyncio.sleep` so the event loop keeps running. In prod it retries forever, since
the Gateway comes back from its nightly ~23:59 restart. Each attempt mirrors
session state into the Prometheus gauge `ib_session_connected{client_id}` for the
Grafana "IB session uptime" panel.

## Market-data types

IB won't stream live quotes without a real-time subscription, so services request
a delayed type via `reqMarketDataType`:

- **market-data** and **vol-engine chain fetcher** request type **3** (delayed).
- **execution-engine** requests type **4** (delayed-frozen: delayed during hours,
  frozen after) on connect, so option tickers still populate a bid/ask and the
  marketable limit prices at the touch (see
  [order-lifecycle.md](order-lifecycle.md)).

On connect the execution engine also subscribes to positions
(`reqPositionsAsync`) and the account-summary ledger (`reqAccountSummaryAsync`) so
`ib.positions()` and per-currency cash populate after a reconnect.

## Single session per userid

IB allows one live session per userid. A web login on the client portal kicks the
container's IBC session, and the paper account is shared: prod runs the engines +
gateway 24/5 and owns the session. Stop the prod `ib-gateway` before any portal
access or a local IB run, or the running engines lose their connection.

## Paper vs live — READ_ONLY_API

Orders route to the IB **paper** account (the order builder stamps every ticket
`PAPER ACCOUNT`). The gateway's `READ_ONLY_API` setting is mirrored into the
engine as `IB_READ_ONLY` (`docker-compose.yml`, both default `no`). The
`OrderExecutor(readonly=...)` flag must match the gateway: when the gateway is
read-only, `ib_insync`'s default handshake calls `reqAutoOpenOrders`, IB rejects
it ("Error 321 ... Read-Only mode"), and the connect never completes — passing
`readonly` through skips that call so the engine degrades to reads instead of
failing to connect.

## Secrets

IB credentials (`IB_USERID`, `IB_PASSWORD`, `VNC_PASSWORD`) live in AWS SSM
Parameter Store under `/fxvol/prod/*`, loaded into RAM at launch — never on disk,
never echoed. See [ops/secrets.md](../ops/secrets.md).
