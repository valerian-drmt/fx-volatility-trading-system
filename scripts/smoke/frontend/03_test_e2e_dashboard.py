"""
03 — Test frontend (boot dashboard via Playwright headless)

Smoke test du container `fxvol-frontend` — étape 3/5. Valide qu'un Chrome
headless qui ouvre http://localhost/ voit le dashboard se monter correctement :
AppShell, header, ConnectionIndicator, et les 14 panels métier (3 colonnes)
tous visibles, sans erreur JS console, sans asset 404.

POURQUOI UN .PY ET PAS UN .IPYNB
--------------------------------
Les notebooks Jupyter tournent sur asyncio (SelectorEventLoop sur Windows) qui
NE supporte PAS le subprocess (NotImplementedError sur create_subprocess_exec).
Playwright pilote Chrome via subprocess donc :
- sync_playwright()  → erreur "running in asyncio loop"
- async_playwright() → erreur "NotImplementedError" pour le subprocess

Solution : script standalone qui boot son propre asyncio loop. Format identique
aux autres notebooks smoke (sections numérotées, OK/FAIL inline, récap final,
troubleshooting cheat sheet en docstring) — juste lancé en CLI :

    python scripts/smoke/frontend/03_test_e2e_dashboard.py

PRÉ-REQUIS (one-shot)
---------------------
    python -m pip install playwright
    python -m playwright install chromium

PRÉ-REQUIS STACK
----------------
- Notebooks 01-02 verts (frontend serve correctement)
- Idéalement les autres smokes 01-09 verts (api, engines)
- Stack démarrée et accessible sur http://localhost/

COUVRE
------
1. Boot dashboard `http://localhost/` < 5s + AppShell visible
2. Header + ConnectionIndicator visibles + status WS courant
3. 14 panels rendus (3 colonnes : 3 left + 6 center + 4 right)
4. DOM final = SPA React (pas page nginx default)
5. Aucune erreur JS console (filtre WS retries tolérés au boot)
6. Aucune requête HTTP `/assets/*` failed (= build incohérent si présent)

TROUBLESHOOTING
---------------
- ImportError playwright       → python -m pip install playwright
- chromium binary missing      → python -m playwright install chromium
- §1 timeout 5s                → bundle trop gros OU api lent au boot
- §2 conn-indicator invisible  → useWebSocket plante au boot, voir §5 errors
- §3 panel manquant            → composant a thrown au render, voir §5 errors
- §6 /assets/index-XXX.js 404  → docker compose build --no-cache frontend
"""
from __future__ import annotations

import asyncio
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
PANEL_TIMEOUT_MS = 3_000

# Panels attendus — cf. grep `data-testid` dans frontend/src/components/panels/.
LEFT_PANELS = ["status-panel", "portfolio-panel", "logs-panel"]
CENTER_PANELS = [
    "regime-panel", "pca-panel", "chart-panel",
    "term-panel", "smile-panel", "scanner-panel",
]
RIGHT_PANELS = [
    "trade-preview-panel", "order-ticket-panel",
    "book-panel", "model-health-panel",
]
ALL_PANELS = LEFT_PANELS + CENTER_PANELS + RIGHT_PANELS


