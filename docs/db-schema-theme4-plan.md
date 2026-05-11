# DB schema refactor — Thème 4 (Settings/Others) — plan d'exécution

> Branche : `db/theme4-settings` sous `sandbox/r11-db-schema`
> Spec : `docs/db-schema-target.md` § Thème 4 (référence target idéale)
> Variante retenue : **mini-B ciblée** — voir § Choix de variante
> Effort estimé : **~3h**

---

## 1. Choix de variante

| | A (doc strict 6→2) | B (fold 3 configs) | **mini-B (retenue) 6→5** | C (renames seuls) |
|---|---|---|---|---|
| Approche | tout fold dans `config` JSONB unifiée + `system_event` | fold ExitRules+DeltaHedge+RiskLimit | fold DeltaHedge+RiskLimit (shape-identique) | renames cosmétiques |
| Pros | 1 table = clean diagram | Real DRY 3→1 | DRY ciblé + 0 perte type safety | 0 risk |
| Cons | Perd VolConfig versioning/UI, perd CHECK ExitRules | Perd CHECK ExitRules + 3 routers à réécrire | Petit scope mais 100% pro | Garde duplication shape-identique |
| Effort | 2j | 1j | 3h | 30 min |

**mini-B retenue** parce que :
- DeltaHedge + RiskLimit ont **shape strictement identique** (`name/value(FLOAT)/unit/description/is_active`) → vraie duplication, fold légitime
- ExitRules a `params(JSONB)` + `CHECK priority BETWEEN 1 AND 10` — shape différent, fold = perte type safety
- VolConfig versionné append-only + admin UI dédié — fold = downgrade ergo
- IbConnectionState est du **state runtime**, pas de la config — rename pour clarifier
- ExitAlert est un **event audit log** avec FK — appartient à Thème 3 (`trade_event`)

Règle pro appliquée : **fold quand shape identique, garder spécialisé sinon**.

---

## 2. Mapping changes

| Avant | Après | Action |
|---|---|---|
| `delta_hedge_config` (4 rows seed) | merged into → `app_config_scalar` | fold avec `namespace='delta_hedge'` |
| `risk_limits` (13 rows seed) | merged into → `app_config_scalar` | fold avec `namespace='risk'` |
| `ib_connection_state` | `ib_session_state` | rename (state semantics, pas connection) |
| `vol_engine_config` | inchangé | garde versioning + admin UI |
| `exit_rules_config` | inchangé | garde JSONB+CHECK |
| `exit_alerts` | inchangé (théme 3 plus tard) | garde audit log |

**Nouvelle table `app_config_scalar`** :

```sql
CREATE TABLE app_config_scalar (
    id          INTEGER PRIMARY KEY,
    namespace   VARCHAR(40) NOT NULL,    -- 'delta_hedge' | 'risk'
    name        VARCHAR(60) NOT NULL,
    value       FLOAT       NOT NULL,
    unit        VARCHAR(20),
    description VARCHAR(300),
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  VARCHAR(40),
    UNIQUE (namespace, name)
);
CREATE INDEX ix_app_config_scalar_ns_active ON app_config_scalar (namespace, is_active);
```

## 3. Migration alembic 024

```python
"""Theme 4: fold delta_hedge_config + risk_limits into app_config_scalar.
Rename ib_connection_state → ib_session_state.

Revises: 023_theme1_renames
"""
def upgrade() -> None:
    # Create unified scalar config table
    op.create_table("app_config_scalar", ...)

    # Copy delta_hedge_config rows with namespace='delta_hedge'
    op.execute("""
      INSERT INTO app_config_scalar
        (namespace, name, value, unit, description, is_active, updated_at)
      SELECT 'delta_hedge', config_name, config_value, unit, description, TRUE, updated_at
      FROM delta_hedge_config
    """)

    # Copy risk_limits rows with namespace='risk'
    op.execute("""
      INSERT INTO app_config_scalar
        (namespace, name, value, unit, description, is_active, updated_at, updated_by)
      SELECT 'risk', limit_name, limit_value, unit, description, is_active, updated_at, updated_by
      FROM risk_limits
    """)

    op.drop_table("delta_hedge_config")
    op.drop_table("risk_limits")

    op.rename_table("ib_connection_state", "ib_session_state")

def downgrade() -> None:
    # Reverse rename
    op.rename_table("ib_session_state", "ib_connection_state")

    # Recreate delta_hedge_config + risk_limits, copy back from scalar
    ...
```

