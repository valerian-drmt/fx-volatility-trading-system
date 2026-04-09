"""
test_future_order.py
Test achat/vente future EUR/USD CME — paper trading, ordre MKT.
"""
from datetime import datetime
from ib_insync import IB, Contract, Order, util

util.patchAsyncio()

PORT      = 4002
CLIENT_ID = 15

ACTION = "BUY"       # "BUY" ou "SELL"
EXPIRY = "20260615"  # front future 6EM6
QTY    = 1

ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)
ib.reqMarketDataType(3)

# Qualifier le contrat
fut = Contract()
fut.symbol       = "EUR"
fut.secType      = "FUT"
fut.exchange     = "CME"
fut.currency     = "USD"
fut.lastTradeDateOrContractMonth = EXPIRY

details = ib.reqContractDetails(fut)
if not details:
    print("ERROR : contrat non trouvé")
    ib.disconnect()
    raise SystemExit(1)

fut = details[0].contract
print(f"Contrat : {fut.localSymbol}  conId={fut.conId}")

# Bid/ask avant ordre
ticker = ib.reqMktData(fut, "", False, False)
ib.sleep(4)

bid   = ticker.bid
ask   = ticker.ask
last  = ticker.last
close = ticker.close
mid   = round((bid + ask) / 2, 6) if bid and ask else None

print(f"bid={bid}  ask={ask}  last={last}  close={close}  mid={mid}")

entry_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
entry_bid   = bid
entry_ask   = ask
entry_mid   = mid

ib.cancelMktData(fut)

# Confirmer
print(f"\nOrdre : {ACTION} {QTY} x {fut.localSymbol} MKT")
if input("Confirmer ? (y/n) : ").lower() != "y":
    print("Annulé.")
    ib.disconnect()
    raise SystemExit(0)

# Envoyer
order = Order()
order.action        = ACTION
order.totalQuantity = QTY
order.orderType     = "MKT"
order.tif           = "DAY"

trade = ib.placeOrder(fut, order)
ib.sleep(3)

print(f"\nStatus   : {trade.orderStatus.status}")
print(f"Filled   : {trade.orderStatus.filled} / {QTY}")
print(f"AvgPrice : {trade.orderStatus.avgFillPrice}")

# Attendre fill
for _ in range(15):
    ib.sleep(2)
    s = trade.orderStatus.status
    print(f"  [{s}]  filled={trade.orderStatus.filled}  avg={trade.orderStatus.avgFillPrice}")
    if s in ("Filled", "Cancelled", "Inactive"):
        break

fill_price = trade.orderStatus.avgFillPrice

# ── Positions avec données live ──
ib.sleep(1)
positions = ib.positions()

print(f"\n{'=' * 70}")
print("POSITIONS OUVERTES")
print(f"{'=' * 70}")

for p in positions:
    c = p.contract
    if c.secType != "FUT":
        continue

    tk = ib.reqMktData(c, "", False, False)
    ib.sleep(4)

    p_bid   = tk.bid
    p_ask   = tk.ask
    p_last  = tk.last
    p_close = tk.close
    p_mid   = round((p_bid + p_ask) / 2, 6) if p_bid and p_ask else None

    ib.cancelMktData(c)

    qty      = p.position
    avg_cost = p.avgCost
    multiplier = float(c.multiplier) if c.multiplier else 125000.0

    # P&L mid
    pnl_mid = round((p_mid - avg_cost) * qty * multiplier, 2) if p_mid else None

    print(f"\n  Symbole    : {c.localSymbol}")
    print(f"  Expiry     : {c.lastTradeDateOrContractMonth}")
    print(f"  Qty        : {qty:+.0f} contrat(s)")
    print(f"  Multiplier : {multiplier:,.0f} EUR/contrat")
    print(f"  ── Entrée ──────────────────────────────")
    print(f"  Heure      : {entry_time}")
    print(f"  Bid/Ask    : {entry_bid} / {entry_ask}  mid={entry_mid}")
    print(f"  Fill price : {fill_price}")
    print(f"  ── Live ────────────────────────────────")
    print(f"  Bid/Ask    : {p_bid} / {p_ask}  mid={p_mid}")
    print(f"  Last       : {p_last}")
    print(f"  Close      : {p_close}")
    print(f"  ── P&L ─────────────────────────────────")
    print(f"  AvgCost    : {avg_cost:.5f}")
    print(f"  P&L mid    : {pnl_mid:+.2f} USD" if pnl_mid else "  P&L mid    : --")
    notional = qty * multiplier * (p_mid if p_mid else avg_cost)
    print(f"  Notionnel  : {notional:,.0f} USD")

print(f"\n{'=' * 70}")
ib.disconnect()