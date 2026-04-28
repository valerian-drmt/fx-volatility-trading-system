"""06 — Test ib-gateway (security surface : VNC bind + paper order placement).

Smoke test du container ``fxvol-ib-gateway`` — étape 6/6. Valide les
deux faces de la "surface de sécurité" du container :

1. **Boundary réseau** — les ports `4002` (TWS API) et `5900` (VNC) sont
   bind strictement sur `127.0.0.1` côté host, pas exposés au LAN.
2. **Order placement réel sur paper** — la chaîne `placeOrder` →
   callbacks IB → `cancelOrder` fonctionne bout-à-bout pour FUT et FOP,
   ce qui valide la même surface que vont utiliser vol-engine et
   risk en prod.

Couvre
------
1-4. Gates host (container, TCP, secrets, IBC login)
5. **VNC + API bind security** — `docker port` montre `127.0.0.1:4002`
   et `127.0.0.1:5900`, pas `0.0.0.0:*`. Defense in depth contre une
   exposition accidentelle au LAN.
6. **Paper FUT MKT order** — `placeOrder(MKT 1 lot)` sur le front future
   EUR/USD CME, attente du callback orderStatus, `cancelOrder`. Outcomes
   acceptables : `Cancelled` ou `Filled` (paper IB remplit les MKT quasi-
   instantanément, race condition sur le cancel — pas un fail).
7. **Paper FOP LMT loin du marché** — `placeOrder(LMT 1 lot)` à un prix
   limite ≪ marché (0.0001) → 0% chance de fill → `cancelOrder` clean,
   status final = `Cancelled` strict.

Pourquoi pas de test READ_ONLY_API toggle
------------------------------------------
Le projet tourne toujours avec ``READ_ONLY_API=no`` en prod (compose) et
``readonly=False`` côté client. Tester le rejection en mode readonly
serait tester un comportement qu'on n'entre jamais. La validation que les
ordres readonly sont rejetés appartient à un test de release ad-hoc, pas
au smoke quotidien.

Architecture en 2 passes
------------------------
Sections 1-5 sur l'host (incluant le VNC bind check qui n'a pas besoin
d'API call). Sections 6-7 spawnées dans un sub-process docker run avec
network namespace partagé d'ib-gateway.

Préreq
------
- Container démarré avec ib-gateway healthy
- IBC loggé sur compte paper (préfixe ``DU``)
- Secrets en env

Usage
-----
    python scripts/ib-gateway/06_test_security_surface.py

Notes paper account
-------------------
La section 6 peut **filler** l'ordre MKT avant qu'on ait le temps de
cancel (paper IB simule un fill instantané sur les MKT futures). Si
ça arrive, le compte paper se retrouve avec une position résiduelle
(1 lot long sur le front EUR FUT CME). Pas un fail du smoke — le test
print un WARN et te suggère le reset paper sur portail IB si nécessaire.
La section 7 ne peut pas filler (limit price 0.0001 << marché 1.16).

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

from ib_insync import IB, Contract, LimitOrder, MarketOrder

PORT = 4002
CLIENT_ID = 184            # sandbox, distinct des autres scripts
CONTAINER = "fxvol-ib-gateway"

# Host pass tape sur 127.0.0.1 (port-forward Docker NAT). Bridge pass
# tape sur DNS `ib-gateway` depuis fxvol-internal — source IP = 172.19.0.X
# (couvert par TrustedIPs=127.0.0.1,172.19.0.0/24 persisté dans volume
# ib_gateway_jts). Path identique aux engines de prod.
HOST_FROM_HOST = "127.0.0.1"
HOST_FROM_DOCKER = "ib-gateway"
DOCKER_NETWORK = "fx-volatility-trading-system_fxvol-internal"

# Délai max pour qu'IB pousse un orderStatus après placeOrder/cancelOrder.
# Empirique : 0.5-1s typique en paper, 2s = marge.
ORDER_CALLBACK_WAIT_S = 2.0

# Min DTE pour le front future (cf. 03).
MIN_DTE = 7

# Limit price pour la section 7 — volontairement très loin du spot
# pour garantir qu'aucun fill ne survient avant le cancel.
LMT_FAR_PRICE = 0.0001

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


# == 1-4. Gates host (réplique) ==
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
           "marker found" if has_login else "marker absent")
    return has_login


# == 5. Bind security (host pass) ==
# Ce que tu dois voir : `docker port` retourne `127.0.0.1:4002` et
# `127.0.0.1:5900` — strictement loopback. Si jamais tu vois `0.0.0.0:*`
# ou `:::* `, le port est exposé au LAN entier — gros trou de sécurité.
#
# Defense in depth : le compose binde explicitement `127.0.0.1:PORT:PORT`
# (cf. docker-compose.yml § ib-gateway), ce test garantit que la config
# est respectée par le runtime Docker.
def section_5_bind_security() -> None:
    print("\n== 5. ports bind security (4002 API + 5900 VNC) ==")
    for internal_port in ("4002", "5900"):
        out = subprocess.run(
            ["docker", "port", CONTAINER, internal_port],
            capture_output=True, text=True,
        )
        binding = out.stdout.strip()  # ex: "127.0.0.1:4002"
        if not binding:
            record(f"port {internal_port} bind", False,
                   "docker port returned empty (port not published)")
            continue
        # On accepte uniquement 127.0.0.1:* — pas 0.0.0.0, pas IP LAN.
        loopback_only = all(
            line.startswith("127.0.0.1:") for line in binding.splitlines()
        )
        record(f"port {internal_port} bind sur 127.0.0.1 uniquement",
               loopback_only, binding.replace("\n", " | "))


# == Helper namespace : front future ==
def _front_future(ib: IB) -> Contract | None:
    fut_template = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
    details = ib.reqContractDetails(fut_template)
    if not details:
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
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# == 6. Paper FUT MKT order — placement + cancel ==
# Ce que tu dois voir : `placeOrder` rend un Trade, le orderStatus
# transite via Submitted (ou Filled si paper IB simule trop vite),
# `cancelOrder` propage le state, status final = Cancelled OU Filled.
#
# Le cas "Filled avant cancel" est connu en paper IB sur les MKT FUT
# liquides — c'est juste une race condition pas un fail. On track la
# position résiduelle dans ce cas et on print un WARN.
def section_6_fut_mkt_order(ib: IB) -> tuple[Contract, float] | None:
    """Returns (front_fut_contract, reference_price) ou None si fail.
    reference_price = avgFillPrice si l'ordre a été filled (le cas habituel
    en paper), sinon le bid/ask mid récupéré pour info."""
    print("\n== 6. paper FUT MKT order — placeOrder + cancelOrder ==")
    front_fut = _front_future(ib)
    if front_fut is None:
        record("front future identifié", False, "aucun futur DTE >= 7")
        return None
    record("front future identifié", True,
           f"{front_fut.localSymbol} conId={front_fut.conId}")

    order = MarketOrder(action="BUY", totalQuantity=1)
    trade = ib.placeOrder(front_fut, order)
    ib.sleep(ORDER_CALLBACK_WAIT_S)
    status_after_place = trade.orderStatus.status
    record("placeOrder retourne un Trade avec status",
           bool(status_after_place),
           f"status après placeOrder = {status_after_place!r}")

    ib.cancelOrder(order)
    ib.sleep(ORDER_CALLBACK_WAIT_S)
    status_final = trade.orderStatus.status

    final_acceptable = status_final in ("Cancelled", "ApiCancelled", "Filled")
    record("status final = Cancelled OU Filled",
           final_acceptable, f"final = {status_final!r}")

    avg_price = safe(trade.orderStatus.avgFillPrice)
    if status_final == "Filled" and avg_price:
        filled_qty = trade.orderStatus.filled
        print(f"  [WARN] ordre MKT filled avant cancel — paper position résiduelle "
              f"{filled_qty} @ {avg_price:.5f} sur {front_fut.localSymbol}")
        print("         Reset paper account si besoin via portail IB.")
        return front_fut, avg_price

    # Pas de fill → pas de prix de référence, section 7 fera son propre fetch.
    return front_fut, 0.0


# == 7. Paper FOP LMT loin du marché — placement + cancel ==
# Ce que tu dois voir : status final strictement = Cancelled. Le LMT
# à 0.0001 ne peut JAMAIS filler (le call ATM est typiquement ~0.005),
# donc le cancel a tout son temps.
def section_7_fop_lmt_far_order(ib: IB, front_fut: Contract, spot_hint: float) -> None:
    print(f"\n== 7. paper FOP LMT loin du marché (lmt={LMT_FAR_PRICE}) ==")

    fop_template = Contract(
        symbol=front_fut.symbol, secType="FOP",
        exchange="CME", currency="USD",
        tradingClass="EUU",
    )
    fop_details = ib.reqContractDetails(fop_template)
    if not fop_details:
        record("FOP contracts trouvés", False, "0 contrats EUU")
        return

    # Spot : on utilise en priorité le avgFillPrice de §6 (le marché
    # vient littéralement de remplir à ce prix → meilleure ref possible).
    # Fallback sur reqMktData si pas de fill, mais en pratique le paper
    # IB fill toujours les MKT FUT instantanément, donc spot_hint > 0.
    if spot_hint > 0:
        spot = spot_hint
        record("spot reference (avgFillPrice de §6)", True, f"{spot:.5f}")
    else:
        ib.reqMarketDataType(3)
        fut_ticker = ib.reqMktData(front_fut, "", False, False)
        deadline = time.perf_counter() + 6.0
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
            record("spot reference (reqMktData fallback)", False, "no quote")
            return
        record("spot reference (reqMktData fallback)", True, f"{spot:.5f}")

    # Les FOP sur EUR/USD CME expirent AVANT le future sous-jacent (typique
    # pour les options sur futures), donc on ne match PAS sur l'expiry du
    # FUT — on cherche la front options expiry indépendamment : la plus
    # proche dans le futur avec DTE >= MIN_DTE.
    now = datetime.now(UTC).replace(tzinfo=None)
    fop_calls_by_expiry: dict[str, list[Contract]] = {}
    for d in fop_details:
        c = d.contract
        if c.right != "C":
            continue
        exp_str = c.lastTradeDateOrContractMonth
        try:
            exp = (datetime.strptime(exp_str, "%Y%m%d") if len(exp_str) == 8
                   else datetime.strptime(exp_str, "%Y%m"))
        except ValueError:
            continue
        if (exp - now).days < MIN_DTE:
            continue
        fop_calls_by_expiry.setdefault(exp_str, []).append(c)

    if not fop_calls_by_expiry:
        record("FOP Call front expiry", False,
               f"aucun Call FOP avec DTE >= {MIN_DTE}")
        return

    front_options_expiry = min(fop_calls_by_expiry.keys())
    front_calls = fop_calls_by_expiry[front_options_expiry]
    fop_call = min(front_calls, key=lambda c: abs(c.strike - spot))
    fop_dte = (datetime.strptime(front_options_expiry, "%Y%m%d") - now).days
    record("FOP Call ATM qualifié", True,
           f"{fop_call.localSymbol} K={fop_call.strike} expiry={front_options_expiry} "
           f"(DTE={fop_dte}, spot={spot:.5f})")

    order = LimitOrder(action="BUY", totalQuantity=1, lmtPrice=LMT_FAR_PRICE)
    trade = ib.placeOrder(fop_call, order)
    ib.sleep(ORDER_CALLBACK_WAIT_S)
    status_after_place = trade.orderStatus.status
    record("placeOrder LMT retourne un Trade",
           bool(status_after_place),
           f"status après placeOrder = {status_after_place!r}")

    ib.cancelOrder(order)
    ib.sleep(ORDER_CALLBACK_WAIT_S)
    status_final = trade.orderStatus.status

    # LMT à 0.0001 ne peut PAS filler → on attend Cancelled strict.
    record("status final = Cancelled (strict)",
           status_final in ("Cancelled", "ApiCancelled"),
           f"final = {status_final!r}")


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
        s6_result = section_6_fut_mkt_order(ib)
        if s6_result is None:
            print("\n  [SKIP] section 7 (front future indisponible)")
        else:
            front_fut, spot_hint = s6_result
            section_7_fop_lmt_far_order(ib, front_fut, spot_hint)
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
    section_5_bind_security()

    host_fail = _print_summary("[host] ")

    if not ibc_ready:
        print("\n  [SKIP] bridge pass (IBC pas loggé)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/ib-gateway/06_test_security_surface.py"
    print(f"\n  [INFO] sections 6-7 tournent sur le réseau {DOCKER_NETWORK}")
    print("         (même path que les engines de prod ; source IP dans 172.19.0.0/24)")
    print("         Spawning docker run...\n")

    cmd = [
        "docker", "run", "--rm",
        "--network", DOCKER_NETWORK,
        "-v", f"{repo_root}:/work",
        "-w", "/work",
        "python:3.11-slim",
        "sh", "-c",
        # tzdata requis : ib_insync.parseIBDatetime utilise ZoneInfo("US/Central")
        # pour décoder les timestamps des callbacks execDetails (orders fillés).
        # python:3.11-slim n'a pas tzdata par défaut → wrapper crash silencieusement
        # et corrompt le state des subscriptions market data suivantes.
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
# | Symptôme                                             | Cause probable                              | Fix                                                                  |
# |------------------------------------------------------|---------------------------------------------|----------------------------------------------------------------------|
# | port X bind sur 127.0.0.1 uniquement FAIL            | Compose modifié pour exposer 0.0.0.0        | Vérifier docker-compose.yml § ib-gateway, ports doivent être         |
# |                                                      |                                             | "127.0.0.1:4002:4002" et "127.0.0.1:5900:5900"                       |
# | placeOrder retourne PendingSubmit qui ne bouge pas   | Compte paper sans buying power suffisant    | Reset paper account (portail IB) ou augmenter quantity à 1            |
# | status final FAIL = Filled (section 7 LMT)           | Marché crashé (peu probable) ou bug         | Vérifier que LMT_FAR_PRICE est bien << prix marché                   |
# | FOP Call ATM qualifié FAIL                           | Marché options EUU fermé ou cache vide      | Re-tester en heures de marché ; sinon idem 04                        |
# | Order rejected with code 201/202                     | READ_ONLY_API a été activé accidentellement | docker compose up -d --force-recreate ib-gateway ; vérifier env var  |

if __name__ == "__main__":
    sys.exit(main())
