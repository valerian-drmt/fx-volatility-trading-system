"""05 — Test ib-gateway (resilience : concurrence, reconnect, collision).

Smoke test du container ``fxvol-ib-gateway`` — étape 5/6. Valide que la
**robustesse de la couche connexion** correspond à ce que le projet
attend en prod : 3 clients concurrents (réplique du modèle 3-threads
de l'app PyQt v1), reconnexion immédiate après disconnect, rejet de
collision sur clientId.

Couvre
------
1-4. Gates host (container, TCP, secrets, IBC login) — réplique 03/04
5. **Concurrence** — 3 ``IB()`` clients connectés simultanément avec
   clientIds distincts (187, 188, 189) ; chacun fait un ``reqCurrentTime``
   indépendamment. Réplique du modèle 3-threads de la v1 PyQt :
   `dashboard_ib` (clientId=3), `market_data_ib` (=2), `order_ib` (=1).
6. **Reconnect même clientId** — connect(186) → disconnect → connect(186)
   à nouveau. Quelques secondes peuvent être nécessaires pour qu'IB
   libère le slot, on poll jusqu'à 5s.
7. **Collision détectée** — c1.connect(185) reste actif, puis c2 tente
   connect(185). IB doit soit rejeter c2 (le cas attendu), soit kicker
   c1 et accepter c2 (acceptable aussi). Le seul cas inacceptable :
   les deux clients connectés simultanément avec même id.

Pourquoi pas tester un container restart
-----------------------------------------
Le test "stale session après ``compose restart``" était dans le SMOKE_PLAN
initial mais on le skip volontairement : c'est lourd (60-90s de restart),
et c'est déjà validé implicitement par ``01_test_connection.py`` à chaque
session fraîche. Le smoke doit rester rapide.

Architecture en 2 passes (cf. ``01_test_connection.py``)
--------------------------------------------------------
Sections 1-4 sur l'host. Sections 5-7 spawnées dans un sub-process
``docker run --rm --network container:fxvol-ib-gateway``.

Préreq
------
- Container démarré avec ib-gateway healthy
- IBC loggé sur compte paper
- Secrets en env

Usage
-----
    python scripts/ib-gateway/05_test_resilience.py

Sortie : 2 tableaux OK/FAIL (host + namespace) + exit code = nb FAILs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from ib_insync import IB

HOST = "127.0.0.1"
PORT = 4002
CONTAINER = "fxvol-ib-gateway"

# clientIds dédiés à ce smoke. Tous distincts des prod (1, 2, 3),
# research (14), et autres smokes (197, 195, 193, 191).
CONCURRENT_IDS = (187, 188, 189)
RECONNECT_ID = 186
COLLISION_ID = 185

# Délai max pour qu'IB libère un clientId après disconnect — empirique :
# 1-3s typique. 5s = marge confortable.
RECONNECT_WAIT_S = 5.0

results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = "OK" if ok else "FAIL"
    print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")


def safe_disconnect(*ibs: IB) -> None:
    """Disconnect tolérant les exceptions, dans n'importe quel état."""
    for ib in ibs:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass


# == 1-4. Gates host (réplique 03/04) ==
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
        sock.connect((HOST, PORT))
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


# == 5. Concurrence — 3 clients simultanés ==
# Ce que tu dois voir : les 3 IB() coexistent, chacun retourne un
# ``reqCurrentTime`` indépendamment. C'est exactement le modèle de
# l'app PyQt v1 : dashboard_ib (clientId=3), market_data_ib (=2),
# order_ib (=1). Si IB acceptait pas plusieurs sessions, le projet
# entier ne pourrait pas tourner.
def section_5_concurrent() -> None:
    print(f"\n== 5. concurrence — 3 clients (ids={CONCURRENT_IDS}) ==")
    clients: list[tuple[int, IB]] = []
    try:
        for cid in CONCURRENT_IDS:
            c = IB()
            c.connect(HOST, PORT, clientId=cid, timeout=15)
            clients.append((cid, c))

        all_connected = all(c.isConnected() for _, c in clients)
        record(f"{len(CONCURRENT_IDS)} clients connectés simultanément",
               all_connected,
               f"all isConnected={all_connected}, sv={[c.client.serverVersion() for _, c in clients]}")

        # Chaque client appelle reqCurrentTime indépendamment.
        for cid, c in clients:
            try:
                t = c.reqCurrentTime()
                record(f"client {cid} reqCurrentTime", isinstance(t, datetime),
                       t.isoformat() if isinstance(t, datetime) else f"got {type(t).__name__}")
            except Exception as e:
                record(f"client {cid} reqCurrentTime", False, f"{type(e).__name__}: {e}")

    finally:
        # Disconnect dans l'ordre inverse pour libérer proprement.
        safe_disconnect(*[c for _, c in reversed(clients)])
        time.sleep(1.0)  # laisse IB libérer les slots avant la suite


