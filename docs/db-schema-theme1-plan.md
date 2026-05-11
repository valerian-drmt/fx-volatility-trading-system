# DB schema refactor — Thème 1 (Vol / Data / Indicators) — plan d'exécution

> Branche : `db/theme1-vol-indicators` sous `sandbox/r11-db-schema`
> Spec : `docs/db-schema-target.md` § Thème 1 (référence target idéale)
> Variante retenue : **B (pragmatic, audit-driven)** — voir § Choix de variante
> Effort estimé : **~3h**

---

## 1. Choix de variante

| | Variante A (doc strict) | **Variante B (retenue)** |
|---|---|---|
| 10 tables → | 4 polymorphes | 8 typées |
| Approche | `feature_history` polymorphe avec `feature_name` + JSONB payload | Renames + drop dead `vrp_default_curve` |
| Pros | Moins de tables | Type safety + indexes natifs préservés |
| Cons | Perd CHECK constraints + indexes par colonne ; refactor lourd | 4 tables de plus |
| Effort | 2-3 j | ~3 h |

**Variante B** est retenue parce que l'audit révèle :
- `feature_history_30d` a 9 colonnes typées avec contraintes utiles
- `PcaModel` + `PcaSignal` ont des schemas radicalement différents (l'un = state versionné, l'autre = time-series), les fold dans un même `feature_history` polymorphe demande 2 layouts JSONB sous un même discriminator
- `vrp_default_curve` est carrément mort en runtime (`core.vol.vrp.VRP_DEFAULTS_VOL_PTS` Python dict est la source de vérité)
- Les renames sont mécaniques + sûrs (Alembic `op.rename_table`)
- Variante A demanderait de réécrire ~10 readers en `payload->>'iv_atm_1m_pct'` au lieu de `iv_atm_1m_pct` — perte de lisibilité durable

---

## 2. Mapping renames

| Avant | Après | Action |
|---|---|---|
| `vol_surface_snapshot` | `vol_surface_history` | rename |
| `surface_snapshots_hourly` | `surface_pca_snapshot_history` | rename (clarifie : c'est l'historique pour PCA fit) |
| `feature_history_30d` | `feature_history` | rename (drop suffixe `_30d` qui mélange schema + rétention) |
| `regime_feature_snapshot` | `regime_snapshot` | rename (drop redondance "feature") |
| `regime_pattern_dict` | `regime_pattern_dict` | **inchangé** — static lookup, fold dans regime_snapshot pas pertinent |
| `vrp_default_curve` | — | **DROP** — table morte (lecture via dict Python) |
| `pca_model` | `pca_model` | inchangé |
| `pca_projection_snapshot` | `pca_signal_history` | rename (clarifie : historique des signaux par PC) |
| `pca_structure_recommendation` | `pca_structure_recommendation` | **inchangé** — static lookup, traité plus tard en Thème 4 (table `config` unifiée) |
| `macro_event` | `event_calendar` | rename (cohérent avec spec target) |

---

## 3. Fichiers à toucher

### 3.1 Persistence layer

**`src/persistence/migrations/versions/023_theme1_renames.py`** (nouveau)

```python
"""Theme 1 schema cleanup: rename 6 vol/indicator tables, drop dead vrp."""
revision = "023"
down_revision = "022"  # ou la rev courante max

def upgrade() -> None:
    op.rename_table("vol_surface_snapshot",     "vol_surface_history")
    op.rename_table("surface_snapshots_hourly", "surface_pca_snapshot_history")
    op.rename_table("feature_history_30d",      "feature_history")
    op.rename_table("regime_feature_snapshot",  "regime_snapshot")
    op.rename_table("pca_projection_snapshot",  "pca_signal_history")
    op.rename_table("macro_event",              "event_calendar")
    op.drop_table("vrp_default_curve")
    # Index naming follows the table — postgres auto-renames most.
    # Verify manually via `psql -c "\\d table_name"` post-migration.

def downgrade() -> None:
    # Recreate vrp_default_curve from migration 010's seed (18 rows).
    # See migration 010 for the schema + bulk_insert.
    op.create_table("vrp_default_curve", ...)
    # Then reverse renames in inverse order.
    op.rename_table("event_calendar",              "macro_event")
    op.rename_table("pca_signal_history",          "pca_projection_snapshot")
    op.rename_table("regime_snapshot",             "regime_feature_snapshot")
    op.rename_table("feature_history",             "feature_history_30d")
    op.rename_table("surface_pca_snapshot_history","surface_snapshots_hourly")
    op.rename_table("vol_surface_history",         "vol_surface_snapshot")
```

