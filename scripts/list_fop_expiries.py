"""
list_fop_expiries.py
Liste toutes les expiries disponibles pour les options FOP EUR/USD (EUU) sur CME,
avec le tenor relatif (1M, 2M, ... 1Y, 2Y).
"""
from datetime import datetime
from ib_insync import IB, Contract, util

util.patchAsyncio()

PORT      = 4002
CLIENT_ID = 16

ib = IB()
ib.connect("127.0.0.1", PORT, clientId=CLIENT_ID)

fop = Contract()
fop.symbol       = "EUR"
fop.secType      = "FOP"
fop.exchange     = "CME"
fop.currency     = "USD"
fop.tradingClass = "EUU"

print("Requête contractDetails FOP EUU en cours...")
details = ib.reqContractDetails(fop)
print(f"{len(details)} contrats trouvés.\n")

expiries = sorted({d.contract.lastTradeDateOrContractMonth for d in details})

today = datetime.now()

def tenor_label(expiry_str: str) -> str:
    exp = datetime.strptime(expiry_str, "%Y%m%d")
    delta = exp - today
    days = delta.days
    months = round(days / 30.44)
    if months < 1:
        return f"{days}D"
    elif months < 12:
        return f"{months}M"
    else:
        years = months // 12
        remainder = months % 12
        if remainder == 0:
            return f"{years}Y"
        return f"{years}Y{remainder}M"

print(f"{'EXPIRY':<12} {'TENOR':<8} {'DAYS':>6}")
print("-" * 30)
for e in expiries:
    exp_date = datetime.strptime(e, "%Y%m%d")
    days = (exp_date - today).days
    label = tenor_label(e)
    print(f"{e:<12} {label:<8} {days:>6}d")

ib.disconnect()