"""03 — Test ib-gateway (market data front future EUR/CME).

Smoke test du container ``fxvol-ib-gateway`` — étape 3/6. Valide que
**la surface market data IB répond** : on résout le contrat du front
future EUR/USD sur CME, on souscrit au tick en delayed (mode 3, marche
même marché fermé), on reçoit bid/ask/last/close cohérents, on cancel
proprement.

Couvre
------
1. Container UP + healthcheck Docker (gate)
2. TCP probe direct sur 127.0.0.1:4002 depuis l'host (gate)
3. Secrets en env (length-only, JAMAIS la valeur)
4. IBC login complété sur les serveurs IB (gate)
5. ``reqContractDetails(EUR FUT CME)`` rend ≥ 1 contrat avec un front
   future (DTE ≥ 7) identifiable par ``localSymbol`` et expiration
6. ``reqMktData(front_future)`` avec ``reqMarketDataType(3)`` (delayed)
   peuple le ticker en < 5s avec au moins une valeur parmi bid/ask/last/close
7. Sanity check du prix : la référence (premier non-null parmi bid/ask/last/close)
   doit être dans la fenêtre EUR/USD typique [0.5, 2.0]
8. ``cancelMktData()`` propre, pas d'exception

Pourquoi delayed (mode 3) plutôt que live (mode 1)
--------------------------------------------------
Le compte paper IB n'a pas systématiquement les entitlements market data
live (selon configuration). Le mode 3 (delayed) marche **toujours** sans
entitlement et est suffisant pour valider que le pipe IB → Gateway → Python
fonctionne. La logique de prod (engines vol/risk) utilise live en pas
delayed, mais le smoke valide juste la surface, pas la latence.

Architecture en 2 passes (cf. ``01_test_connection.py``)
--------------------------------------------------------
Sections 1-4 sur l'host. Sections 5-8 spawnées dans un sub-process
``docker run --rm --network container:fxvol-ib-gateway``.

Préreq
------
- Container démarré : ``docker compose --profile ib up -d ib-gateway``
- IBC login terminé
- Secrets en env : ``.\\scripts\\load_secrets.ps1``

Usage
-----
    python scripts/ib-gateway/03_test_market_data.py        # mode normal
    python scripts/ib-gateway/03_test_market_data.py --namespace  # interne, auto-spawné

Sortie : 2 tableaux OK/FAIL (host + namespace) + exit code = nb FAILs.
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ib_insync import IB, Contract

HOST = "127.0.0.1"
PORT = 4002
CLIENT_ID = 193            # sandbox, distinct des autres scripts (01=197, 02=195)
CONTAINER = "fxvol-ib-gateway"

# Délai max accepté pour que le ticker se peuple après reqMktData.
# Empirique : 1-2s typique en delayed, 5s = marge confortable.
TICKER_WAIT_S = 5.0

# Fenêtre EUR/USD raisonnable pour le sanity check. Le prix d'un FUT EUR
# sur CME suit le taux spot EUR/USD, donc historiquement 0.85-1.60.
# 0.5-2.0 = marge anti-fausse-alarme.
PRICE_MIN = 0.5
PRICE_MAX = 2.0

# Min DTE pour considérer un FUT comme "front" — on évite les expiries
# qui sont déjà en règlement (DTE < 7 = trading thin, prix non fiables).
MIN_DTE = 7

results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = "OK" if ok else "FAIL"
    print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")


def safe(val: float | None) -> float | None:
    """ib_insync renvoie NaN pour les valeurs non-set ; on normalise."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


# == 1. Container UP + healthcheck Docker ==
def section_1_container() -> None:
    print("\n== 1. container state + healthcheck ==")
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER],
        capture_output=True, text=True,
    )
    state = out.stdout.strip()
    record("docker container state", state == "running", state or "<not found>")

    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Health.Status}}", CONTAINER],
        capture_output=True, text=True,
    )
    health = out.stdout.strip()
    record("docker healthcheck", health == "healthy", health or "<no healthcheck>")


# == 2. TCP probe ==
def section_2_tcp_probe() -> None:
    print("\n== 2. TCP probe host -> 127.0.0.1:4002 ==")
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect((HOST, PORT))
        dt_ms = (time.perf_counter() - t0) * 1000
        record("TCP connect 127.0.0.1:4002", True, f"{dt_ms:.1f} ms")
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        record("TCP connect 127.0.0.1:4002", False, f"{type(e).__name__}: {e}")
    finally:
        sock.close()


# == 3. Secrets ==
def section_3_secrets() -> None:
    print("\n== 3. secrets en env (length-only check) ==")
    for key in ("IB_USERID", "IB_PASSWORD", "VNC_PASSWORD"):
        val = os.environ.get(key, "")
        record(f"{key} set", bool(val), f"length = {len(val)}" if val else "MISSING")


