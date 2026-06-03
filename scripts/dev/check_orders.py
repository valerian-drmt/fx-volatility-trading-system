"""Dump every IB trade visible to the execution-engine session.

Hits ``/internal/trades`` which reads ``ib.trades()`` from the same
clientId=5 that placed any closes, so a freshly-submitted close (and
its status, including ``Rejected`` / ``Inactive`` with the reason)
becomes visible immediately.

Workflow :
    1. Click Close in the UI.
    2. Run this script — the new order appears at the bottom of the
       list with its ``status`` and ``last_log`` (reject reason).

Re-deploy :
    docker compose cp check_orders.py execution-engine:/tmp/check_orders.py
    docker compose exec execution-engine python /tmp/check_orders.py
"""
import httpx

resp = httpx.get("http://localhost:8001/internal/trades", timeout=15)
resp.raise_for_status()
trades = resp.json()["trades"]
print(f"total trades visible : {len(trades)}\n")

# Show every trade — sort by orderId so the freshest sits at the bottom.
# IB's orderId is monotonically increasing within a session, so the
# largest id == most recent submission.
trades_sorted = sorted(trades, key=lambda t: t.get("order_id") or 0)

cols = ("order_id", "status", "side", "qty", "sym", "limit", "perm_id", "why")
print("{:>8} {:<14} {:<5} {:>5} {:<14} {:>10} {:>10}  {}".format(*cols))
print("-" * 100)
for t in trades_sorted:
    last = (t.get("last_log") or "-")[:80]
    print(
        "{:>8} {:<14} {:<5} {:>5} {:<14} {:>10} {:>10}  {}".format(
            str(t.get("order_id")),
            str(t.get("status")),
            str(t.get("side")),
            str(t.get("qty")),
            str(t.get("local_symbol")),
            str(t.get("limit_price")) if t.get("limit_price") is not None else "MKT",
            str(t.get("perm_id")) if t.get("perm_id") is not None else "-",
            last,
        )
    )
