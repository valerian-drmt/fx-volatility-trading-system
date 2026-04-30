# container — `frontend`

**Image** : nginx static (build Vite multi-stage)
**Container** : `fxvol-frontend` derrière `fxvol-nginx`
**État** : ✅ existe — dev console 8 tabs
**Steps** : tous (UI)

---

## Rôle

SPA React 18 + TypeScript + Vite. Routing maison path-based (pas de react-router) :
- `/` → user-facing dashboard (à construire pour v1.0)
- `/dev` → console développeur (état actuel)

Tous les onglets restent **mountés** au load (display:none toggle), ce qui préserve les
states WS / fetch en arrière-plan. Cf. `frontend/src/pages/DevLayout.tsx`.

## Onglets actuels (dev validation backend)

1. 🐳 Stack · Health · Redis
2. 📡 WS Monitor
3. 🗃 DB Explorer
4. 🌊 Vol (Surface, Estimators, Signals, ConfigEditor)
5. 💲 Pricing
6. 📦 Trade Preview
7. 📈 Signals
8. 📝 Orders

Ces onglets restent en sous-section « Dev tools » même après v1.0.

## Onglets à créer (1 par step + backtest)

| Tab | Composant | Backend | Statut |
|---|---|---|---|
| 🚦 Step 1 — Regime | `Step1Regime.tsx` | `/regime/state`, `/regime/history` | à créer |
| 📊 Step 2 — PCA | `Step2Pca.tsx` | `/signals/pca` + WS | à créer |
| 📦 Step 3 — Preview | `Step3Preview.tsx` | `/preview` (existe partiel) | à étendre |
| 🚀 Step 4 — Execution | `Step4Execution.tsx` | `/orders` proxy | à créer |
| 📈 Step 5 — Active | `Step5Positions.tsx` | `/positions/live` + WS exits | à créer |
| 🧪 Backtest | `BacktestRunner.tsx` | `/backtest/*` | à créer |

Les composants reuse les widgets existants (`VolSurface`, `Plotly`, `DbExplorer` snippets).

## Conventions UI

- Auto-refresh 3s (pas de boutons refresh — décision Valérian).
- Aucune valeur fabriquée : si un champ n'est pas calculé, afficher `—` + tooltip
  « not available ». Cf. README §Garde-fous «NE PAS faire» #5.
- Formules math affichées en TeX (KaTeX) pour les concepts non triviaux dans les step tabs.
- Couleurs régime : `RANGE` gris, `TRENDING_UP` vert, `TRENDING_DOWN` rouge,
  `INSUFFICIENT_DATA` orange.

## Mapping steps

L'utilisateur navigue dans l'ordre Step 1 → 5 dans la SPA :
- Tab Step 1 : voit régime live + history → décide de regarder Step 2 ou pas.
- Tab Step 2 : voit z-scores PC1/2/3 + actionable flag → si actionable, ouvre Step 3.
- Tab Step 3 : assemble preview structure → si valid_for_submit, click Submit (déclenche Step 4).
- Tab Step 4 : suivi orders / fills.
- Tab Step 5 : positions actives + exit triggers + delta hedge proposé.

## À faire pour v1.0

- [ ] 6 nouveaux onglets ci-dessus.
- [ ] Reorganisation menu : groupe « Pipeline » (steps) vs « Dev tools » (onglets actuels).
- [ ] Page `/` user-facing (cible release v1.0+).
