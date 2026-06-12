"""
04 — Test frontend (WebSocket live update + reconnect)

Smoke test du container `fxvol-frontend` — étape 4/5. Valide que **les hooks
WebSocket se connectent et que le DOM se met à jour à chaque message** :
ConnectionIndicator passe `open`, les MetricTile (Bid/Ask/Mid/Ticks) reflètent
les ticks live de market-data, et le hook `useWebSocket` reconnecte
automatiquement après un restart du proxy nginx.

DIFFÉRENCE AVEC 03
------------------
03 = boot statique (le DOM monte). 04 = pipeline live (le DOM bouge). Ici on
ne se contente pas de "le panel est rendu" — on regarde la **valeur** affichée,
on attend N secondes, et on vérifie qu'elle a changé. C'est le seul test de
toute la suite smoke qui valide vraiment la chaîne complète :

    market-data engine → Redis publish → api WS bridge → nginx proxy → useWebSocket → DOM

NOTE WIRING UI
--------------
- `useTicks()` est consommé par StatusPanel (Bid/Ask/Mid/Ticks via MetricTile)
  ET par ChartPanel (rendu Plotly). On teste via les MetricTile : selectors
  stables et valeurs textuelles inspectables.
- `useRiskStream()` est défini dans `frontend/src/hooks/useRiskStream.ts` mais
  N'EST CONSOMMÉ par AUCUN composant à ce stade (R9 sandbox). Donc on ne teste
  PAS le live update des greeks dans le DOM (pas de selector à inspecter). Le
  pipeline backend a été validé dans risk/04 (WS bridge api). Le wire frontend
  des greeks est à faire dans une PR future, ce smoke deviendra strict à ce
  moment-là.
- `useSystemAlerts()` → `LogsPanel` (FIFO 50 messages). Pas testé ici (les
  alerts arrivent peu fréquemment, faudrait un trigger artificiel).

PRÉ-REQUIS
----------
- Notebook 03 vert (dashboard boot OK)
- market-data healthy + en train de publier des ticks (heartbeat frais)
- api healthy + WS bridge subscribed à `ticks` channel
- nginx healthy

⚠ DESTRUCTIF (§4 reconnect)
---------------------------
La section 4 fait `docker restart fxvol-nginx`, ce qui déconnecte tous les
clients WS pendant ~5-10s. À lancer en dernier dans la journée si tu fais
des manips parallèles.

LANCEMENT
---------
    python scripts/smoke/frontend/04_test_e2e_websocket.py

COUVRE
------
1. Au moins un ConnectionIndicator passe `data-status="open"` en < 10s
2. `metric-Ticks` (count) > 0 dans les 10s du load (preuve que des ticks arrivent)
3. `metric-Mid` change entre t=0 et t=5s (preuve que le DOM se rafraîchit en live)
4. `metric-Ticks` incrémente strictement entre t=0 et t=5s
5. Après `docker restart fxvol-nginx`, le ConnectionIndicator repasse `open` en < 20s
6. Après reconnect, les ticks reprennent (le count incrémente après le restart)

TROUBLESHOOTING
---------------
- §1 timeout 10s sur "open"      → api WS bridge KO ou nginx WS proxy mal configuré
- §2 metric-Ticks reste à 0       → pas de ticks Redis OU bridge ne forward pas
- §3 metric-Mid ne change pas     → ticks reçus mais hook useTicks bug
- §4 metric-Ticks reste constant  → idem §2/§3
- §5 reconnect timeout            → useWebSocket retry policy cassée (regarde frontend/src/hooks/useWebSocket.ts)
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

try:
    from playwright.async_api import async_playwright, expect
except ImportError:
    print(
        "FAIL : `playwright` non installé.\n"
        "  -> python -m pip install playwright\n"
        "  -> python -m playwright install chromium",
        file=sys.stderr,
    )
    sys.exit(2)


BASE_URL = "http://localhost/"
PAGE_TIMEOUT_MS = 5_000

CONNECT_DEADLINE_S = 10.0
TICK_FRESH_DEADLINE_S = 10.0
LIVE_UPDATE_WINDOW_S = 5.0
RECONNECT_DEADLINE_S = 20.0
RECONNECT_TICK_WINDOW_S = 10.0

NGINX_CONTAINER = "fxvol-nginx"


def make_recorder(results: list):
    def record(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        sym = "OK" if ok else "FAIL"
        print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")
    return record


async def get_first_visible_status(page) -> str | None:
    """Renvoie le data-status du premier conn-indicator visible, ou None."""
    indicators = page.get_by_test_id("conn-indicator")
    n = await indicators.count()
    for i in range(n):
        ind = indicators.nth(i)
        try:
            if await ind.is_visible():
                return await ind.get_attribute("data-status")
        except Exception:
            continue
    return None


async def wait_for_status(page, target: str, timeout_s: float) -> tuple[bool, str | None]:
    """Poll le data-status jusqu'à atteindre `target` ou timeout."""
    deadline = time.perf_counter() + timeout_s
    last = None
    while time.perf_counter() < deadline:
        last = await get_first_visible_status(page)
        if last == target:
            return True, last
        await asyncio.sleep(0.3)
    return False, last


