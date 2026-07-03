# IB order ops — send, inspect, clean up

Operational runbook for the **IB order layer**: the commands to submit / list /
cancel orders, what each **order state** means, why an order can sit `Submitted`
forever (the *naked-butterfly* cause), and how to clear the residue.

For the *code* path (how a click becomes an order) see `docs/ORDER_PIPELINE.md`.
This doc is the *operator* side — what to run and what to expect.

> **Secrets rule (see `CLAUDE.md`)**: none of these commands print a secret. Never
> dump `env` / `docker inspect` Config.Env on the fxvol containers. The
> execution-engine is on the internal Docker network only — reach it *through* a
> container (`docker compose exec …`), never expose :8001 publicly.

---

## 1. The execution-engine internal API

The engine (`fxvol-execution`, clientID 5) listens on **:8001**, router prefix
**`/internal`**. It's not published — call it from inside a container. `curl`
isn't in the image, so use a Python one-liner:

| Method + path | Does | Expected |
|---|---|---|
| `GET  /internal/orders` | open orders resting at IB (not yet filled/cancelled) | `{count, orders[]}` |
| `GET  /internal/trades` | every trade in the IB session (open + done + rejected) | diagnostic dump |
| `DELETE /internal/orders/{id}` | cancel one resting order | `{...}` or 404 if not open |
| `POST /internal/reconcile` | flip stuck `submitted` DB rows to `filled` when IB holds the contract | `{reconciled, closed}` |
| `POST /internal/positions/sync` | force a positions/mirror sync now | sync counts |
| `GET  /health` (no prefix) | IB connection + sync interval | `{status, ib_connected, …}` |

Helper — run any of them:

```powershell
docker compose exec -T execution-engine python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8001/internal/orders')))"
```

---

## 2. Order states — what "submitted but not filled" means

IB reports an `orderStatus.status` per order; we map it onto `trade_order.state`:

| IB status | Our state | Meaning | Fills? |
|---|---|---|---|
| `PendingSubmit` | `pending` | sent, not yet acknowledged | not yet |
| `Submitted` | `submitted` | **live at IB, working** | when a counterparty trades |
| `Filled` | `filled` | fully executed | ✅ |
| `Inactive` | (stays `submitted`) | IB **rejected/parked** it (e.g. option **price-cap** hit) | ❌ won't fill |
| `Cancelled` | `cancelled` | pulled (by us or IB) | ❌ |
| `ApiCancelled` | `cancelled` | our DELETE took effect | ❌ |

**`Submitted` ≠ filled.** A resting `Submitted` order is *working* — it fills only
when there's opposing size at (or through) its limit. On **IB paper**, deep-OTM /
thin strikes may have **no size**, so the order rests indefinitely. `Inactive`
means IB parked it outright (usually the option **market-order price-cap** — which
is exactly why we send **marketable limits**, see ORDER_PIPELINE §3).

---

## 3. Why a butterfly goes *naked* — and how to see it

A butterfly is **3 legs** (low + 2× ATM body + high). If one leg's order rests
`Submitted`/`Inactive` while the others fill, you hold an **unbalanced** position —
e.g. the short body filled but a long wing didn't → **naked short**, unbounded tail.
The 10Δ wings are the usual culprit: **deep OTM = thin/no liquidity**, so their
order sits unfilled (a *market/volume* problem, not a pricing bug).

**Diagnose it:**

```powershell
# 1. orders still resting at IB (the ones keeping you naked)
docker compose exec -T execution-engine python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:8001/internal/orders')); print('count =', d['count']); [print(o.get('order_id'), o.get('action'), o.get('totalQuantity'), o.get('status')) for o in d['orders']]"

# 2. the DB view : which legs filled vs. still submitted
docker compose exec -T postgres psql -U fxvol -d fxvol -c "select o.structure_id, o.leg_idx, o.side, o.state, o.qty, o.qty_filled, o.ib_local_symbol from trade_order o where o.structure_id = <ID> order by o.leg_idx;"

# 3. what IB actually holds (the mirror the panel reads)
docker compose exec -T postgres psql -U fxvol -d fxvol -c "select id, structure, side, quantity, market_price from open_position order by id;"
```