# == 6. Reconnect même clientId ==
# Ce que tu dois voir : après disconnect, on peut se reconnecter avec
# le MÊME clientId en quelques secondes max. IB met parfois 1-3s à
# libérer le slot — c'est connu, on poll jusqu'à 5s.
#
# Pourquoi c'est critique : si l'app crashe et redémarre rapidement,
# elle DOIT pouvoir reprendre ses connexions sur les mêmes clientIds
# sans devoir attendre, sinon on perd des ticks/orders.
def section_6_reconnect() -> None:
    print(f"\n== 6. reconnect même clientId={RECONNECT_ID} ==")
    c1 = IB()
    try:
        c1.connect(HOST, PORT, clientId=RECONNECT_ID, timeout=15)
        record("première connexion", c1.isConnected(),
               f"clientId={RECONNECT_ID} connecté")
        c1.disconnect()
        time.sleep(0.2)
    except Exception as e:
        record("première connexion", False, f"{type(e).__name__}: {e}")
        return

    # Poll de reconnexion immédiate.
    reconnect_ok = False
    last_err: str | None = None
    deadline = time.perf_counter() + RECONNECT_WAIT_S
    attempts = 0
    while time.perf_counter() < deadline:
        attempts += 1
        c2 = IB()
        try:
            c2.connect(HOST, PORT, clientId=RECONNECT_ID, timeout=8)
            reconnect_ok = c2.isConnected()
            safe_disconnect(c2)
            break
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.5)

    record(f"reconnect ≤ {RECONNECT_WAIT_S}s", reconnect_ok,
           f"after {attempts} attempt(s)" if reconnect_ok
           else f"failed after {attempts} attempts: {last_err}")
    time.sleep(0.5)


# == 7. Collision détectée — 2 connects sur même clientId ==
# Comportements acceptables :
#  A. c2 rejeté → IB enforce le no-duplicate (cas le plus courant)
#  B. c1 kické + c2 accepté → IB remplace silencieusement la session
# Comportement INACCEPTABLE :
#  C. c1 + c2 simultanés sur même clientId → bug de Gateway, on doit
#     fail loud parce que le state IB devient inconsistant.
def section_7_collision() -> None:
    print(f"\n== 7. collision détectée — 2× connect(clientId={COLLISION_ID}) ==")
    c1 = IB()
    c2 = IB()
    try:
        c1.connect(HOST, PORT, clientId=COLLISION_ID, timeout=15)
        record("c1 première connexion", c1.isConnected(),
               f"clientId={COLLISION_ID} en place")

        # Tentative de collision — peut raise ou retourner avec c2 connecté
        try:
            c2.connect(HOST, PORT, clientId=COLLISION_ID, timeout=10)
        except Exception as e:
            print(f"  [INFO] c2.connect a raise : {type(e).__name__}: {e}")

        c1_still = c1.isConnected()
        c2_connected = c2.isConnected()

        if not c2_connected:
            # Cas A : c2 rejeté
            record("collision détectée (c2 rejeté)", True,
                   f"c1 still connected={c1_still}, c2 rejected as expected")
        elif c2_connected and not c1_still:
            # Cas B : c1 kické, c2 a pris la place
            record("collision détectée (c1 remplacé par c2)", True,
                   "IB a remplacé la session existante — acceptable")
        else:
            # Cas C : les deux connectés en même temps = bug
            record("collision détectée", False,
                   "BUG: c1 et c2 connectés simultanément avec même clientId")
    finally:
        safe_disconnect(c1, c2)


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
    print(f"target = {HOST}:{PORT}\n")
    print(f"  reserved clientIds: concurrent={CONCURRENT_IDS}, "
          f"reconnect={RECONNECT_ID}, collision={COLLISION_ID}\n")

    # Pas de marker connection ici — chaque section gère sa propre
    # connexion. Si la connectivité namespace est cassée, la section 5
    # le verra et on aura un FAIL clair sur le premier connect.
    section_5_concurrent()
    section_6_reconnect()
    section_7_collision()

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
        print("\n  [SKIP] namespace pass (IBC pas loggé)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/ib-gateway/05_test_resilience.py"
    print("\n  [INFO] sections 5-7 doivent tourner depuis le namespace ib-gateway")
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
# | Symptôme                                       | Cause probable                              | Fix                                                                       |
# |------------------------------------------------|---------------------------------------------|---------------------------------------------------------------------------|
# | 3 clients concurrents FAIL au 2e ou 3e         | clientId déjà pris par un autre process     | Vérifier qu'aucun autre smoke ni l'app v1 ne tourne en parallèle           |
# | reconnect ≤ 5s FAIL                            | IB met > 5s à libérer le slot ce jour-là    | Bumper RECONNECT_WAIT_S à 10s ; sinon Gateway en surcharge → restart      |
# | collision détectée FAIL avec "BUG"             | Bug Gateway (très rare)                      | Restart Gateway ; rapporter à upstream gnzsnz/ib-gateway si reproductible |
# | Tous timeout                                   | clientId zombie d'un précédent crash         | Restart container : docker compose --profile ib restart ib-gateway        |

if __name__ == "__main__":
    sys.exit(main())