## 4. Code changes

### 4.1 models.py

- New class `AppConfigScalar` mapping `app_config_scalar`
- Delete `DeltaHedgeConfig`, `RiskLimit` classes
- Rename `IbConnectionState.__tablename__` → `"ib_session_state"`

### 4.2 Writers

- Migration 024 seeds (data copy in upgrade) — no runtime writer changes needed for these 2 tables (seed-only patterns confirmed by audit)

### 4.3 Readers — 3 files

**`src/api/routers/positions.py`** :

- `GET /api/v1/positions/delta-hedge-config` (l. 349-356) — query devient :
  ```python
  rows = await db.execute(
      select(AppConfigScalar).where(AppConfigScalar.namespace == "delta_hedge")
  )
  ```
- Response shape garde le même format (`config_name`, `config_value`) par mapping :
  ```python
  return [{"config_name": r.name, "config_value": r.value, ...} for r in rows]
  ```

**`src/api/routers/trade.py`** :

- `_load_limits()` (l. 71-75) devient :
  ```python
  rows = await db.execute(
      select(AppConfigScalar.name, AppConfigScalar.value)
      .where(AppConfigScalar.namespace == "risk", AppConfigScalar.is_active == True)
  )
  return {name: float(value) for name, value in rows}
  ```

**`src/api/routers/dev.py`** :

- `ALLOWED_TABLES` dict (l. 271-294) :
  - Remove `"delta_hedge_config"` + `"risk_limits"`
  - Add `"app_config_scalar": "id"`
  - Rename `"ib_connection_state"` → `"ib_session_state"`

### 4.4 Tests

Tests existants qui touchent ces tables :
- `tests/unit/api/routers/test_trade_submit_gating.py` — adapter mock pour `app_config_scalar` au lieu de `risk_limits` (fixture-level change)
- `tests/unit/engines/execution/test_ib_heartbeat.py` — string `"ib_connection_state"` → `"ib_session_state"` si présent
- Audit complet via grep

---

## 5. Procédure

### Step 1 — Migration alembic 024 (1h)
Créer + tester `alembic upgrade head`.

### Step 2 — models.py refactor (30 min)
Nouvelle class `AppConfigScalar`, delete `DeltaHedgeConfig` + `RiskLimit`, rename `IbConnectionState.__tablename__`.

### Step 3 — Readers refactor (1h)
3 fichiers : `positions.py`, `trade.py`, `dev.py`.

### Step 4 — Tests update + run (30 min)

```powershell
PYTHONPATH=src python -m pytest tests/unit/ -q
```

### Step 5 — Live validation (30 min)

```powershell
docker compose exec api alembic -c src/persistence/alembic.ini upgrade head
docker compose exec postgres psql -U fxvol -d fxvol -c "\dt" | findstr -i "app_config\|ib_session\|delta_hedge\|risk_limits"
curl.exe http://localhost/api/v1/positions/delta-hedge-config
curl.exe http://localhost/api/v1/trade/limits  # if endpoint exists
```

---

## 6. Out of scope (à ne PAS faire en Thème 4)

- Fold `vol_engine_config` → `config` générique (perd Pydantic + admin UI + Redis hot-reload)
- Fold `exit_rules_config` → générique (perd CHECK constraint + JSONB params shape)
- Fold `exit_alerts` (event log, va dans Thème 3 → `trade_event`)
- Suppression de `vrp_default_curve` (vol-engine en a besoin, cf. Thème 1 audit)
- Création d'une table générique `system_event` (low value à ce stade, peut être ajoutée plus tard si besoin observability dédiée)

---

## 7. Roadmap après Thème 4

- **Thème 3 — Trade/Order** (~1.5j) : 10 tables → 4. Le plus de valeur métier.
- **Thème 2 — Portfolio** (~2j) : le plus risqué, à faire en dernier (touche les panels live).
