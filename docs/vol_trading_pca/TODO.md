# TODO — dette technique globale du projet

> **Source de vérité unique** pour la dette identifiée pendant les reviews
> de Step 1 → Step 5. Chaque step doc référence cette page dans son §14
> "Dette technique" au lieu de dupliquer.
>
> **Convention** :
> - 🔴 = bloquant (doit être fixé avant le step)
> - 🟡 = non-bloquant mais à attaquer post-step
> - 🟢 = nice-to-have, peut attendre
> - ⚰️ = bombe à retardement avec deadline explicite

---

## Step 1 — Regime gating (tag v1.0 done 2026-04-30)

### 🟡 1.1 Dampener convexe au lieu de step function J-5

**Aujourd'hui** : `dampener=True` dès `days_to_event < 5` → `size_mult=0.5` constant.

**Cible** :
```python
def event_size_mult(days_to_event: float) -> float:
    if days_to_event >= 5:    return 1.0
    if days_to_event >= 3:    return 0.8
    if days_to_event >= 1:    return 0.5
    if days_to_event >= 0:    return 0.2   # release-day morning
    if days_to_event >= -1:   return 0.0   # release-day post + next session
    if days_to_event >= -2:   return 0.5
    return 1.0
```

**Pourquoi** : à J-1 d'un NFP, `0.5` est encore agressif. Les desks vol systématiques sortent à 0.1-0.2. Convexité = sortir trop tôt < entrer trop tard (asymétrie payoff).

**Quand** : avant Step 4 (execution) — sinon le sizing live sera mal calibré le jour des events.

**Coût impl.** : ~30 lignes (helper + tests + wire dans gate_decision).

### 🟡 1.2 Stability gate asymétrique

**Aujourd'hui** : 3 cycles cohérents requis dans les 2 sens → 9 min de latence à toute transition.

**Cible** :
- Sortie → calm : exiger 3 cycles (filtre le bruit)
- Entrée → stressed/pre_event : activée immédiatement (réactivité aux vrais events)

```python
if new_label in {"stressed", "pre_event"} and history[0] != new_label:
    return GateDecision.from_label(new_label)  # immediate
# else require 3 cohérents
```

**Pourquoi** : asymétrie payoff = sortir trop vite est moins coûteux qu'entrer trop tard. À J-2 d'un FOMC, attendre 9 min pour bloquer les nouveaux trades = trop long.

**Quand** : avant Step 4. Tester sur ≥ 3 events live observés en Step 1-2.

**Coût impl.** : ~10 lignes dans `regime_engine.gate_decision`.

### 🟡 1.3 Hysteresis sur le seuil pre_event

**Aujourd'hui** : flip pre_event ↔ calm dès que `vol_of_vol` traverse `0.4`.

**Risque** : sur 30j de rolling std, le bruit de mesure de vov est ≈ 0.05-0.10. Oscillations attendues si vov stationne autour de 0.4.

**Détection** : endpoint `GET /api/v1/regime/transitions?days=7` retourne le compteur. Warning si > 5 flips/jour.

**Cible** :
```python
# Hystérésis :
#   entrer pre_event à vov > 0.40
#   sortir vers calm seulement à vov < 0.35

# Ou seuil dynamique :
#   threshold = μ_30j(vov) + 1.5 · σ_30j(vov)
```

**Quand** : si `transitions/day > 5` observé sur 7 jours consécutifs.

**Coût impl.** : ~5 lignes (state-aware classifier).

### ⚰️ 1.4 Parsers dynamiques ECB / BoE / FOMC / Eurostat / ONS

**Aujourd'hui** : 16 dates 2026-2027 hardcodées dans `sources/{ecb,boe,fomc}.py`.

**Bombe** : à chaque changement de calendrier d'une banque centrale, le système devient stale silencieusement. **Deadline Q4 2026** (avant que les dates 2027 ne deviennent obsolètes).

**Cible** : parser HTML/RSS/ICS officiel par source. Spec déjà écrite dans `events_pipeline_spec.md` §4.

**Coût impl.** : ~200 lignes par source × 3-5 sources = ~1000 lignes total.

### 🟢 1.5 Z-score avec IC95% au lieu de z seul

**Aujourd'hui** : panel zone 3 affiche `z` brut. Min N=30 obs (depuis v1.0) évite le bruit. Mais l'utilisateur ne voit pas l'incertitude.

**Cible** : afficher `(z, IC95%)` ; si IC contient 0 → grayed-out automatiquement.

**Quand** : amélioration UX, peut attendre Step 5 (Active positions) où la précision compte plus.

### 🟡 1.6 GMM promotion (heuristic → gmm_v1)

**Aujourd'hui** : GMM tourne en shadow, label pilote toujours par heuristique. 3 gates documentés dans STEP1 §13.

**Trigger de revue** :
- `n_with_gmm ≥ 1000` ET
- `agreement_ratio ≥ 70%` AVEC `len(by_label.keys()) ≥ 2` ET
- Au moins 1 event high-impact traversé live dans le training set

**Action quand vert** : flipper la branche dans `core/vol/regime_engine.py::compute_regime_snapshot`.

---

## Step 2 — PCA signal detection (en cours)

(À compléter au fur et à mesure des reviews.)

### 🟡 2.1 Sign correction stability log

Voir spec STEP2 §5.4 — table `pca_stability_log` non implémentée. Décision Q3 2026.

### 🟡 2.2 PCA fitter container dédié

Aujourd'hui le refit PCA est exposé comme endpoint `POST /admin/pca/refit` (manuel MVP). Cible : container cron hebdo.

---

## Step 3 — Trade preview (à venir)

(Vide pour l'instant.)

---

## Step 4 — Execution (à venir)

(Vide pour l'instant.)

---

## Step 5 — Active positions (à venir)

(Vide pour l'instant.)

---

## Backtest harness (à venir)

(Vide pour l'instant.)

---

## Transverse / infra

### 🟡 T.1 Hardening prod : CI/CD + secrets rotation

- `aws ssm put-parameter --overwrite` runbook pour rotation IB/DB/FRED
- GitHub Actions : ruff + pytest + integration smoke
- Healthchecks Prometheus + Alertmanager

### ⚰️ T.2 IB Gateway auto Trusted IP

À chaque nouvel engine container ou redémarrage, IP doit être whitelist manuellement via VNC. Bombe à retardement quand on déploie EC2.

**Cible** : script qui auto-confirme via VNC ou API IBC. Voir `docs/finale_project/container_market_data.md` §À faire.

### 🟢 T.3 Frontend tests E2E

Pas de tests Playwright sur les onglets du dev console. Risk = régression UI silencieuse à chaque ajout de tab.

---

## Process & convention

- Ce fichier est **gitignored ne PAS pousser** (cf. `releases/` policy de CLAUDE.md). C'est un cahier de notes interne, pas un livrable.
- Toute review (Claude ou humaine) qui identifie une dette doit ajouter une ligne ici avec ID, sévérité, deadline si applicable.
- Quand un item est résolu : déplacer vers `docs/vol_trading_pca/CHANGELOG.md` (à créer) avec date + commit SHA.