**`src/persistence/models.py`** — 6 `__tablename__` updates + 1 class delete

| Ligne actuelle | Nouvelle |
|---|---|
| `class VolSurface: __tablename__ = "vol_surface_snapshot"` (l. 252) | `"vol_surface_history"` |
| `class SurfaceSnapshotHourly: __tablename__ = "surface_snapshots_hourly"` (l. 449) | `"surface_pca_snapshot_history"` |
| `class FeatureHistory: __tablename__ = "feature_history_30d"` (l. 343) | `"feature_history"` |
| `class RegimeSnapshot: __tablename__ = "regime_feature_snapshot"` (l. 271) | `"regime_snapshot"` |
| `class PcaSignal: __tablename__ = "pca_projection_snapshot"` (l. 508) | `"pca_signal_history"` |
| `class Event: __tablename__ = "macro_event"` (l. 362) | `"event_calendar"` |
| `class VrpTableDefault` (l. 390) | **DELETE** la classe entière |

**`src/persistence/writer.py`** — 2 dicts à mettre à jour (`TABLE_MODELS` + `idempotent_keys`)

| Ligne | Modif |
|---|---|
| 61 | `"feature_history_30d"` → `"feature_history"` |
| 62 | `"pca_projection_snapshot"` → `"pca_signal_history"` |
| 65 | `"regime_feature_snapshot"` → `"regime_snapshot"` |
| 66 | `"surface_snapshots_hourly"` → `"surface_pca_snapshot_history"` |
| 68 | `"vol_surface_snapshot"` → `"vol_surface_history"` |
| 83-87 (idempotent_keys dict) | mêmes 5 keys renommées (+ event_calendar si présent) |

### 3.2 Engines

**`src/engines/vol/engine.py`** — 5 `table=...` strings

| Ligne | Avant | Après |
|---|---|---|
| 546 | `table="vol_surface_snapshot"` | `table="vol_surface_history"` |
| 562 | `table="regime_feature_snapshot"` | `table="regime_snapshot"` |
| 566 | `table="feature_history_30d"` | `table="feature_history"` |
| 573 | `table="pca_projection_snapshot"` | `table="pca_signal_history"` |
| 578 | `table="surface_snapshots_hourly"` | `table="surface_pca_snapshot_history"` |

### 3.3 API

**`src/api/orchestration/analytics_service.py:29`** — 1 string
```python
("vol_surface_snapshot", VolSurface)  →  ("vol_surface_history", VolSurface)
```

**`src/api/routers/dev.py`** — 7 dict keys (lignes 278-289)

| Avant | Après |
|---|---|
| `"vol_surface_snapshot"` | `"vol_surface_history"` |
| `"regime_feature_snapshot"` | `"regime_snapshot"` |
| `"feature_history_30d"` | `"feature_history"` |
| `"macro_event"` | `"event_calendar"` |
| `"vrp_default_curve"` | **DELETE** la ligne |
| `"surface_snapshots_hourly"` | `"surface_pca_snapshot_history"` |
| `"pca_projection_snapshot"` | `"pca_signal_history"` |

### 3.4 Frontend

**`frontend/src/components/panels/ModelHealthPanel.tsx:42`** — 1 string display
```tsx
<tr><td>vol_surface_snapshot</td>...  →  <tr><td>vol_surface_history</td>...
```

### 3.5 Aucun changement requis

Audit confirme — tous ces fichiers passent par classes ORM (imports), pas par strings :
- `src/api/routers/cockpit.py`
- `src/api/routers/signals.py`
- `src/api/routers/regime.py`
- `src/api/routers/trade.py`
- `src/api/orchestration/regime_features.py`
- `src/api/orchestration/vol_service.py`
- `src/api/orchestration/position_monitor.py`
- `src/api/orchestration/events/repository.py`
- `src/api/orchestration/events/sources/*.py`
- `tests/unit/**/*.py` (fixtures ORM-based, aucune hardcoded string)
- `scripts/smoke/**/*` (aucune ref aux table names)

### 3.6 Suppression dead code

- `src/persistence/models.py` : delete class `VrpTableDefault` (lignes ~390-414)
- `src/core/vol/vrp.py` : aucune dépendance à VrpTableDefault, garder le dict `VRP_DEFAULTS_VOL_PTS`
- Aucun reader actif sur `vrp_default_curve` (audit confirme : tout passe par le dict Python)

---

## 4. Procédure d'exécution

