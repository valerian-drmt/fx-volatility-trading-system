"""01 — Test ib-gateway (connexion + handshake TWS API).

Smoke test du container ``fxvol-ib-gateway``. Aucune dépendance runtime —
ib-gateway est une feuille du graphe (cf. ``docs/container_deps.md``),
seul le compte IB paper et les secrets SSM sont nécessaires côté test.

Couvre
------
1. Container UP + état du healthcheck Docker (socat probe interne)
2. TCP probe direct sur 127.0.0.1:4002 depuis l'host
3. Secrets présents en env (length-only, JAMAIS la valeur)
4. IBC login complété sur les serveurs IB (parse ``docker logs``)
5. ``ib.connect()`` synchrone (ib_insync) → ``isConnected() == True``
6. ``reqCurrentTime()`` — drift serveur vs local ≤ 5s
7. ``disconnect()`` propre + reconnect avec un autre clientId

Le §4 distingue **container ready** (TWS écoute le socket, ce que mesure
le §1 healthcheck) de **IBC ready** (IBC a fini son login auprès des
serveurs IB Interactive Brokers). Sans le §4, un IBC bloqué en 2FA
silencieux ou en image obsolète passe les §1-§3 mais fait timeout
silencieusement le §5 — et on perd 15s à comprendre pourquoi.

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

Architecture en 2 passes
------------------------
Le smoke tourne en deux passes successives, orchestrées par le script lui-même :

1. **Host pass** (sections 1-4) : exécutée par Python sur l'host Windows. Valide
   la couche compose / docker / TCP / secrets / IBC login. Connecte au container
   via `127.0.0.1:4002` mais Docker NAT translate la source en `172.19.0.1`,
   non trusted par Gateway.
2. **Namespace pass** (sections 5-7) : si IBC est OK, le script spawn un sub-
   process ``docker run --rm --network container:fxvol-ib-gateway python:3.11-slim``
   qui partage le namespace réseau du container ib-gateway. Depuis ce namespace,
   ``127.0.0.1:4002`` est trusted nativement → handshake API marche.

Pourquoi ce split : sur Windows Docker Desktop, ``network_mode: host`` ne
fonctionne pas réellement (le container reste dans la VM Linux), et l'IP
source vue par Gateway est toujours la passerelle Docker bridge. La GUI
permet d'ajouter cette IP aux Trusted IPs (cf. fix VNC dans la session R9
sandbox 28/04/2026), mais IBC enforce ``TrustedIPs=127.0.0.1`` à chaque
boot, et la persistance via .ibgzenc encrypted file ne survit pas non plus.
Le namespace partagé est le seul fix qui marche end-to-end sans toucher
à l'archi compose.

Usage
-----
    python scripts/ib-gateway/01_test_connection.py        # mode normal (host + namespace)
    python scripts/ib-gateway/01_test_connection.py --namespace  # interne, ne pas appeler à la main

Le mode ``--namespace`` est invoqué automatiquement par le sub-process
``docker run``. L'utilisateur lance toujours la commande sans flag.

Sortie : 2 tableaux OK/FAIL (un par passe) + exit code = nombre total de FAILs.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from ib_insync import IB

PORT = 4002
CLIENT_ID = 197
CLIENT_ID_BIS = 196       # pour le test reconnect avec un autre id
CONTAINER = "fxvol-ib-gateway"

# Host pass (sections 1-4) tape sur 127.0.0.1 (port-forward Docker NAT
# côté Windows). Namespace pass (sections 5-7) tape sur le DNS Docker
# `ib-gateway` depuis l'intérieur du réseau fxvol-internal — ainsi la
# source IP est `172.19.0.X` (l'IP du container test sur le subnet),
# qui matche `172.19.0.0/24` dans Trusted IPs. Path identique à celui
# qu'utilisent market-data, vol-engine, risk en prod.
HOST_FROM_HOST = "127.0.0.1"
HOST_FROM_DOCKER = "ib-gateway"

# Network Docker du compose. Préfixé automatiquement par le nom du
# projet (= nom du dossier racine). Si tu renommes le dossier, mets à
# jour ici.
DOCKER_NETWORK = "fx-volatility-trading-system_fxvol-internal"

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
        sock.connect((HOST_FROM_HOST, PORT))
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


# == 4. IBC login status (parse docker logs) ==
# Ce que tu dois voir : "Login has completed" présent dans les ~200
# dernières lignes des logs container.
#
# Pourquoi cette section : le healthcheck Docker (§1) ne valide que le
# port TCP — il dit "TWS écoute" mais pas "IBC est loggé sur les
# serveurs IB". Si IBC n'a pas fini son login (2FA en attente, image
# obsolète, creds rejetés), le socket TCP ouvre quand même (§2 OK), mais
# le handshake API du §5 timeoutera silencieusement après 15s sans
# explication. Cette section attrape le cas en amont avec un message
# clair, et skip §5/§6/§7 pour ne pas perdre 30s de plus à attendre des
# timeouts certains.
def section_4_ibc_login() -> bool:
    print("\n== 4. IBC login status (docker logs) ==")

    out = subprocess.run(
        ["docker", "logs", "--tail", "200", CONTAINER],
        capture_output=True, text=True,
    )
    logs = (out.stdout + out.stderr).lower()

    has_login = "login has completed" in logs
    has_2fa = "second factor authentication" in logs
    has_obsolete = "is no longer supported" in logs
    has_bad_creds = "invalid username" in logs or "invalid password" in logs

    record("IBC login completed", has_login,
           "marker 'Login has completed' found" if has_login else "marker absent")

    if not has_login:
        if has_2fa:
            print("  [WARN] pattern '2FA' dans les logs — valider la push IB Key sur iPhone")
        if has_obsolete:
            print("  [WARN] 'is no longer supported' dans les logs — image Gateway à bumper (cf. infrastructure/ib-gateway/README.md)")
        if has_bad_creds:
            print("  [WARN] credentials rejetés dans les logs — secrets mal injectés au compose up")
        if not (has_2fa or has_obsolete or has_bad_creds):
            print("  [WARN] aucun marker connu — IBC est peut-être encore en cours de login (attendre 30s puis re-run)")

    return has_login


# == 5. IB.connect() + handshake TWS API ==
# Ce que tu dois voir : `connect()` rend la main en < 5s, `isConnected()
# == True`, `serverVersion()` retourne un int ≥ 176 pour Gateway 10.x.
#
# Si TimeoutError alors que §1-§4 ont passé : TWS API socket ouvert et
# IBC loggé, mais Gateway ne répond pas au handshake API. Causes :
#   1. clientId déjà utilisé (zombie session) → bumper CLIENT_ID
#   2. Popup IB Gateway "Allow incoming connection?" en attente côté VNC
#      (l'IP source vue par le container = Docker bridge 172.17.0.1, pas
#      127.0.0.1, donc TrustedIPs jts.ini ne couvre pas → popup)
#   3. "Enable ActiveX and Socket Clients" décoché dans Configuration > API
def section_5_connect(ib: IB) -> bool:
    print("\n== 5. IB.connect() + handshake ==")

    t0 = time.perf_counter()
    try:
        ib.connect(HOST_FROM_DOCKER, PORT, clientId=CLIENT_ID, timeout=15)
        dt = time.perf_counter() - t0
        record("ib.connect()", ib.isConnected(), f"{dt*1000:.0f} ms")
    except Exception as e:
        record("ib.connect()", False, f"{type(e).__name__}: {e}")
        return False

    sv = ib.client.serverVersion()
    record("serverVersion", isinstance(sv, int) and sv >= 176, f"v{sv}")
    return True


# == 6. reqCurrentTime — drift serveur vs local ==
# Ce que tu dois voir : drift |server-local| ≤ 5s. Drift > 5s = horloge
# host désynchronisée (NTP) ou container Docker décalé. Critique car
# tous les timestamps applicatifs viennent du serveur IB ; un drift fait
# rejeter les ordres MOC/LOC et casse les corrélations multi-services.
def section_6_clock(ib: IB) -> None:
    print("\n== 6. reqCurrentTime — server vs local clock ==")

    server_dt = ib.reqCurrentTime()  # datetime tz-aware (UTC)
    local_dt = datetime.now(UTC)
    drift_s = (server_dt - local_dt).total_seconds()

    print(f"  server time : {server_dt.isoformat()}")
    print(f"  local  time : {local_dt.isoformat()}")
    print(f"  drift       : {drift_s:+.2f} s")

    record("reqCurrentTime renvoie un datetime", isinstance(server_dt, datetime), type(server_dt).__name__)
    record("drift |server-local| ≤ 5s", abs(drift_s) <= 5.0, f"{drift_s:+.2f}s")


# == 7. disconnect + reconnect avec autre clientId ==
# Ce que tu dois voir : après `disconnect()`, `isConnected() == False`.
# Une nouvelle instance IB() peut immédiatement se reconnecter avec un
# AUTRE clientId (198) sans erreur 326. Preuve qu'on ne laisse pas de
# session zombie côté Gateway. Le test "même clientId" est volontairement
# absent ici — il fait l'objet du futur 05_test_resilience.py.
def section_7_reconnect(ib: IB) -> None:
    print("\n== 7. disconnect + reconnect avec autre clientId ==")

    ib.disconnect()
    record("disconnect()", not ib.isConnected(), f"isConnected={ib.isConnected()}")

    ib2 = IB()
    try:
        ib2.connect(HOST_FROM_DOCKER, PORT, clientId=CLIENT_ID_BIS, timeout=15)
        record(f"reconnect avec clientId={CLIENT_ID_BIS}", ib2.isConnected(),
               f"serverVersion=v{ib2.client.serverVersion()}")
    except Exception as e:
        record(f"reconnect avec clientId={CLIENT_ID_BIS}", False, f"{type(e).__name__}: {e}")
    finally:
        if ib2.isConnected():
            ib2.disconnect()


def _print_summary(prefix: str = "") -> int:
    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{prefix}{'LABEL':<45} STATUS  DETAIL")
    print("-" * 100)
    for label, ok, detail in results:
        sym = "OK" if ok else "FAIL"
        print(f"{label:<45} {sym:<6}  {detail}")
    print("-" * 100)
    print(f"\n{prefix}{n_ok} OK / {n_fail} FAIL  ({len(results)} total)")
    return n_fail


def _run_namespace_pass() -> int:
    """Sections 5-7 only. Lancé via `docker run --network <fxvol-internal>`
    sur le réseau bridge Docker du compose. Source IP côté Gateway =
    `172.19.0.X` (l'IP du container test sur le subnet), couverte par
    `TrustedIPs=127.0.0.1,172.19.0.0/24` configuré dans la GUI Gateway
    et persisté via le volume `ib_gateway_jts`. Path identique à
    market-data, vol-engine, risk en prod."""
    print("=== BRIDGE PASS (inside fxvol-internal Docker network) ===")
    print(f"target = {HOST_FROM_DOCKER}:{PORT}, clientId = {CLIENT_ID}\n")
    ib = IB()
    try:
        connected = section_5_connect(ib)
        if connected:
            section_6_clock(ib)
            section_7_reconnect(ib)
        else:
            print("\n  [SKIP] sections 6 et 7 (connect a fail)")
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
    return _print_summary("[namespace] ")


def _run_host_pass() -> int:
    """Sections 1-4 sur l'host (Windows / WSL2). Si IBC est loggé, spawn un
    docker run sur le bridge network fxvol-internal pour exécuter sections
    5-7 — source IP = IP container test sur 172.19.0.0/24, exactement le
    path de market-data/vol-engine/risk en prod."""
    print("=== HOST PASS (Windows / Docker NAT) ===")
    print(f"target = {HOST_FROM_HOST}:{PORT}\n")

    section_1_container()
    section_2_tcp_probe()
    section_3_secrets()
    ibc_ready = section_4_ibc_login()

    host_fail = _print_summary("[host] ")

    if not ibc_ready:
        print("\n  [SKIP] bridge pass (IBC pas loggé, sections 5/6/7 inutiles)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/smoke/ib-gateway/01_test_connection.py"
    print(f"\n  [INFO] sections 5-7 tournent sur le réseau {DOCKER_NETWORK}")
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
# | Symptôme                                       | Cause probable                          | Fix                                                                                |
# |------------------------------------------------|-----------------------------------------|------------------------------------------------------------------------------------|
# | docker container state = <not found>           | Profile `ib` non monté                  | docker compose --profile ib up -d ib-gateway                                       |
# | healthcheck = starting depuis > 2 min          | IBC en attente push 2FA / image vieille | docker logs fxvol-ib-gateway --tail 100 ; valider IB Key iPhone                    |
# | healthcheck = unhealthy mais TCP probe host OK | Probe interne cassé (image minimale)    | Déjà fixé avec socat dans docker-compose.yml                                       |
# | TCP probe host FAIL                            | Conflit port 4002 côté Windows          | Get-NetTCPConnection -LocalPort 4002                                               |
# | IB_USERID / IB_PASSWORD MISSING                | Secrets pas chargés                     | .\scripts\load_secrets.ps1 dans la PowerShell qui lance ce .py                     |
# | IBC login completed = absent                   | IBC pas fini de logger / 2FA / image    | docker logs --tail 200 fxvol-ib-gateway ; valider IB Key iPhone si pending          |
# | ib.connect() TimeoutError mais §1-§4 OK        | clientId zombie OU popup VNC bloquante  | Restart container, ou regarder VNC pour cliquer "Accept incoming connection"        |
# | Error 326 client id already in use             | clientId déjà pris                      | Bumper CLIENT_ID en haut du fichier ; vérifier que l'app PyQt v1 ne tourne pas     |
# | Error 502 Couldn't connect to TWS              | TWS API pas prête / READ_ONLY_API mal   | Restart container ; docker compose config                                          |
# | drift > 5s                                     | Horloge host désync / WSL2 post-veille  | w32tm /resync (Windows) ; wsl --shutdown                                           |
# | reconnect avec clientId=198 FAIL avec 326      | Session zombie côté Gateway             | Restart container ; en prod c'est 05_test_resilience qui couvre ce cas             |

if __name__ == "__main__":
    sys.exit(main())
