"""Helper de seed Redis pour les smoke tests risk-engine.

Sans market-data ni vol-engine fonctionnels, risk-engine ne peut pas
récupérer les inputs (`latest_spot:EURUSD`, `latest_vol_surface:EURUSD`)
et son cycle skip silencieusement. Ce script seed les 2 clés au bon
format pour qu'on puisse valider la chaîne risk-engine → Redis → WS
indépendamment des engines amont.

Usage :
    python scripts/risk/_seed_redis.py

Le seed expire après 600s (10 min), largement suffisant pour run un
smoke notebook. Re-lancer le script si tu attends > 10 min entre 2 runs.

Pourquoi un .py et pas un redis-cli inline : PowerShell mange les
guillemets autour des keys JSON quand on passe du JSON imbriqué via
``redis-cli SET`` en command-line. En écrivant via ``redis-py`` depuis
un .py, on contourne tout le quoting hell de PowerShell.
"""
import json
import sys

import redis

REDIS_URL = "redis://localhost:6380/0"
SYMBOL = "EURUSD"
TTL_S = 600

# Format spot accepté par risk-engine après le patch sandbox R9 :
# soit dict {"mid":..., "bid":..., "ask":...}, soit plain float string.
# On utilise le dict pour être explicite.
SPOT = {"mid": 1.17, "bid": 1.169, "ask": 1.171}

# Format surface attendu par risk-engine `_read_surface` :
# wrapper avec clé "surface" qui contient un dict {tenor: {label: {iv, strike}}}.
SURFACE = {
    "timestamp": "2026-04-28T15:00:00Z",
    "surface": {
        "1M": {
            "atm": {"iv": 0.07, "strike": 1.17},
            "25dc": {"iv": 0.072, "strike": 1.18},
            "25dp": {"iv": 0.075, "strike": 1.16},
        },
        "3M": {
            "atm": {"iv": 0.078, "strike": 1.17},
        },
    },
}


def main() -> int:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    if not r.ping():
        print("Redis ping FAIL — check 'docker compose ps'", file=sys.stderr)
        return 1

    r.set(f"latest_spot:{SYMBOL}", json.dumps(SPOT), ex=TTL_S)
    print(f"  seeded latest_spot:{SYMBOL} (TTL {TTL_S}s)")

    r.set(f"latest_vol_surface:{SYMBOL}", json.dumps(SURFACE), ex=TTL_S)
    print(f"  seeded latest_vol_surface:{SYMBOL} (TTL {TTL_S}s)")

    # Sanity check : on relit ce qu'on a écrit pour vérifier qu'on n'a
    # pas un problème d'encoding bizarre côté Redis.
    spot_back = json.loads(r.get(f"latest_spot:{SYMBOL}"))
    surface_back = json.loads(r.get(f"latest_vol_surface:{SYMBOL}"))
    assert spot_back == SPOT, "spot roundtrip mismatch"
    assert surface_back == SURFACE, "surface roundtrip mismatch"
    print("  ✓ roundtrip JSON validé")
    return 0


if __name__ == "__main__":
    sys.exit(main())
