"""01 — Test ib-gateway (connexion + handshake TWS API).

Smoke test du container ``fxvol-ib-gateway``. Aucune dépendance runtime —
ib-gateway est une feuille du graphe (cf. ``docs/container_deps.md``),
seul le compte IB paper et les secrets SSM sont nécessaires côté test.

Couvre
------
1. Container UP + état du healthcheck Docker (socat probe interne)
2. TCP probe direct sur 127.0.0.1:4002 depuis l'host
3. Secrets présents en env (length-only, JAMAIS la valeur)
4. ``ib.connect()`` synchrone (ib_insync) → ``isConnected() == True``
5. ``reqCurrentTime()`` — drift serveur vs local ≤ 5s
6. ``disconnect()`` propre + reconnect avec un autre clientId

Préreq
------
- Container démarré : ``docker compose --profile ib up -d ib-gateway``
- IBC login terminé : ``docker logs fxvol-ib-gateway --tail 50`` doit
  montrer ``Login has completed`` (~60-90s après le ``up``).
- Secrets en env : ``.\\scripts\\load_secrets.ps1`` dans la PowerShell
  qui lance ce script.
- ``pip install ib_insync`` (déjà dans ``requirements.txt``).

Pourquoi un .py et pas un .ipynb (alors que les autres containers le sont)
-------------------------------------------------------------------------
``ib_insync`` a une incompatibilité documentée avec ipykernel récent :
``ib.connect()`` fait ``loop.run_until_complete(connectAsync())`` ; or
ipykernel maintient un context asyncio strict qui refuse les
``run_until_complete`` re-entrants, même avec ``nest_asyncio`` ou
``util.startLoop()``. Symptôme : ``RuntimeError: cannot enter context
... is already entered`` puis ``TimeoutError`` sur le handshake API.
Le fix officiel ``util.patchAsyncio()`` aggrave le problème dans les
versions récentes de Jupyter. Cf. discussion notebook 01 R9 sandbox.

La surface IB ne nécessitant pas de stepping cellule-par-cellule (pass/fail
binaire), le pivot vers .py est plus pragmatique. ``SMOKE_PLAN.md``
documente cette exception au pattern ``0N_test_*.ipynb`` du reste.

Usage
-----
    python scripts/ib-gateway/01_test_connection.py

Sortie : tableau OK/FAIL + exit code = nombre de FAILs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime

from ib_insync import IB

HOST = "127.0.0.1"
PORT = 4002
CLIENT_ID = 199            # sandbox, hors plage prod (1, 2, 3) et research (14)
CLIENT_ID_BIS = 198        # pour le test reconnect avec un autre id
CONTAINER = "fxvol-ib-gateway"

results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = "OK" if ok else "FAIL"
    print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")


# == 1. Container UP + healthcheck Docker ==
# Ce que tu dois voir : container `running` ET healthcheck `healthy`. Un
# état `starting` (≤ 90s post-`up`) est acceptable. `unhealthy` après 2
# min = TWS API a crashé après login OU probe interne cassé (cf. fix socat
# dans `docker-compose.yml` § ib-gateway).
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

    out = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", CONTAINER],
        capture_output=True, text=True,
    )
    print(f"  [INFO] StartedAt = {out.stdout.strip()}")


# == 2. TCP probe 127.0.0.1:4002 depuis l'host ==
# Ce que tu dois voir : socket ouvre en < 1s. Valide que le port-mapping
# `127.0.0.1:4002:4002` fonctionne. Si fail mais healthcheck OK → conflit
# de port côté Windows (`Get-NetTCPConnection -LocalPort 4002`).
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


# == 3. Secrets présents en env (sans exposition) ==
# Règle absolue (CLAUDE.md § "zéro exposition des secrets") : on ne lit
# JAMAIS la valeur d'un secret. Length-only check. Si MISSING → relancer
# `.\scripts\load_secrets.ps1` dans CETTE PowerShell, puis re-run le .py.
def section_3_secrets() -> None:
    print("\n== 3. secrets en env (length-only check) ==")

    for key in ("IB_USERID", "IB_PASSWORD", "VNC_PASSWORD"):
        val = os.environ.get(key, "")
        record(f"{key} set", bool(val), f"length = {len(val)}" if val else "MISSING")


# == 4. IB.connect() + handshake TWS API ==
# Ce que tu dois voir : `connect()` rend la main en < 5s, `isConnected()
# == True`, `serverVersion()` retourne un int ≥ 176 pour Gateway 10.x.
#
# Si TimeoutError alors que TCP probe (§2) a réussi : TWS API socket
# ouvert mais Gateway ne répond pas au handshake. Causes courantes :
#   1. clientId déjà utilisé (zombie session) → bumper CLIENT_ID
#   2. Popup IB Gateway "Allow incoming connection?" en attente côté VNC
#      (l'IP source vue par le container = Docker bridge 172.17.0.1, pas
#      127.0.0.1, donc TrustedIPs jts.ini ne couvre pas → popup)
#   3. Image Gateway obsolète refusant la version du protocole
def section_4_connect(ib: IB) -> bool:
    print("\n== 4. IB.connect() + handshake ==")

    t0 = time.perf_counter()
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
        dt = time.perf_counter() - t0
        record("ib.connect()", ib.isConnected(), f"{dt*1000:.0f} ms")
    except Exception as e:
        record("ib.connect()", False, f"{type(e).__name__}: {e}")
        return False

    sv = ib.client.serverVersion()
    record("serverVersion", isinstance(sv, int) and sv >= 176, f"v{sv}")

    ct = ib.client.twsConnectionTime()
    record("twsConnectionTime", bool(ct), str(ct)[:30] if ct else "<empty>")
    return True


# == 5. reqCurrentTime — drift serveur vs local ==
# Ce que tu dois voir : drift |server-local| ≤ 5s. Drift > 5s = horloge
# host désynchronisée (NTP) ou container Docker décalé. Critique car
# tous les timestamps applicatifs viennent du serveur IB ; un drift fait
# rejeter les ordres MOC/LOC et casse les corrélations multi-services.
def section_5_clock(ib: IB) -> None:
    print("\n== 5. reqCurrentTime — server vs local clock ==")

    server_dt = ib.reqCurrentTime()  # datetime tz-aware (UTC)
    local_dt = datetime.now(UTC)
    drift_s = (server_dt - local_dt).total_seconds()

    print(f"  server time : {server_dt.isoformat()}")
    print(f"  local  time : {local_dt.isoformat()}")
    print(f"  drift       : {drift_s:+.2f} s")

    record("reqCurrentTime renvoie un datetime", isinstance(server_dt, datetime), type(server_dt).__name__)
    record("drift |server-local| ≤ 5s", abs(drift_s) <= 5.0, f"{drift_s:+.2f}s")


# == 6. disconnect + reconnect avec autre clientId ==
# Ce que tu dois voir : après `disconnect()`, `isConnected() == False`.
# Une nouvelle instance IB() peut immédiatement se reconnecter avec un
# AUTRE clientId (198) sans erreur 326. Preuve qu'on ne laisse pas de
# session zombie côté Gateway. Le test "même clientId" est volontairement
# absent ici — il fait l'objet du futur 05_test_resilience.py.
def section_6_reconnect(ib: IB) -> None:
    print("\n== 6. disconnect + reconnect avec autre clientId ==")

    ib.disconnect()
    record("disconnect()", not ib.isConnected(), f"isConnected={ib.isConnected()}")

    ib2 = IB()
    try:
        ib2.connect(HOST, PORT, clientId=CLIENT_ID_BIS, timeout=15)
        record(f"reconnect avec clientId={CLIENT_ID_BIS}", ib2.isConnected(),
               f"serverVersion=v{ib2.client.serverVersion()}")
    except Exception as e:
        record(f"reconnect avec clientId={CLIENT_ID_BIS}", False, f"{type(e).__name__}: {e}")
    finally:
        if ib2.isConnected():
            ib2.disconnect()


def main() -> int:
    print(f"target = {HOST}:{PORT}, clientId = {CLIENT_ID}")
    ib = IB()

    section_1_container()
    section_2_tcp_probe()
    section_3_secrets()

    connected = section_4_connect(ib)
    if connected:
        section_5_clock(ib)
        section_6_reconnect(ib)
    else:
        print("\n  [SKIP] sections 5 et 6 (connect a fail, rien à tester en aval)")

    # Cleanup défensif
    try:
        if ib.isConnected():
            ib.disconnect()
    except Exception:
        pass

    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)

    print(f"\n{'LABEL':<45} STATUS  DETAIL")
    print("-" * 100)
    for label, ok, detail in results:
        sym = "OK" if ok else "FAIL"
        print(f"{label:<45} {sym:<6}  {detail}")
    print("-" * 100)
    print(f"\n{n_ok} OK / {n_fail} FAIL  ({len(results)} total)")

    if n_fail == 0:
        print("\nOK ib-gateway connection surface fully validated. Pass aux scripts 02-06.")

    return n_fail


# Troubleshooting cheat sheet
# ============================
# | Symptôme                                       | Cause probable                          | Fix                                                                                |
# |------------------------------------------------|-----------------------------------------|------------------------------------------------------------------------------------|
# | docker container state = <not found>           | Profile `ib` non monté                  | docker compose --profile ib up -d ib-gateway                                       |
# | healthcheck = starting depuis > 2 min          | IBC en attente push 2FA / image vieille | docker logs fxvol-ib-gateway --tail 100 ; valider IB Key iPhone                    |
# | healthcheck = unhealthy mais TCP probe host OK | Probe interne cassé (image minimale)    | Déjà fixé avec socat dans docker-compose.yml                                       |
# | TCP probe host FAIL                            | Conflit port 4002 côté Windows          | Get-NetTCPConnection -LocalPort 4002                                               |
# | IB_USERID / IB_PASSWORD MISSING                | Secrets pas chargés                     | .\scripts\load_secrets.ps1 dans la PowerShell qui lance ce .py                     |
# | ib.connect() TimeoutError mais TCP OK          | clientId zombie OU popup VNC bloquante  | Restart container, ou regarder VNC pour cliquer "Accept incoming connection"        |
# | Error 326 client id already in use             | clientId déjà pris                      | Bumper CLIENT_ID en haut du fichier ; vérifier que l'app PyQt v1 ne tourne pas     |
# | Error 502 Couldn't connect to TWS              | TWS API pas prête / READ_ONLY_API mal   | Restart container ; docker compose config                                          |
# | drift > 5s                                     | Horloge host désync / WSL2 post-veille  | w32tm /resync (Windows) ; wsl --shutdown                                           |
# | reconnect avec clientId=198 FAIL avec 326      | Session zombie côté Gateway             | Restart container ; en prod c'est 05_test_resilience qui couvre ce cas             |

if __name__ == "__main__":
    sys.exit(main())
