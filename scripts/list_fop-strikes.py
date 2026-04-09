"""
list_fop_strikes.py
Liste tous les strikes disponibles pour une expiry donnée sur FOP EUR/USD (EUU).
"""
from ib_insync import IB, Contract, util

util.patchAsyncio()

PORT      = 4002
CLIENT_ID = 17

EXPIRY = "20260508"  # choisis l'expiry qui t'intéresse

ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)

fop = Contract()
fop.symbol       = "EUR"
fop.secType      = "FOP"
fop.exchange     = "CME"
fop.currency     = "USD"
fop.tradingClass = "EUU"
fop.lastTradeDateOrContractMonth = EXPIRY

print(f"Requête strikes pour EUU expiry {EXPIRY}...")
details = ib.reqContractDetails(fop)
print(f"{len(details)} contrats trouvés.\n")

# Extraire strikes uniques, triés
strikes = sorted({d.contract.strike for d in details})

# Séparer calls et puts disponibles par strike
calls = {d.contract.strike for d in details if d.contract.right == "C"}
puts  = {d.contract.strike for d in details if d.contract.right == "P"}

print(f"{'STRIKE':>10}  {'CALL':>5}  {'PUT':>5}")
print("-" * 25)
for k in strikes:
    c = "C" if k in calls else ""
    p = "P" if k in puts  else ""
    print(f"{k:>10.4f}  {c:>5}  {p:>5}")

print(f"\n{len(strikes)} strikes disponibles")
print(f"Range : {strikes[0]:.4f} — {strikes[-1]:.4f}")

ib.disconnect()