### Step 1 — Préparer (5 min)

```powershell
git switch db/theme1-vol-indicators
git status   # working tree clean attendu
```

### Step 2 — Migration alembic (1h)

1. Créer `src/persistence/migrations/versions/023_theme1_renames.py`
2. Vérifier la revision parent (`down_revision = "022"` ou la dernière)
3. Tester localement :
   ```powershell
   docker compose exec api alembic -c src/persistence/alembic.ini upgrade head
   docker compose exec postgres psql -U fxvol -d fxvol -c "\dt"
   # → doit montrer les 6 tables renommées + absence de vrp_default_curve
   ```

### Step 3 — Refactor models + writer (30 min)

1. Update `src/persistence/models.py` (6 `__tablename__` + delete VrpTableDefault class)
2. Update `src/persistence/writer.py` (TABLE_MODELS + idempotent_keys dicts)
3. Run unit tests :
   ```powershell
   PYTHONPATH=src python -m pytest tests/unit/persistence/ -v
   ```

### Step 4 — Refactor engines + api (30 min)

1. Update `src/engines/vol/engine.py` (5 `table=...`)
2. Update `src/api/orchestration/analytics_service.py` (1 string)
3. Update `src/api/routers/dev.py` (7 dict keys)
4. Update `frontend/src/components/panels/ModelHealthPanel.tsx` (1 string display)
5. Run :
   ```powershell
   PYTHONPATH=src python -m pytest tests/unit/api/ tests/unit/engines/ -v
   cd frontend && npm run build
   ```

### Step 5 — Test live (30 min)

```powershell
docker compose --profile engines up -d --build vol-engine api
Start-Sleep 30
# vol-engine doit publier dans les nouvelles tables
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT count(*) FROM vol_surface_history; SELECT count(*) FROM feature_history;"
# Frontend Portfolio panels doivent toujours afficher data
```

### Step 6 — Smoke notebooks (15 min)

```powershell
# Re-run scripts/smoke/vol-engine/*.ipynb
# Re-run scripts/smoke/api/*.ipynb
# Vérifier aucune assertion sur les anciens noms ne plante
```

### Step 7 — Commit + push (10 min)

```powershell
git add -A
git commit -m "feat(db): theme 1 — rename 6 vol/indicator tables, drop dead vrp_default_curve

10 tables → 8 (renames + 1 drop). Variante B audit-driven (vs doc target
polymorphic JSONB) : conserve type safety + CHECK constraints + indexes
natifs. Effort réel ~3h."

git push -u origin db/theme1-vol-indicators
```

### Step 8 — Update doc (5 min)

- Mettre à jour `docs/db-schema-target.md` § Thème 1 : noter "Livré Variante B (audit-driven)" + tableau renames effectifs
- Cocher Thème 1 dans la roadmap migration § 5

---

## 5. Rollback

Si quelque chose foire mid-migration en local :

```powershell
docker compose exec api alembic -c src/persistence/alembic.ini downgrade -1
git checkout HEAD~1 -- src/persistence/models.py src/persistence/writer.py src/engines/vol/engine.py src/api/orchestration/analytics_service.py src/api/routers/dev.py frontend/src/components/panels/ModelHealthPanel.tsx
git restore --staged .
```

Sur main : ne touche pas main avant que Thème 1 soit 100% green en local + tests + smoke. Décomposer en PR atomique quand prêt à push.

---

## 6. Checkpoint post-livraison

| Critère | Vérification |
|---|---|
| Alembic upgrade head clean | `docker compose exec api alembic upgrade head` zero error |
| `\dt` montre 6 renamed + absence vrp_default_curve | psql interactive |
| Unit tests passent | `pytest tests/unit/` exit 0 |
| Frontend build passe | `npm run build` exit 0 |
| vol-engine écrit dans nouvelles tables | `SELECT count(*) > 0 FROM vol_surface_history` après 1 cycle |
| Panels portfolio affichent data | Inspection visuelle dans browser |
| Smoke notebooks re-run vert | Pas d'assertion FAIL |

---

## 7. Suites possibles

- **Thème 4 — Settings** (~1j) : fold `pca_structure_recommendation` + `regime_pattern_dict` + autres configs dans une table `config` versionnée JSONB. Branchera sur `db/theme4-settings` depuis `sandbox/r11-db-schema`.
- **Thème 3 — Trade/Order** (~1.5j) : restructure des 10 tables trade/exécution.
- **Thème 2 — Portfolio** (~2j) : le plus risqué, à faire en dernier.