The Open positions panel flags this as `1/3 legs ⚠ naked`. `GET
/positions/reconciliation` (API) gives the book-vs-broker breaks per contract.

---

## 4. Clean up resting orders

Waiting isn't always enough (a deep-OTM leg may never fill on paper). To stop a
stuck order from keeping you naked, **cancel it** and re-decide.

**Cancel one order** (by the `order_id` from §3 step 1):

```powershell
docker compose exec -T execution-engine python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8001/internal/orders/<ID>', method='DELETE'), timeout=10); print('cancelled')"
```

**Cancel ALL resting orders** (nuclear — clears every working order at IB; use when
a stack has piled up):

```powershell
docker compose exec -T execution-engine python -c "import urllib.request,json; b='http://localhost:8001/internal'; d=json.load(urllib.request.urlopen(b+'/orders')); [ (urllib.request.urlopen(urllib.request.Request(b+'/orders/%d'%o['order_id'],method='DELETE')), print('cancelled',o['order_id'])) for o in d['orders'] ]"
```

**Verify it's clear** (expect `count = 0`):

```powershell
docker compose exec -T execution-engine python -c "import urllib.request,json; print('open orders =', json.load(urllib.request.urlopen('http://localhost:8001/internal/orders'))['count'])"
```

> ⚠️ Cancelling live orders is an **account action** — it's yours to run, not
> Claude's. A cancelled order at IB does **not** auto-flip its `trade_order` row to
> `cancelled` (no cancel→DB sync), so the DB can keep a stale `submitted` row. That
> stale row is harmless for the close guard (it self-heals after
> `CLOSE_INFLIGHT_WINDOW_MIN`, default 3 min), but you can reconcile it explicitly:
> `docker compose exec -T postgres psql -U fxvol -d fxvol -c "update trade_order set state='cancelled' where order_role='closing' and state in ('pending','submitted','partially_filled');"`

---

## 5. After a cleanup — close what's left

Once the stuck orders are cancelled, flatten the residual leg(s) from the panel
(**Close** / **Close all**) or via the API close. Options close as **marketable
limits** priced off the live quote / mark (ORDER_PIPELINE §5), so they cross — but
the same **paper-liquidity** limit applies to a deep-OTM residual: it may dribble.
On a **live** account with real market-makers the wings fill far more reliably.

**Rule of thumb to avoid naked wings on paper:** trade **25Δ** rather than **10Δ**
(much more liquid), and expect a deep-OTM leg to sometimes rest.

---

## 6. Quick reference

```powershell
# health / IB connected?
docker compose exec -T execution-engine python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8001/health')))"

# list resting orders
docker compose exec -T execution-engine python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://localhost:8001/internal/orders')); print(d['count']); [print(o.get('order_id'),o.get('status')) for o in d['orders']]"

# cancel all resting orders
docker compose exec -T execution-engine python -c "import urllib.request,json; b='http://localhost:8001/internal'; d=json.load(urllib.request.urlopen(b+'/orders')); [urllib.request.urlopen(urllib.request.Request(b+'/orders/%d'%o['order_id'],method='DELETE')) for o in d['orders']]; print('done')"

# reconcile stuck submitted rows against IB holdings
docker compose exec -T execution-engine python -c "import urllib.request,json; print(json.load(urllib.request.urlopen(urllib.request.Request('http://localhost:8001/internal/reconcile', method='POST'))))"

# positions the broker reports
docker compose exec -T postgres psql -U fxvol -d fxvol -c "select id, structure, side, quantity from open_position order by id;"
```

Related: `docs/ORDER_PIPELINE.md` (code path), `docs/BACKEND_ARCHITECTURE.md`
(system-of-record vs mirror + reconciliation), `CLAUDE.md` (secrets rules).
