"""
test_option_order.py
Test achat/vente option FOP EUR/USD CME — paper trading, ordre MKT.
"""
from datetime import datetime
from ib_insync import IB, Contract, Order, util

util.patchAsyncio()

PORT      = 4002
CLIENT_ID = 15

ACTION = "BUY"       # "BUY" ou "SELL"
RIGHT  = "C"         # "C" ou "P"
STRIKE = 1.1725
EXPIRY = "20260508"
QTY    = 1

ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)
ib.reqMarketDataType(3)

# Qualifier le contrat
fop = Contract()
fop.symbol       = "EUR"
fop.secType      = "FOP"
fop.exchange     = "CME"
fop.currency     = "USD"
fop.lastTradeDateOrContractMonth = EXPIRY
fop.strike       = STRIKE
fop.right        = RIGHT
fop.multiplier   = "125000"
fop.tradingClass = "EUU"

details = ib.reqContractDetails(fop)
if not details:
    print("ERROR : contrat non trouvé")
    ib.disconnect()
    raise SystemExit(1)

fop = details[0].contract
print(f"Contrat : {fop.localSymbol}  conId={fop.conId}")

# Bid/ask + greeks avant ordre
ticker = ib.reqMktData(fop, "100", False, False)
ib.sleep(4)

bid   = ticker.bid
ask   = ticker.ask
greeks = ticker.modelGreeks
iv     = greeks.impliedVol if greeks else None
delta  = greeks.delta      if greeks else None
gamma  = greeks.gamma      if greeks else None
vega   = greeks.vega       if greeks else None
theta  = greeks.theta      if greeks else None
und    = greeks.undPrice   if greeks else None

print(f"bid={bid}  ask={ask}")
if iv:
    print(f"IV={iv*100:.2f}%  delta={delta:.4f}  gamma={gamma:.4f}  "
          f"vega={vega:.6f}  theta={theta:.6f}  F={und:.5f}")

# Snapshot au moment de l'ordre
entry_time  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
entry_bid   = bid
entry_ask   = ask
entry_mid   = round((bid + ask) / 2, 6) if bid and ask else None
entry_iv    = iv
entry_delta = delta
entry_F     = und

ib.cancelMktData(fop)

# Confirmer
print(f"\nOrdre : {ACTION} {QTY} x {fop.localSymbol} MKT")
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

trade = ib.placeOrder(fop, order)
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

# ── Positions avec greeks live ──
ib.sleep(1)
positions = ib.positions()

print(f"\n{'=' * 70}")
print("POSITIONS OUVERTES")
print(f"{'=' * 70}")

for p in positions:
    c = p.contract

    # Re-subscribe greeks sur chaque position
    tk = ib.reqMktData(c, "100", False, False)
    ib.sleep(4)

    g   = tk.modelGreeks
    p_iv    = g.impliedVol if g else None
    p_delta = g.delta      if g else None
    p_gamma = g.gamma      if g else None
    p_vega  = g.vega       if g else None
    p_theta = g.theta      if g else None
    p_und   = g.undPrice   if g else None
    p_bid   = tk.bid
    p_ask   = tk.ask
    p_mid   = round((p_bid + p_ask) / 2, 6) if p_bid and p_ask else None

    ib.cancelMktData(c)

    # P&L non réalisé (approximation mid)
    qty      = p.position
    avg_cost = p.avgCost / float(c.multiplier)  # ramené par unité
    pnl_mid  = round((p_mid - avg_cost) * qty * float(c.multiplier), 2) if p_mid else None

    print(f"\n  Symbole      : {c.localSymbol}")
    print(f"  Right/Strike : {c.right} K={c.strike}  Expiry={c.lastTradeDateOrContractMonth}")
    print(f"  Qty          : {qty:+.0f} contrat(s)")
    print(f"  ── Entrée ──────────────────────────────")
    print(f"  Heure entrée : {entry_time}")
    print(f"  Bid/Ask      : {entry_bid} / {entry_ask}  mid={entry_mid}")
    print(f"  Fill price   : {fill_price}")
    print(f"  IV entrée    : {entry_iv*100:.2f}%"   if entry_iv    else "  IV entrée    : --")
    print(f"  Delta entrée : {entry_delta:.4f}"      if entry_delta else "  Delta entrée : --")
    print(f"  F entrée     : {entry_F:.5f}"          if entry_F     else "  F entrée     : --")
    print(f"  ── Live ────────────────────────────────")
    print(f"  Bid/Ask live : {p_bid} / {p_ask}  mid={p_mid}")
    print(f"  IV live      : {p_iv*100:.2f}%"        if p_iv    else "  IV live      : --")
    print(f"  Delta live   : {p_delta:.4f}"           if p_delta else "  Delta live   : --")
    print(f"  Gamma live   : {p_gamma:.4f}"           if p_gamma else "  Gamma live   : --")
    print(f"  Vega live    : {p_vega:.6f}"            if p_vega  else "  Vega live    : --")
    print(f"  Theta live   : {p_theta:.6f}"           if p_theta else "  Theta live   : --")
    print(f"  F live       : {p_und:.5f}"             if p_und   else "  F live       : --")
    print(f"  ── P&L ─────────────────────────────────")
    print(f"  AvgCost/unit : {avg_cost:.6f}")
    print(f"  P&L mid      : {pnl_mid:+.2f} USD"     if pnl_mid else "  P&L mid      : --")

print(f"\n{'=' * 70}")
ib.disconnect()