async def read_metric(page, label: str) -> str | None:
    """Lit la VALEUR d'un MetricTile (le span.metric-value, pas le texte complet
    qui inclurait le label + le hint). Cf. frontend/src/components/common/
    MetricTile.tsx — le DOM est <div data-testid="metric-<label>"><span
    class="metric-label">…</span><span class="metric-value">VAL</span>…</div>.
    """
    try:
        loc = page.get_by_test_id(f"metric-{label}").first.locator(".metric-value")
        return (await loc.text_content() or "").strip()
    except Exception:
        return None


async def main() -> int:
    results: list[tuple[str, bool, str]] = []
    record = make_recorder(results)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        await expect(page.get_by_test_id("app-shell")).to_be_visible(timeout=PAGE_TIMEOUT_MS)
        print(f"Dashboard chargé sur {BASE_URL}\n")

        # ===== 1. ConnectionIndicator → "open" en < 10s =====
        print("== 1. WebSocket connect ==")
        ok, last = await wait_for_status(page, "open", CONNECT_DEADLINE_S)
        record(f"≥ 1 conn-indicator data-status='open' en < {CONNECT_DEADLINE_S}s",
               ok,
               f"last status seen = {last!r}")

        # ===== 2. Tick count > 0 dans les 10s =====
        print("\n== 2. tick count > 0 (preuve que ticks arrivent) ==")
        deadline = time.perf_counter() + TICK_FRESH_DEADLINE_S
        count_seen = "0"
        while time.perf_counter() < deadline:
            count_seen = (await read_metric(page, "Ticks") or "0").strip()
            try:
                if int(count_seen) > 0:
                    break
            except ValueError:
                pass
            await asyncio.sleep(0.5)
        try:
            count_int = int(count_seen)
            record(f"metric-Ticks > 0 en < {TICK_FRESH_DEADLINE_S}s",
                   count_int > 0,
                   f"count = {count_int}")
        except ValueError:
            record(f"metric-Ticks > 0 en < {TICK_FRESH_DEADLINE_S}s",
                   False,
                   f"non-numeric value: {count_seen!r}")

        # ===== 3. metric-Mid change en LIVE_UPDATE_WINDOW_S =====
        print(f"\n== 3. metric-Mid change en {LIVE_UPDATE_WINDOW_S}s ==")
        mid_t0 = await read_metric(page, "Mid")
        await asyncio.sleep(LIVE_UPDATE_WINDOW_S)
        mid_t1 = await read_metric(page, "Mid")
        record("metric-Mid valeur change (DOM live)",
               mid_t0 != mid_t1 and mid_t0 not in (None, "—") and mid_t1 not in (None, "—"),
               f"t0 = {mid_t0!r}, t1 = {mid_t1!r}")

        # ===== 4. metric-Ticks incrémente strictement =====
        print(f"\n== 4. metric-Ticks incrémente en {LIVE_UPDATE_WINDOW_S}s ==")
        try:
            count_t0 = int((await read_metric(page, "Ticks") or "0").strip())
            await asyncio.sleep(LIVE_UPDATE_WINDOW_S)
            count_t1 = int((await read_metric(page, "Ticks") or "0").strip())
            record("metric-Ticks incrémente (count strict ↑)",
                   count_t1 > count_t0,
                   f"t0 = {count_t0}, t1 = {count_t1}, delta = {count_t1 - count_t0}")
        except ValueError as e:
            record("metric-Ticks incrémente", False, f"parse error: {e}")

        # ===== 5. Reconnect après nginx restart =====
        # (destructif mais bornes courtes, container nginx restart en ~3-5s)
        print(f"\n== 5. reconnect après docker restart {NGINX_CONTAINER} ==")
        t0 = time.perf_counter()
        out = subprocess.run(
            ["docker", "restart", NGINX_CONTAINER],
            capture_output=True, text=True,
        )
        restart_elapsed = time.perf_counter() - t0
        if out.returncode != 0:
            record("docker restart nginx", False,
                   f"exit={out.returncode}, stderr={out.stderr.strip()[:120]}")
        else:
            record("docker restart nginx", True, f"took {restart_elapsed:.1f}s")
            # Attendre que le statut repasse "open"
            ok, last = await wait_for_status(page, "open", RECONNECT_DEADLINE_S)
            record(f"conn-indicator repasse 'open' en < {RECONNECT_DEADLINE_S}s",
                   ok,
                   f"last status seen = {last!r}")

        # ===== 6. Ticks reprennent après reconnect =====
        print(f"\n== 6. ticks reprennent après reconnect ==")
        try:
            count_pre = int((await read_metric(page, "Ticks") or "0").strip())
            await asyncio.sleep(RECONNECT_TICK_WINDOW_S)
            count_post = int((await read_metric(page, "Ticks") or "0").strip())
            record(f"metric-Ticks incrémente sur les {RECONNECT_TICK_WINDOW_S}s post-reconnect",
                   count_post > count_pre,
                   f"pre = {count_pre}, post = {count_post}, delta = {count_post - count_pre}")
        except ValueError as e:
            record("ticks reprennent", False, f"parse error: {e}")

        await context.close()
        await browser.close()

    # ===== Récap =====
    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{'LABEL':<70} STATUS  DETAIL")
    print("-" * 120)
    for label, ok, detail in results:
        sym = "OK" if ok else "FAIL"
        print(f"{label:<70} {sym:<6}  {detail}")
    print("-" * 120)
    print(f"\n{n_ok} OK / {n_fail} FAIL  ({len(results)} total)")

    if n_fail == 0:
        print("\nOK — WebSocket pipeline live + reconnect validés.")
        print("Pass au notebook 05 (order ticket trade-preview flow).")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
