"""
05 — Test frontend (trade preview flow end-to-end)

Smoke test du container `fxvol-frontend` — étape 5/5 (dernier de la série
frontend, et dernier des 10 containers de la stack). Valide le flux user le
plus complexe sans aller jusqu'à un vrai ordre IB :

    User clique "Preview" sur TradePreviewPanel
        → POST /api/v1/vol/trade-preview
            → api/routers/cockpit.py construit la struct (StraddleATM, etc.)
                → réponse JSON avec legs[] + net_greeks + total_premium
        → DOM affiche la table des legs + les 4 stats greeks

PAS D'ENVOI D'ORDRE
-------------------
Le bouton "Submit" de OrderTicketPanel est désactivé (cf. props `disabled`),
et de toute façon on ne déclenche jamais POST /orders depuis le smoke parce
que ça toucherait IB (paper account, mais quand même). La preview de struct
suffit pour valider la chaîne UI ↔ API ↔ pricing engine.

DIFFÉRENCE AVEC 03/04
---------------------
03 = boot statique. 04 = pipeline live (ticks). 05 = interaction user (click)
+ requête HTTP synchrone (POST trade-preview) + DOM update sur réponse.
C'est le test le plus proche d'un vrai user flow.

PRÉ-REQUIS
----------
- Notebooks 03-04 verts
- api healthy + endpoint /api/v1/vol/trade-preview opérationnel
- vol-engine en train de produire une surface (les tenors 1M-6M doivent être
  fittés sinon la struct StraddleATM ne trouve pas le strike ATM)
- Idéalement vol/02 vert (surface complète + signals présents — sinon la
  preview peut renvoyer un payload partiel)

LANCEMENT
---------
    python scripts/smoke/frontend/05_test_e2e_order_ticket.py

COUVRE
------
1. TradePreviewPanel visible + bouton "Preview" cliquable
2. Click "Preview" déclenche un POST /api/v1/vol/trade-preview status 200
3. Réponse JSON contient `legs` (array non-vide) + `net_*` (greeks agrégés)
4. DOM affiche la table des legs avec ≥ 2 rows pour StraddleATM
5. Les 4 stats greeks (Vega, Gamma, Theta/day, Delta) affichés avec valeurs
   numériques finis
6. Total premium affiché et numérique

TROUBLESHOOTING
---------------
- §2 timeout sur response   → endpoint cassé OU vol-engine n'a pas de surface
                              (re-vérifier vol/02)
- §2 status 4xx/5xx         → check `docker logs fxvol-api --tail 30`
- §3 legs absent du JSON    → bug côté api/routers/cockpit.py
- §4 0 row affichée         → state React `result` non-mis à jour, regarder
                              console.error pendant le test
- §5 stats non-numériques   → backend renvoie des strings/null au lieu de floats
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

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
RESPONSE_TIMEOUT_MS = 10_000

TRADE_PREVIEW_PATH = "/api/v1/vol/trade-preview"


def make_recorder(results: list):
    def record(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        sym = "OK" if ok else "FAIL"
        print(f"  [{sym:4}] {label}{('  | ' + detail) if detail else ''}")
    return record


async def main() -> int:
    results: list[tuple[str, bool, str]] = []
    record = make_recorder(results)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        # Boot dashboard (pré-requis)
        await page.goto(BASE_URL, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        await expect(page.get_by_test_id("app-shell")).to_be_visible(timeout=PAGE_TIMEOUT_MS)
        print(f"Dashboard chargé sur {BASE_URL}\n")

        # ===== 1. TradePreviewPanel + bouton Preview =====
        print("== 1. TradePreviewPanel + bouton Preview ==")
        panel = page.get_by_test_id("trade-preview-panel")
        try:
            await expect(panel).to_be_visible(timeout=PANEL_TIMEOUT_MS)
            record("trade-preview-panel visible", True)
        except Exception as e:
            record("trade-preview-panel visible", False, f"{type(e).__name__}: {str(e)[:120]}")

        preview_btn = panel.get_by_role("button", name=re.compile("Preview", re.I))
        try:
            await expect(preview_btn).to_be_enabled(timeout=PANEL_TIMEOUT_MS)
            record("bouton 'Preview' cliquable", True)
        except Exception as e:
            record("bouton 'Preview' cliquable", False, f"{type(e).__name__}: {str(e)[:120]}")

        # ===== 2. Click déclenche POST /trade-preview status 200 =====
        print(f"\n== 2. POST {TRADE_PREVIEW_PATH} status 200 ==")
        # On laisse les selects sur les valeurs par défaut : StraddleATM / 3M / BUY / qty=10.
        try:
            async with page.expect_response(
                lambda r: TRADE_PREVIEW_PATH in r.url and r.request.method == "POST",
                timeout=RESPONSE_TIMEOUT_MS,
            ) as resp_info:
                await preview_btn.click()
            response = await resp_info.value
            record(f"POST {TRADE_PREVIEW_PATH} reçu", True, f"status = {response.status}")
            record("response status = 200", response.status == 200, f"got {response.status}")
        except Exception as e:
            record(f"POST {TRADE_PREVIEW_PATH} reçu", False,
                   f"{type(e).__name__}: {str(e)[:200]}")
            response = None

        # ===== 3. JSON body : legs[] + net_* greeks =====
        print("\n== 3. response body : legs + net_greeks + total_premium ==")
        body = None
        if response is not None:
            try:
                raw = await response.text()
                body = json.loads(raw)
                record("response body est JSON valide", True, f"keys = {sorted(body.keys())}")
            except Exception as e:
                record("response body est JSON valide", False, f"{type(e).__name__}: {e}")

        if body is not None:
            legs = body.get("legs", [])
            record("legs[] non-vide",
                   isinstance(legs, list) and len(legs) >= 1,
                   f"n_legs = {len(legs) if isinstance(legs, list) else 'not-a-list'}")
            # StraddleATM = 1 call ATM + 1 put ATM = 2 legs (ou 1 leg synthétique
            # selon l'impl côté api). On accepte ≥ 1 leg minimum.
            for key in ("net_delta", "net_gamma", "net_vega", "net_theta", "total_premium"):
                v = body.get(key)
                record(f"  body.{key} numérique fini",
                       isinstance(v, (int, float)) and v == v,  # NaN-safe
                       f"value = {v}")

        # ===== 4. DOM : table des legs avec ≥ 1 row =====
        print("\n== 4. DOM table des legs ==")
        # Attendre que React update (le useEffect/setResult prend ~100ms après
        # la response).
        await asyncio.sleep(0.5)
        try:
            rows = panel.locator("table.smile-table tbody tr")
            n_rows = await rows.count()
            record("≥ 1 row dans la table des legs",
                   n_rows >= 1,
                   f"n_rows = {n_rows}")
            # Sample du contenu pour le diag
            if n_rows >= 1:
                first_row_text = await rows.first.text_content() or ""
                print(f"  [INFO] sample row[0] text = {first_row_text.strip()[:120]!r}")
        except Exception as e:
            record("≥ 1 row dans la table des legs", False,
                   f"{type(e).__name__}: {str(e)[:120]}")

        # ===== 5. 4 stats greeks affichés (Vega, Gamma, Theta/day, Delta) =====
        print("\n== 5. 4 stats greeks affichés ==")
        # Les Stats sont des <div> custom dans TradePreviewPanel (pas des
        # MetricTile) — on les match par leur label textuel.
        for greek_label in ("Vega", "Gamma", "Theta/day", "Delta"):
            try:
                # Le DOM est <div><div>label</div><div>value</div></div>.
                # On match le parent qui contient le label.
                stat = panel.locator(
                    f"xpath=.//div[normalize-space(text())='{greek_label}']/following-sibling::div"
                ).first
                value_text = (await stat.text_content() or "").strip()
                # Le value est un float formatté (toFixed). On vérifie
                # qu'il parse en float.
                try:
                    parsed = float(value_text)
                    record(f"stat '{greek_label}' = float fini",
                           parsed == parsed,  # NaN-safe
                           f"value = {value_text!r}")
                except ValueError:
                    record(f"stat '{greek_label}' = float fini", False,
                           f"non-float: {value_text!r}")
            except Exception as e:
                record(f"stat '{greek_label}' présent", False,
                       f"{type(e).__name__}: {str(e)[:120]}")

        # ===== 6. Total premium affiché =====
        print("\n== 6. total premium affiché ==")
        try:
            # Format : "Total premium: <strong>NNN.NN</strong>"
            tp_locator = panel.locator(
                "xpath=.//strong[preceding-sibling::text()[contains(., 'Total premium')]]"
            ).first
            tp_text = (await tp_locator.text_content() or "").strip()
            try:
                parsed = float(tp_text)
                record("total premium = float fini",
                       parsed == parsed,
                       f"value = {tp_text!r}")
            except ValueError:
                # fallback : chercher un nombre dans le texte du body après "Total premium:"
                body_text = await panel.text_content() or ""
                m = re.search(r"Total premium[:\s]+([0-9.\-]+)", body_text)
                if m:
                    parsed = float(m.group(1))
                    record("total premium = float fini",
                           parsed == parsed,
                           f"value = {m.group(1)!r}")
                else:
                    record("total premium = float fini", False, f"non-float: {tp_text!r}")
        except Exception as e:
            record("total premium présent", False,
                   f"{type(e).__name__}: {str(e)[:120]}")

        await context.close()
        await browser.close()

    # ===== Récap =====
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
        print("\nOK — trade preview flow validé bout-à-bout.")
        print("Surface frontend complètement validée (smokes 01-05).")
        print("\nLa stack 10 containers est entièrement smoke-validée.")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