# == 4. IBC login ==
def section_4_ibc_login() -> bool:
    print("\n== 4. IBC login status (docker logs) ==")
    out = subprocess.run(
        ["docker", "logs", "--tail", "200", CONTAINER],
        capture_output=True, text=True,
    )
    logs = (out.stdout + out.stderr).lower()
    has_login = "login has completed" in logs
    record("IBC login completed", has_login,
           "marker found" if has_login else "marker absent — IBC pas encore loggé")
    return has_login


# == 5. reqContractDetails — front future EUR/CME ==
# Ce que tu dois voir : ≥ 1 contrat retourné, et au moins un avec DTE ≥ 7
# (= un futur dont la date d'expiration est dans le futur, pas en règlement).
# `localSymbol` du front identifie le contrat exact (ex: "6EM6" pour
# EUR juin 2026). On garde la référence pour les sections suivantes.
def section_5_contract_details(ib: IB) -> Contract | None:
    print("\n== 5. reqContractDetails(EUR FUT CME) ==")
    fut_template = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")

    try:
        details = ib.reqContractDetails(fut_template)
    except Exception as e:
        record("reqContractDetails() call", False, f"{type(e).__name__}: {e}")
        return None

    record("reqContractDetails returned ≥ 1", bool(details),
           f"{len(details)} contract(s)")
    if not details:
        return None

    # Filtre : on garde les futures dont l'expiration est ≥ MIN_DTE jours
    # dans le futur. Tri ascendant par DTE → le premier = front.
    now = datetime.now(UTC).replace(tzinfo=None)
    candidates = []
    for d in details:
        c = d.contract
        exp_str = c.lastTradeDateOrContractMonth
        try:
            exp = (datetime.strptime(exp_str, "%Y%m%d") if len(exp_str) == 8
                   else datetime.strptime(exp_str, "%Y%m"))
        except ValueError:
            continue
        dte = (exp - now).days
        if dte >= MIN_DTE:
            candidates.append((dte, c))

    if not candidates:
        record(f"front future (DTE ≥ {MIN_DTE})", False,
               f"{len(details)} contracts but none with DTE ≥ {MIN_DTE}")
        return None

    candidates.sort(key=lambda x: x[0])
    front_dte, front = candidates[0]
    record(f"front future (DTE ≥ {MIN_DTE})", True,
           f"{front.localSymbol} expires={front.lastTradeDateOrContractMonth} DTE={front_dte}")
    return front


# == 6. reqMktData — peuplement du ticker ==
# Ce que tu dois voir : après reqMarketDataType(3) + reqMktData(), au
# moins une valeur non-null parmi bid/ask/last/close arrive en < 5s.
# Marché ouvert → bid/ask/last vivants. Marché fermé (weekend, soir) →
# seul close du dernier jour de session sera populé. Les deux sont OK.
def section_6_market_data(ib: IB, contract: Contract) -> tuple[float | None, float | None, float | None, float | None]:
    print("\n== 6. reqMktData (delayed mode 3) ==")
    ib.reqMarketDataType(3)

    ticker = ib.reqMktData(contract, "", False, False)

    # Polling : on attend qu'au moins une des 4 valeurs apparaisse, ou
    # le timeout. ib_insync met à jour `ticker` in-place via callbacks.
    t0 = time.perf_counter()
    deadline = t0 + TICKER_WAIT_S
    while time.perf_counter() < deadline:
        ib.sleep(0.2)
        if any(safe(v) is not None for v in (ticker.bid, ticker.ask, ticker.last, ticker.close)):
            break

    elapsed = time.perf_counter() - t0
    bid, ask, last, close = (safe(ticker.bid), safe(ticker.ask),
                             safe(ticker.last), safe(ticker.close))

    has_data = any(v is not None for v in (bid, ask, last, close))
    record(f"ticker populé en ≤ {TICKER_WAIT_S}s", has_data,
           f"{elapsed:.2f}s — bid={bid} ask={ask} last={last} close={close}")
    return bid, ask, last, close


# == 7. Sanity check du prix ==
# Le FUT EUR/USD sur CME suit le spot EUR/USD. Historique : ~0.85-1.60.
# Notre fenêtre [0.5, 2.0] catche tout sauf un prix manifestement faux
# (0, négatif, NaN, valeur en cents au lieu de devise…).
def section_7_price_sanity(prices: tuple[float | None, ...]) -> None:
    print("\n== 7. sanity check prix EUR/USD futures ==")
    ref = next((p for p in prices if p is not None), None)
    if ref is None:
        record("sanity prix", False, "aucun prix capturé")
        return
    cohérent = PRICE_MIN < ref < PRICE_MAX
    record(f"prix dans [{PRICE_MIN}, {PRICE_MAX}]", cohérent,
           f"ref={ref:.5f}" + ("" if cohérent else " (HORS FENÊTRE)"))