def make_recorder(results: list):
    def record(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        sym = "OK" if ok else "FAIL"
        print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")
    return record


async def main() -> int:
    results: list[tuple[str, bool, str]] = []
    errors: list[str] = []
    failed_requests: list[str] = []
    record = make_recorder(results)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: errors.append(f"console.{msg.type}: {msg.text}")
            if msg.type == "error" else None,
        )
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(f"{req.method} {req.url} — {req.failure}"),
        )

        print(f"Chrome headless ready, target = {BASE_URL}\n")

        # ===== 1. Boot dashboard < 5s =====
        print("== 1. boot dashboard ==")
        t0 = time.perf_counter()
        try:
            await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            await expect(page.get_by_test_id("app-shell")).to_be_visible(timeout=PAGE_TIMEOUT_MS)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            record("AppShell visible en < 5s",
                   elapsed_ms < 5_000,
                   f"boot = {elapsed_ms:.0f}ms")
        except Exception as e:
            record("AppShell visible en < 5s", False, f"{type(e).__name__}: {str(e)[:200]}")

        # ===== 2. Header + ConnectionIndicator =====
        print("\n== 2. header + connection indicator ==")
        try:
            await expect(page.get_by_test_id("app-header")).to_be_visible(timeout=PANEL_TIMEOUT_MS)
            record("app-header visible", True)
        except Exception as e:
            record("app-header visible", False, f"{type(e).__name__}: {str(e)[:120]}")

        # ConnectionIndicator est instancié plusieurs fois (1 par stream WS :
        # ticks / risk / system_alerts), tous avec data-testid="conn-indicator".
        # On valide qu'il y en a ≥ 1 visible et on log le data-status de chacun.
        try:
            indicators = page.get_by_test_id("conn-indicator")
            n = await indicators.count()
            visible_count = 0
            statuses = []
            for i in range(n):
                ind = indicators.nth(i)
                try:
                    if await ind.is_visible():
                        visible_count += 1
                        statuses.append(await ind.get_attribute("data-status"))
                except Exception:
                    pass
            record("≥ 1 conn-indicator visible",
                   visible_count >= 1,
                   f"{visible_count}/{n} visible, data-status = {statuses}")
        except Exception as e:
            record("≥ 1 conn-indicator visible", False,
                   f"{type(e).__name__}: {str(e)[:120]}")

        # ===== 3. 14 panels rendus =====
        print("\n== 3. 14 panels rendus ==")
        missing: list[str] = []
        for panel in ALL_PANELS:
            try:
                await expect(page.get_by_test_id(panel)).to_be_visible(timeout=PANEL_TIMEOUT_MS)
            except Exception:
                missing.append(panel)

        record(f"{len(ALL_PANELS) - len(missing)}/{len(ALL_PANELS)} panels visibles",
               not missing,
               f"missing = {missing}" if missing else "all 14 panels rendered")
        for col_name, col_panels in (("left", LEFT_PANELS), ("center", CENTER_PANELS), ("right", RIGHT_PANELS)):
            rendered = [p for p in col_panels if p not in missing]
            print(f"  [INFO] {col_name:6} : {len(rendered)}/{len(col_panels)} — {rendered}")

        # ===== 4. DOM final = SPA React =====
        print("\n== 4. DOM final = SPA React ==")
        html = await page.content()
        is_react_spa = "#root" in html or 'id="root"' in html or "id='root'" in html
        is_nginx_default = "welcome to nginx" in html.lower()
        record("DOM contient #root (SPA React)", is_react_spa, f"size = {len(html)} chars")
        record("DOM ≠ page nginx default", not is_nginx_default,
               "OK" if not is_nginx_default else "WTF: nginx default page servie")

        # ===== 5. Erreurs JS console =====
        print("\n== 5. erreurs JS console ==")
        real_errors = [
            e for e in errors
            if "websocket" not in e.lower()
            and "ws://" not in e.lower()
            and "wss://" not in e.lower()
        ]
        ws_errors = [e for e in errors if e not in real_errors]
        record("aucune erreur JS console (hors WS retries)",
               not real_errors,
               f"{len(real_errors)} erreur(s)" if real_errors else "clean")
        if ws_errors:
            print(f"  [INFO] {len(ws_errors)} WS retry log(s) ignorés (normal au boot)")
        if real_errors:
            print("  [INFO] erreurs détectées :")
            for e in real_errors[:10]:
                print(f"    {e[:200]}")

        # ===== 6. Requêtes failed =====
        print("\n== 6. requêtes failed ==")
        failed_assets = [r for r in failed_requests if "/assets/" in r]
        failed_api = [r for r in failed_requests if "/api/" in r]
        failed_other = [
            r for r in failed_requests
            if r not in failed_assets and r not in failed_api
        ]
        record("aucun /assets/* failed", not failed_assets,
               f"{len(failed_assets)} failed" if failed_assets else "clean")
        if failed_api:
            print(f"  [INFO] {len(failed_api)} /api/ failed (signal backend, pas frontend) :")
            for r in failed_api[:5]:
                print(f"    {r[:200]}")
        if failed_other:
            print(f"  [INFO] {len(failed_other)} autres failed :")
            for r in failed_other[:5]:
                print(f"    {r[:200]}")
        if failed_assets:
            print("  [INFO] /assets/ failed :")
            for r in failed_assets[:10]:
                print(f"    {r[:200]}")

        await context.close()
        await browser.close()

    # ===== Récap =====
    print()
    n_ok = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{'LABEL':<60} STATUS  DETAIL")
    print("-" * 110)
    for label, ok, detail in results:
        sym = "OK" if ok else "FAIL"
        print(f"{label:<60} {sym:<6}  {detail}")
    print("-" * 110)
    print(f"\n{n_ok} OK / {n_fail} FAIL  ({len(results)} total)")

    if n_fail == 0:
        print("\nOK — dashboard boot proprement, panels rendus, no JS error.")
        print("Pass au notebook 04 (WebSocket live update).")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    # Sur Windows, force ProactorEventLoop pour supporter subprocess
    # (sinon NotImplementedError comme côté Jupyter).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
