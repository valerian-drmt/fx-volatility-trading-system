"""04 — Test ib-gateway (options chain EUR/CME EUU + ATM Greeks).

Smoke test du container ``fxvol-ib-gateway`` — étape 4/6. Valide que la
**chaîne d'options sur le front future EUR/USD** est accessible et que
les Greeks sont calculés par IB pour un strike ATM.

Couvre
------
1-4. Gates host (container, TCP, secrets, IBC login)
5. Front future EUR/USD CME identifié (réutilise la logique du 03)
6. ``reqSecDefOptParams()`` rend une chaîne sur la trading class **EUU**
   (futures options sur EUR/USD) avec ≥ 6 expiries futures
7. ``reqContractDetails(FOP ATM)`` qualifie un strike ATM (proche du
   spot) sur le front expiry — nécessaire pour avoir un conId valide
   avant de souscrire au market data
8. ``reqMktData(genericTickList="100")`` peuple ``modelGreeks`` en ≤ 10s
   avec ``impliedVol > 0`` ET ``-1 < delta < 1``

Pourquoi tradingClass EUU
-------------------------
Sur EUR/USD CME, plusieurs tradingClass coexistent :
- ``EUR`` — options classiques sur le contrat futures full-size
- ``EUU`` — options weekly + standard, c'est ce qu'on utilise dans le
  projet (tous les notebooks legacy ``vol_mid``, ``vol_fair`` partaient
  d'EUU). Plus de strikes, plus d'expiries, multipliers 62500/125000.

Pourquoi tickList "100"
-----------------------
Generic tick "100" demande à IB de pousser ``modelGreeks`` (Black-Scholes
implicite) en plus des cotations bid/ask. Sans ce flag, ``ticker.modelGreeks``
reste à None — on ne validerait que la cotation, pas le calcul Greeks.

Architecture en 2 passes (cf. ``01_test_connection.py``)
--------------------------------------------------------
Sections 1-4 sur l'host. Sections 5-8 spawnées dans un sub-process
``docker run --rm --network container:fxvol-ib-gateway``.

Préreq
------
- Container démarré avec ib-gateway healthy
- IBC loggé sur compte paper IB
- Secrets en env

Usage
-----
    python scripts/ib-gateway/04_test_options_chain.py

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

PORT = 4002
CLIENT_ID = 191            # sandbox, distinct des autres scripts (01=197, 02=195, 03=193)
CONTAINER = "fxvol-ib-gateway"

# Host pass tape sur 127.0.0.1 (port-forward Docker NAT). Bridge pass
# tape sur DNS `ib-gateway` depuis fxvol-internal — source IP = 172.19.0.X
# (couvert par TrustedIPs=127.0.0.1,172.19.0.0/24 persisté dans volume
# ib_gateway_jts). Path identique aux engines de prod.
HOST_FROM_HOST = "127.0.0.1"
HOST_FROM_DOCKER = "ib-gateway"
DOCKER_NETWORK = "fx-volatility-trading-system_fxvol-internal"

# Trading class à filtrer dans la chaîne d'options. EUU = options
# weekly + standard sur le contrat full-size EUR/USD (cf. notebooks
# legacy vol_mid/vol_fair).
TARGET_TRADING_CLASS = "EUU"

# Min expiries futures dans la chaîne pour considérer le check OK.
# IB pousse typiquement 12-24 expiries (weekly + monthly + quarterly).
MIN_EXPIRIES = 6

# Délai max pour peuplement du ticker ATM avec modelGreeks.
# Greeks = computation côté IB, plus lent que bid/ask brut. 10s = marge.
GREEKS_WAIT_S = 10.0

# Min DTE pour le front future (cf. 03).
MIN_DTE = 7

results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = "OK" if ok else "FAIL"
    print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")


def safe(val: float | None) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


# == 1-4. Gates host (réplique de 03) ==
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


def section_2_tcp_probe() -> None:
    print("\n== 2. TCP probe host -> 127.0.0.1:4002 ==")
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect((HOST_FROM_HOST, PORT))
        record("TCP connect 127.0.0.1:4002", True, f"{(time.perf_counter() - t0) * 1000:.1f} ms")
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        record("TCP connect 127.0.0.1:4002", False, f"{type(e).__name__}: {e}")
    finally:
        sock.close()


def section_3_secrets() -> None:
    print("\n== 3. secrets en env (length-only check) ==")
    for key in ("IB_USERID", "IB_PASSWORD", "VNC_PASSWORD"):
        val = os.environ.get(key, "")
        record(f"{key} set", bool(val), f"length = {len(val)}" if val else "MISSING")


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


# == 5. Front future EUR/USD CME (réutilise 03) ==
# Nécessaire avant tout, parce que reqSecDefOptParams a besoin du conId
# du sous-jacent pour rendre les chaînes pertinentes (les options sur
# EUR/USD sont indexées par futures expiry).
def section_5_front_future(ib: IB) -> Contract | None:
    print("\n== 5. front future EUR/USD CME ==")
    fut_template = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
    try:
        details = ib.reqContractDetails(fut_template)
    except Exception as e:
        record("reqContractDetails(FUT)", False, f"{type(e).__name__}: {e}")
        return None
    if not details:
        record("reqContractDetails(FUT) returned ≥ 1", False, "0 contracts")
        return None

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
        if (exp - now).days >= MIN_DTE:
            candidates.append(((exp - now).days, c))

    if not candidates:
        record("front future identifié", False, "aucun futur DTE ≥ MIN_DTE")
        return None

    candidates.sort(key=lambda x: x[0])
    front_dte, front = candidates[0]
    record("front future identifié", True,
           f"{front.localSymbol} DTE={front_dte} conId={front.conId}")
    return front


# == 6. Chaîne EUU avec ≥ 6 expiries (reqSecDefOptParams + fallback) ==
# Stratégie en 2 niveaux :
#
#   A. Tentative `reqSecDefOptParams("EUR", "CME", "FUT", conId)` — l'API
#      officielle IB pour récupérer la chaîne d'options. Renvoie idéalement
#      une liste de SecDefOptParam avec strikes et expiries déjà groupés.
#      MAIS connue pour rendre 0 silencieusement sur paper account / off-
#      hours / cache pas warmé.
#
#   B. Fallback `reqContractDetails(FOP partial)` avec tradingClass=EUU —
#      brute force qui récupère TOUS les contrats FOP matching, dont on
#      extrait nous-mêmes les expiries/strikes uniques. Plus de bande
#      passante (peut rendre 1000+ contrats), mais marche toujours.
#
# Le legacy notebook vol_mid utilisait A. Le smoke test prend les deux
# en cascade pour être robuste.
def section_6_options_chain(ib: IB, front_fut: Contract) -> tuple[list[str], list[float]] | None:
    print(f"\n== 6. options chain (tradingClass={TARGET_TRADING_CLASS}) ==")

    ib.sleep(1.0)  # laisse IB warmer son cache après le reqContractDetails du §5

    # --- A. reqSecDefOptParams ---
    expiries: set[str] = set()
    strikes: set[float] = set()
    multipliers: set[str] = set()
    method_used = ""

    try:
        chains = ib.reqSecDefOptParams(
            front_fut.symbol, "CME", front_fut.secType, front_fut.conId
        )
        print(f"  [INFO] reqSecDefOptParams → {len(chains)} entries")
        for ch in chains:
            if ch.tradingClass != TARGET_TRADING_CLASS:
                continue
            expiries.update(ch.expirations)
            strikes.update(ch.strikes)
            multipliers.add(str(ch.multiplier))
        if expiries:
            method_used = "reqSecDefOptParams"
    except Exception as e:
        print(f"  [INFO] reqSecDefOptParams raised {type(e).__name__}: {e}")

    # --- B. Fallback reqContractDetails sur FOP partial ---
    if not expiries:
        print("  [INFO] reqSecDefOptParams vide, fallback sur reqContractDetails(FOP)")
        fop_template = Contract(
            symbol=front_fut.symbol, secType="FOP",
            exchange="CME", currency="USD",
            tradingClass=TARGET_TRADING_CLASS,
        )
        try:
            fop_details = ib.reqContractDetails(fop_template)
        except Exception as e:
            print(f"  [INFO] reqContractDetails(FOP) raised {type(e).__name__}: {e}")
            fop_details = []

        print(f"  [INFO] reqContractDetails(FOP) → {len(fop_details)} contracts")
        for d in fop_details:
            c = d.contract
            if c.lastTradeDateOrContractMonth:
                expiries.add(c.lastTradeDateOrContractMonth)
            if c.strike:
                strikes.add(float(c.strike))
            if c.multiplier:
                multipliers.add(str(c.multiplier))
        if expiries:
            method_used = "reqContractDetails(FOP)"

    record(f"chain via {method_used or 'aucune méthode'}", bool(expiries),
           f"{len(expiries)} expiries, {len(strikes)} strikes, multipliers={sorted(multipliers)}"
           if expiries else "ni reqSecDefOptParams ni reqContractDetails n'ont rendu de chaîne")
    if not expiries:
        return None

    expiries_sorted = sorted(expiries)
    strikes_sorted = sorted(strikes)
    record(f"≥ {MIN_EXPIRIES} expiries futures", len(expiries) >= MIN_EXPIRIES,
           f"first 3: {expiries_sorted[:3]}")

    if not expiries:
        return None
    return expiries_sorted, strikes_sorted


# == 7. Qualifier un FOP ATM sur le front expiry ==
# IB exige un conId valide avant qu'on puisse souscrire au market data.
# On prend le front expiry (premier de la chaîne) + le strike le plus
# proche du spot. On essaie chaque multiplier disponible jusqu'à ce
# qu'un contrat FOP soit qualifié.
def section_7_qualify_atm_fop(ib: IB, front_fut: Contract,
                              chain: tuple[list[str], list[float]]) -> Contract | None:
    print("\n== 7. qualifier FOP ATM sur front expiry ==")
    expiries, strikes = chain
    front_expiry = expiries[0]

    # Spot du future = mid bid/ask, ou fallback sur close.
    ib.reqMarketDataType(3)
    fut_ticker = ib.reqMktData(front_fut, "", False, False)
    deadline = time.perf_counter() + 4.0
    while time.perf_counter() < deadline:
        ib.sleep(0.2)
        if any(safe(v) is not None for v in (fut_ticker.bid, fut_ticker.ask, fut_ticker.last, fut_ticker.close)):
            break
    bid, ask = safe(fut_ticker.bid), safe(fut_ticker.ask)
    spot = ((bid + ask) / 2) if (bid is not None and ask is not None) else (
        safe(fut_ticker.last) or safe(fut_ticker.close))
    ib.cancelMktData(front_fut)
    ib.sleep(0.2)

    if spot is None:
        record("front future spot price", False, "aucune cotation FUT pour calculer ATM")
        return None
    record("front future spot price", True, f"{spot:.5f}")

    # Strike le plus proche du spot.
    atm_strike = min(strikes, key=lambda k: abs(k - spot))
    record("ATM strike calculé", True, f"K={atm_strike} (|K-spot|={abs(atm_strike-spot):.4f})")

    # Essai séquentiel des multipliers connus pour EUU (62500, 125000).
    # IB rejette silencieusement (rend [] sur reqContractDetails) si la
    # combinaison strike/multiplier/expiry ne correspond à rien.
    for mult in ("62500", "125000"):
        for right in ("C", "P"):
            fop = Contract(
                symbol=front_fut.symbol, secType="FOP",
                exchange="CME", currency="USD",
                lastTradeDateOrContractMonth=front_expiry,
                strike=atm_strike, right=right, multiplier=mult,
                tradingClass=TARGET_TRADING_CLASS,
            )
            try:
                qualif = ib.reqContractDetails(fop)
            except Exception:
                continue
            if qualif:
                contract = qualif[0].contract
                record(f"FOP ATM qualifié ({right}, mult={mult})", True,
                       f"localSymbol={contract.localSymbol} conId={contract.conId}")
                return contract

    record("FOP ATM qualifié", False,
           f"aucune combinaison (mult, C/P) ne match pour expiry={front_expiry} K={atm_strike}")
    return None


# == 8. reqMktData(genericTickList="100") + modelGreeks ==
# Tick 100 = "Option Volume" mais surtout débloque la pousseé périodique
# des Greeks calculés par IB (impliedVol, delta, gamma, theta, vega).
# On poll 10s et on valide impliedVol > 0 et |delta| ∈ ]0, 1[.
def section_8_atm_greeks(ib: IB, fop: Contract) -> None:
    print("\n== 8. reqMktData ATM avec modelGreeks ==")
    ticker = ib.reqMktData(fop, "100", False, False)

    deadline = time.perf_counter() + GREEKS_WAIT_S
    while time.perf_counter() < deadline:
        ib.sleep(0.3)
        greeks = ticker.modelGreeks
        if greeks and safe(greeks.impliedVol) is not None:
            break

    greeks = ticker.modelGreeks
    if greeks is None:
        record(f"modelGreeks reçus en ≤ {GREEKS_WAIT_S}s", False, "modelGreeks=None")
        ib.cancelMktData(fop)
        return

    iv = safe(greeks.impliedVol)
    delta = safe(greeks.delta)

    record(f"modelGreeks reçus en ≤ {GREEKS_WAIT_S}s", iv is not None,
           f"iv={iv} delta={delta}")
    if iv is not None:
        record("impliedVol > 0", iv > 0, f"iv={iv:.4%}" if iv > 0 else f"iv={iv}")
    if delta is not None:
        cohérent = -1.0 < delta < 1.0
        record("delta ∈ ]−1, 1[", cohérent, f"delta={delta:.4f}")

    ib.cancelMktData(fop)
    ib.sleep(0.2)


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
    print("=== BRIDGE PASS (inside fxvol-internal Docker network) ===")
    print(f"target = {HOST_FROM_DOCKER}:{PORT}, clientId = {CLIENT_ID}\n")
    ib = IB()
    try:
        ib.connect(HOST_FROM_DOCKER, PORT, clientId=CLIENT_ID, timeout=15)
        record("ib.connect()", ib.isConnected(),
               f"serverVersion=v{ib.client.serverVersion()}")
    except Exception as e:
        record("ib.connect()", False, f"{type(e).__name__}: {e}")
        return _print_summary("[bridge] ")

    try:
        front_fut = section_5_front_future(ib)
        if front_fut is None:
            print("\n  [SKIP] sections 6/7/8 (pas de front future)")
        else:
            chain = section_6_options_chain(ib, front_fut)
            if chain is None:
                print("\n  [SKIP] sections 7/8 (chaîne EUU vide)")
            else:
                fop = section_7_qualify_atm_fop(ib, front_fut, chain)
                if fop is None:
                    print("\n  [SKIP] section 8 (FOP non qualifié)")
                else:
                    section_8_atm_greeks(ib, fop)
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass

    return _print_summary("[bridge] ")


def _run_host_pass() -> int:
    print("=== HOST PASS (Windows / Docker NAT) ===")
    print(f"target = {HOST_FROM_HOST}:{PORT}\n")

    section_1_container()
    section_2_tcp_probe()
    section_3_secrets()
    ibc_ready = section_4_ibc_login()

    host_fail = _print_summary("[host] ")

    if not ibc_ready:
        print("\n  [SKIP] bridge pass (IBC pas loggé)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/smoke/ib-gateway/04_test_options_chain.py"
    print(f"\n  [INFO] sections 5-8 tournent sur le réseau {DOCKER_NETWORK}")
    print("         (même path que les engines de prod ; source IP dans 172.19.0.0/24)")
    print("         Spawning docker run...\n")

    cmd = [
        "docker", "run", "--rm",
        "--network", DOCKER_NETWORK,
        "-v", f"{repo_root}:/work",
        "-w", "/work",
        "python:3.11-slim",
        "sh", "-c",
        f"pip install -q ib_insync tzdata && python /work/{rel_script} --namespace",
    ]
    result = subprocess.run(cmd)
    return host_fail + (0 if result.returncode == 0 else result.returncode)


def main() -> int:
    if "--namespace" in sys.argv:
        return _run_namespace_pass()
    return _run_host_pass()


# Troubleshooting cheat sheet
# ============================
# | Symptôme                                         | Cause probable                              | Fix                                                                       |
# |--------------------------------------------------|---------------------------------------------|---------------------------------------------------------------------------|
# | reqSecDefOptParams returned 0                    | conId du future invalide                    | Vérifier section 5 — front future correctement résolu                     |
# | tradingClass EUU absent                          | Compte sans entitlement options EUR/CME     | Activer market data CME options sur portail IB (peut être gratuit paper)  |
# | < 6 expiries futures                             | Chaîne en transition après expiry mensuelle | Bumper MIN_EXPIRIES à 3, ou attendre quelques jours                       |
# | front future spot price FAIL                     | Marché fermé ET aucun close récent          | Comportement normal week-end long ; re-tester en heures de marché         |
# | FOP ATM qualifié FAIL pour toutes combinaisons   | Multiplier non-standard, ou strike inactif   | Inspecter manuellement: ib.reqContractDetails(fop) avec print du contrat   |
# | modelGreeks reçus FAIL                           | tickList "100" non honoré                   | Compte sans flux Greeks ; vérifier "Stream model based bid/ask" dans IB   |
# | impliedVol = 0 ou negative                       | Option deep ITM/OTM ou expirée              | Choisir un autre strike ATM (le smoke fait déjà ça) ou autre expiry      |

if __name__ == "__main__":
    sys.exit(main())