# == 8. cancelMktData — pas de leak de subscription ==
# Ce que tu dois voir : cancel sans exception. ib_insync ne renvoie
# pas de confirmation explicite ; un cancel "silencieux" = OK. Si une
# exception sort, c'est qu'on a fait le cancel sur un ticker non-souscrit
# (bug logique côté script).
def section_8_cancel(ib: IB, contract: Contract) -> None:
    print("\n== 8. cancelMktData ==")
    try:
        ib.cancelMktData(contract)
        ib.sleep(0.3)
        record("cancelMktData() — no exception", True, "subscription cancelled")
    except Exception as e:
        record("cancelMktData()", False, f"{type(e).__name__}: {e}")


def _print_summary(prefix: str = "") -> int:
    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{prefix}{'LABEL':<55} STATUS  DETAIL")
    print("-" * 110)
    for label, ok, detail in results:
        sym = "OK" if ok else "FAIL"
        print(f"{label:<55} {sym:<6}  {detail}")
    print("-" * 110)
    print(f"\n{prefix}{n_ok} OK / {n_fail} FAIL  ({len(results)} total)")
    return n_fail


def _run_namespace_pass() -> int:
    print("=== NAMESPACE PASS (inside ib-gateway network) ===")
    print(f"target = {HOST}:{PORT}, clientId = {CLIENT_ID}\n")
    ib = IB()
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
        record("ib.connect()", ib.isConnected(),
               f"serverVersion=v{ib.client.serverVersion()}")
    except Exception as e:
        record("ib.connect()", False, f"{type(e).__name__}: {e}")
        return _print_summary("[namespace] ")

    try:
        contract = section_5_contract_details(ib)
        if contract is None:
            print("\n  [SKIP] sections 6/7/8 (pas de contrat front)")
        else:
            prices = section_6_market_data(ib, contract)
            section_7_price_sanity(prices)
            section_8_cancel(ib, contract)
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    return _print_summary("[namespace] ")


def _run_host_pass() -> int:
    print("=== HOST PASS (Windows / Docker NAT) ===")
    print(f"target = {HOST}:{PORT}\n")

    section_1_container()
    section_2_tcp_probe()
    section_3_secrets()
    ibc_ready = section_4_ibc_login()

    host_fail = _print_summary("[host] ")

    if not ibc_ready:
        print("\n  [SKIP] namespace pass (IBC pas loggé, sections 5+ inutiles)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/ib-gateway/03_test_market_data.py"
    print("\n  [INFO] sections 5-8 doivent tourner depuis le namespace ib-gateway")
    print("         Spawning docker run avec network namespace partagé...\n")

    cmd = [
        "docker", "run", "--rm",
        "--network", f"container:{CONTAINER}",
        "-v", f"{repo_root}:/work",
        "-w", "/work",
        "python:3.11-slim",
        "sh", "-c",
        f"pip install -q ib_insync && python /work/{rel_script} --namespace",
    ]
    result = subprocess.run(cmd)
    return host_fail + (0 if result.returncode == 0 else result.returncode)


def main() -> int:
    if "--namespace" in sys.argv:
        return _run_namespace_pass()
    return _run_host_pass()


# Troubleshooting cheat sheet
# ============================
# | Symptôme                                       | Cause probable                              | Fix                                                                          |
# |------------------------------------------------|---------------------------------------------|------------------------------------------------------------------------------|
# | reqContractDetails returned 0                  | Symbole/exchange/currency mal spécifiés     | Vérifier que symbol="EUR", secType="FUT", exchange="CME", currency="USD"     |
# | front future (DTE ≥ 7) FAIL                    | Tous les contrats < 7 DTE (transition)       | Bumper MIN_DTE à 1, ou attendre la mise en place de l'expiry suivante         |
# | ticker populé FAIL en delayed                  | reqMarketDataType(3) pas accepté ou perm     | Vérifier les market data permissions du compte sur portail IB                 |
# | bid/ask=None mais close OK (weekend)           | Marché fermé, seuls close du last jour OK    | Comportement normal weekend / off-hours, le smoke OK                          |
# | sanity prix HORS FENÊTRE                       | Mauvais contrat (pas EUR/USD ?), bug pricing | Inspecter manuellement: ib.reqMktData(contract) puis ticker.bid/ask           |
# | cancelMktData exception                        | Cancel sur un ticker pas souscrit           | Bug logique du script — vérifier que reqMktData a bien été appelé avant       |

if __name__ == "__main__":
    sys.exit(main())
