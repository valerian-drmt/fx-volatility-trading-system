"""02 — Test ib-gateway (account summary + paper guard + positions).

Smoke test du container ``fxvol-ib-gateway`` — étape 2/6. Ce script valide
que **le compte IB connecté est utilisable** : tags critiques renseignés,
compte effectivement en mode paper (jamais live par accident), endpoint
positions répondant.

Couvre
------
1. Container UP + healthcheck Docker (réplique du 01, gate)
2. TCP probe direct sur 127.0.0.1:4002 depuis l'host (gate)
3. Secrets en env (length-only, JAMAIS la valeur)
4. IBC login complété sur les serveurs IB (gate)
5. ``reqAccountSummary()`` renvoie ``NetLiquidation``, ``BuyingPower``,
   ``AvailableFunds`` numériques > 0 (currency = celle configurée dans
   le compte IB ; EUR/USD/GBP, peu importe tant que cohérente)
6. **Paper guard** — l'account ID commence par ``DU`` (compte paper IB).
   Tout autre préfixe (``U``, ``F``, ``I``) = compte live, on fail loud
   pour ne JAMAIS exécuter d'ordre paper-only sur un compte réel
7. ``reqPositions()`` rend une liste (vide acceptable, c'est juste une
   preuve que l'API positions répond)

Architecture en 2 passes (cf. ``01_test_connection.py``)
--------------------------------------------------------
Sections 1-4 sur l'host Windows. Sections 5-7 spawnées dans un
sub-process ``docker run --rm --network container:fxvol-ib-gateway``
qui partage le namespace réseau d'ib-gateway → source IP = 127.0.0.1
(loopback partagé), trusted nativement, le handshake API marche.

Pourquoi un fail strict sur le préfixe ``DU``
---------------------------------------------
Tout le projet est conçu pour fonctionner sur compte paper (``TRADING_MODE=paper``
côté compose, secrets SSM = credentials paper). Si quelqu'un swappe les
credentials par erreur ou si IBC bascule sur un compte live (multi-comptes
IB), un order placement piloté par un engine pourrait s'exécuter en réel.
Cette section est un **interrupteur d'arrêt** explicite : si le compte
n'est pas un ``DU…``, on stop ici, le smoke fail.

Préreq
------
- Container démarré : ``docker compose --profile ib up -d ib-gateway``
- IBC login terminé (``Login has completed`` dans les docker logs)
- Secrets en env : ``.\\scripts\\load_secrets.ps1``
- ``pip install ib_insync`` (déjà dans ``requirements.txt``)

Usage
-----
    python scripts/ib-gateway/02_test_account.py        # mode normal
    python scripts/ib-gateway/02_test_account.py --namespace  # interne, auto-spawné

Sortie : 2 tableaux OK/FAIL (host + namespace) + exit code = nb FAILs.
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from ib_insync import IB

PORT = 4002
CLIENT_ID = 195            # sandbox, distinct des autres scripts (01 = 197/196)
CONTAINER = "fxvol-ib-gateway"

# Host pass tape sur 127.0.0.1 (port-forward Docker NAT). Bridge pass
# tape sur DNS `ib-gateway` depuis fxvol-internal — source IP = 172.19.0.X
# (couvert par TrustedIPs=127.0.0.1,172.19.0.0/24 persisté dans volume
# ib_gateway_jts). Path identique aux engines de prod.
HOST_FROM_HOST = "127.0.0.1"
HOST_FROM_DOCKER = "ib-gateway"
DOCKER_NETWORK = "fx-volatility-trading-system_fxvol-internal"

# Tags critiques que reqAccountSummary doit renvoyer pour qu'on considère
# le compte exploitable. Tous attendus en USD, valeur numérique > 0.
REQUIRED_TAGS = ("NetLiquidation", "BuyingPower", "AvailableFunds")

# Préfixe légal pour un compte paper IB. Tout autre préfixe = compte live
# ou compte de démo non-paper, on stop le smoke.
PAPER_ACCOUNT_PREFIX = "DU"

results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    sym = "OK" if ok else "FAIL"
    print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")


# == 1. Container UP + healthcheck Docker ==
# Gate : si le container n'est pas healthy, inutile de continuer.
# Identique au 01 — pourrait être factorisé plus tard quand on aura les
# 6 fichiers et un pattern stable.
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


# == 2. TCP probe 127.0.0.1:4002 depuis l'host ==
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
def section_3_secrets() -> None:
    print("\n== 3. secrets en env (length-only check) ==")
    for key in ("IB_USERID", "IB_PASSWORD", "VNC_PASSWORD"):
        val = os.environ.get(key, "")
        record(f"{key} set", bool(val), f"length = {len(val)}" if val else "MISSING")


# == 4. IBC login status (parse docker logs) ==
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


# == 5. reqAccountSummary — tags critiques renseignés ==
# Ce que tu dois voir : `NetLiquidation`, `BuyingPower`, `AvailableFunds`
# présents avec valeur numérique strictement > 0. La currency est celle
# que l'utilisateur a configurée dans IB (EUR pour FR, USD pour US, GBP
# pour UK…), peu importe laquelle tant qu'elle est cohérente.
#
# `accountSummary()` retourne typiquement plusieurs entries par tag —
# une par devise présente sur le compte (ex: `NetLiquidation EUR`,
# `NetLiquidation USD`, `NetLiquidation BASE`). On cherche pour chaque
# tag la première entry avec valeur numérique > 0, peu importe la
# currency.
def section_5_account_summary(ib: IB) -> dict[str, tuple[float, str]] | None:
    print("\n== 5. reqAccountSummary — tags critiques ==")
    try:
        summary = ib.accountSummary()
    except Exception as e:
        record("accountSummary() call", False, f"{type(e).__name__}: {e}")
        return None

    record("accountSummary() returned non-empty", bool(summary),
           f"{len(summary)} entries")
    if not summary:
        return None

    # Group entries by tag. AccountValue: account, tag, value, currency.
    # Plusieurs entries par tag (une par devise + parfois "BASE").
    by_tag: dict[str, list] = {}
    for av in summary:
        by_tag.setdefault(av.tag, []).append(av)

    parsed: dict[str, tuple[float, str]] = {}

    for tag in REQUIRED_TAGS:
        entries = by_tag.get(tag, [])
        if not entries:
            record(f"tag {tag} present", False, "<missing>")
            continue

        # Cherche la première entry numérique strictement positive.
        # Ignore les entries en devise BASE qui peuvent être "0" en doublon
        # quand le compte est mono-devise.
        chosen: tuple[float, str] | None = None
        for av in entries:
            try:
                num = float(av.value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(num) and num > 0:
                chosen = (num, av.currency or "<no currency>")
                break

        if chosen is None:
            record(f"tag {tag}", False,
                   f"{len(entries)} entries, none with numeric > 0")
            continue
        num, cur = chosen
        record(f"tag {tag}", True, f"{num:,.2f} {cur}")
        parsed[tag] = chosen

    # Info supplémentaire : devise de base du compte (utile pour les
    # engines de risk qui font des conversions FX).
    base_entries = [av for av in summary if av.tag in ("AccountType", "Currency", "BaseCurrency")]
    base_currency = next(
        (av.value for av in base_entries if av.tag == "Currency" and av.value),
        None,
    ) or next(
        (cur for _, cur in parsed.values()),
        "<unknown>",
    )
    record("base currency", True, base_currency)

    return parsed if len(parsed) == len(REQUIRED_TAGS) else None


# == 6. Paper guard — l'account ID commence par "DU" ==
# Ce que tu dois voir : `ib.managedAccounts()` rend la liste des comptes
# tradables (ex: `['DUM194375']`), tous préfixés `DU` (compte paper IB).
# Tout autre préfixe = compte live, on FAIL loud — c'est l'interrupteur
# d'arrêt anti-trading-réel-par-accident.
#
# Pourquoi `managedAccounts()` et pas `accountSummary()` : `accountSummary`
# inclut une entrée d'agrégation avec `account="All"` (marker, pas un vrai
# compte) qui pollue le check. `managedAccounts()` est l'API canonique IB
# qui rend uniquement les vrais IDs de comptes tradables.
def section_6_paper_guard(ib: IB) -> None:
    print("\n== 6. paper account guard ==")
    accounts = sorted(ib.managedAccounts() or [])

    if not accounts:
        record("paper account guard", False, "no account ID returned")
        return

    non_paper = [a for a in accounts if not a.startswith(PAPER_ACCOUNT_PREFIX)]
    record(f"all accounts start with '{PAPER_ACCOUNT_PREFIX}'", not non_paper,
           ", ".join(accounts) if not non_paper
           else f"NON-PAPER detected: {non_paper} (all={accounts})")


# == 7. reqPositions — l'API positions répond ==
# Ce que tu dois voir : la liste des positions est rendue (peut être
# vide). On valide juste que l'endpoint répond — le contenu sera testé
# en intégration plus tard quand le risk commencera à snapshotter.
def section_7_positions(ib: IB) -> None:
    print("\n== 7. reqPositions ==")
    try:
        positions = ib.positions()
    except Exception as e:
        record("positions() call", False, f"{type(e).__name__}: {e}")
        return

    record("positions() returned a list", isinstance(positions, list),
           f"{len(positions)} position(s) — vide acceptable" if isinstance(positions, list)
           else f"got {type(positions).__name__}")


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
    print("=== BRIDGE PASS (inside fxvol-internal Docker network) ===")
    print(f"target = {HOST_FROM_DOCKER}:{PORT}, clientId = {CLIENT_ID}\n")
    ib = IB()
    try:
        ib.connect(HOST_FROM_DOCKER, PORT, clientId=CLIENT_ID, timeout=15)
        record("ib.connect()", ib.isConnected(), f"serverVersion=v{ib.client.serverVersion()}")
    except Exception as e:
        record("ib.connect()", False, f"{type(e).__name__}: {e}")
        return _print_summary("[bridge] ")

    try:
        if section_5_account_summary(ib):
            section_6_paper_guard(ib)
            section_7_positions(ib)
        else:
            print("\n  [SKIP] sections 6 et 7 (account summary incomplet)")
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
        print("\n  [SKIP] bridge pass (IBC pas loggé, sections 5/6/7 inutiles)")
        return host_fail

    repo_root = Path(__file__).resolve().parent.parent.parent
    rel_script = "scripts/smoke/ib-gateway/02_test_account.py"
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
# | Symptôme                                       | Cause probable                              | Fix                                                                          |
# |------------------------------------------------|---------------------------------------------|------------------------------------------------------------------------------|
# | accountSummary() returned 0 entries            | IBC vient juste de finir login              | Re-run après ~5s, IB met du temps à pousser le first snapshot account        |
# | tag NetLiquidation missing                     | Compte sans positions ni cash (compte neuf) | Connecte-toi à la GUI paper, accepte la pop-up market data warning           |
# | tag <X> ≤ 0                                    | Compte vidé / paper reset                   | https://www.interactivebrokers.com — Reset Paper Trading Account             |
# | base currency = <unknown>                      | Tag Currency absent, multi-devises atypique | Inspecter manuellement: ib.accountSummary() pour voir les devises présentes  |
# | NON-PAPER detected (préfixe ≠ DU)              | TWS_USERID pointe sur un compte live !      | STOP IMMEDIAT — vérifier /fxvol/prod/IB_USERID dans SSM, rotater si erreur    |
# | positions() returned not a list                | API désynchronisée                          | Restart container : docker compose --profile ib restart ib-gateway           |

if __name__ == "__main__":
    sys.exit(main())
