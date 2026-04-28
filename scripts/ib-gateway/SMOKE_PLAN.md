# `scripts/ib-gateway/` — plan de refonte smoke tests

> Aligner ce dossier sur le format des autres containers (`redis/`, `api/`,
> `db-writer/`, `nginx/`, `postgresql/`) : notebooks `0N_test_<aspect>.ipynb`,
> chaque section ouvre par une cellule markdown **« Ce que tu dois tester »**,
> re-runnable bout-à-bout, troubleshooting cheat sheet en fin de notebook.

---

## 1. Contexte container

| Item | Valeur |
|---|---|
| Service compose | `ib-gateway` (profile `ib`) |
| Image | `${IB_GATEWAY_IMAGE:-ghcr.io/gnzsnz/ib-gateway:latest}` (fallback build local : `infrastructure/ib-gateway/upstream/`) |
| Ports | `127.0.0.1:4002` (TWS API) · `127.0.0.1:5900` (VNC) |
| Healthcheck | `nc -z 127.0.0.1 4002` (TCP probe, 30s interval, 90s start_period) |
| Runtime deps | **aucune** (feuille du graphe `docs/container_deps.md`) |
| Test deps | compte IB **paper** actif · secrets SSM `IB_USERID` / `IB_PASSWORD` / `VNC_PASSWORD` chargés via `scripts/load_secrets.ps1` |
| Lancement | `docker compose --profile ib up -d ib-gateway` |
| Client IDs déjà réservés | `1` (orders, app PyQt v1) · `2` (market-data engine) · `3` (risk engine) · `14` (vol-engine notebooks legacy) — choisir un ID **libre ≥ 100** pour les smoke tests pour ne pas voler la session d'un service prod |

> **Règle absolue** : aucun smoke test ne doit faire `print` d'une valeur de
> secret. Vérifier les vars uniquement via `len()` ou booléen (cf. CLAUDE.md
> § « zéro exposition des secrets »).

---

## 2. Ce qu'on refait

Les 6 notebooks legacy + leurs CSV de sortie ont été **supprimés**
(recherche exploratoire pré-R9, récupérable via
`git log --all --diff-filter=D -- scripts/ib-gateway/` si besoin un jour).
Le dossier ne contient **que** des smoke tests au format `0N_test_*.ipynb`,
aligné strictement avec `redis/`, `api/`, `db-writer/`, `nginx/`,
`postgresql/`.

### Traçabilité legacy → smoke

Chaque notebook supprimé est tracé soit vers un smoke ib-gateway, soit
vers un autre container (le smoke ib-gateway ne valide **pas** le modèle
de vol — c'est le job du smoke `vol-engine` et des unit tests de
`src/core/vol/`).

| Notebook supprimé | Couvert par smoke ib-gateway ? | Où / Pourquoi |
|---|---|---|
| `list_fop_expiries.ipynb` | ✅ oui | `04_test_options_chain` — `reqSecDefOptParams` rend ≥ 6 expiries EUU |
| `list_fop_strikes.ipynb` | ✅ oui | `04_test_options_chain` — chaîne strikes + qualify FOP ATM |
| `future_booking.ipynb` | ✅ oui | `06_test_security_surface` — `placeOrder` MKT 1 lot FUT paper + `cancelOrder` |
| `option_booking.ipynb` | ✅ oui | `06_test_security_surface` — `placeOrder` LMT 1 lot FOP ATM paper + `cancelOrder` |
| `vol_mid.ipynb` (scan IV C+P, PCHIP, RR/BF/ATM, 6 tenors) | ❌ hors scope | Logique modèle de vol → **smoke `vol-engine`** (à venir) + unit tests `src/core/vol/`. `04` se contente de prouver que `modelGreeks.impliedVol > 0` et `delta ∈ ]−1, 1[` sur 1 strike ATM, soit la **surface IB**, pas le smile fitté. |
| `vol_fair.ipynb` (GARCH / Yang-Zhang / σ_fair) | ❌ hors scope | Domaine pur, testable sans IB → unit tests `src/core/vol/` + smoke `vol-engine`. |

Distinction clé à garder en tête : un smoke ib-gateway prouve que le
**container parle correctement à TWS** (TCP, login IBC, market data,
ordres, options chain). Il ne prouve **pas** que les calculs financiers
sont corrects.

### À créer (smoke tests, format aligné avec les autres containers)

| # | Fichier | Couvre | Dépendances de run |
|---|---|---|---|
| 01 | `01_test_connection.ipynb` | Container UP, TCP probe, login IBC réussi, `IB.connect()` qui rend `isConnected() == True`, `reqCurrentTime()` qui renvoie un timestamp serveur cohérent (±5s vs local), `disconnect()` propre. | `--profile ib up -d ib-gateway` + secrets chargés |
| 02 | `02_test_account.ipynb` | `reqAccountSummary()` renvoie les tags clés (`NetLiquidation`, `BuyingPower`, `AvailableFunds`, devise = `USD`), compte = paper (préfixe `DU`), `reqPositions()` rend une liste (vide acceptable). | idem 01 |
| 03 | `03_test_market_data.ipynb` | `reqContractDetails(EUR FUT CME)` rend ≥ 1 contrat, `reqMktData(front_fut)` avec `reqMarketDataType(3)` (delayed) renvoie bid/ask/last cohérents en < 5s, `cancelMktData()` propre. Optionnel : tester live data si entitlement actif. | idem 01 |
| 04 | `04_test_options_chain.ipynb` | `reqSecDefOptParams()` sur EUR/CME rend une chaîne EUU avec ≥ 6 expiries futures, `reqContractDetails(FOP)` qualifie un strike ATM, `reqMktData(..., genericTickList="100")` renvoie `modelGreeks` avec `impliedVol > 0` et `delta` ∈ ]−1, 1[. | idem 01 |
| 05 | `05_test_resilience.ipynb` | Concurrence : 3 `IB()` clients avec IDs distincts coexistent (réplique du modèle 3-threads de l'app PyQt v1). Reconnexion : `disconnect()` puis `connect()` immédiat → OK. Bad client ID : connect avec un ID déjà pris → erreur attendue (code 326). Stale session : container restart → premier `connect()` post-restart < 90s (start_period). | idem 01 |
| 06 | `06_test_security_surface.ipynb` | `READ_ONLY_API=no` autorise placement d'ordre paper sur **les deux surfaces** : (a) FUT EUR/CME `placeOrder` MKT 1 lot + `cancelOrder` (reprise de `future_booking.ipynb`), (b) FOP EUR/CME ATM 1 lot `placeOrder` LMT loin du marché + `cancelOrder` (reprise de `option_booking.ipynb`). Bascule `READ_ONLY_API=yes` → les deux `placeOrder` rejetés (code 201/202). VNC port `5900` bind sur `127.0.0.1` uniquement (pas de LAN exposure). | idem 01 + restart container avec env modifié |

> **Pourquoi 6 notebooks et pas 1 monolithique** : alignement strict avec le
> pattern existant (`postgresql/` a 3 notebooks, `redis/` 2, etc.). Surtout :
> le notebook 06 nécessite un restart container avec env modifié → on ne peut
> pas le mélanger avec les autres sans casser le `Restart & Run All` du
> reste. Et notebook 05 prend 1-2 min (start_period 90s) alors que 01-04
> sont rapides → séparer pour itérer vite en dev.

---

## 3. Squelette détaillé par notebook

Chaque notebook commence avec **le header standard** (cf.
`scripts/redis/01_test_pubsub.ipynb` ll. 5-25) qui rappelle :

```
# 0N — Test ib-gateway (<aspect>)

Smoke test du container `fxvol-ib-gateway`. <Deps depuis container_deps.md>

**Couvre** :
1. ...
2. ...

**Préreq** :
- Container démarré : `docker compose --profile ib up -d ib-gateway`
- Healthcheck vert : `docker ps --filter name=fxvol-ib-gateway --format '{{.Status}}'`
- Secrets en env : `IB_USERID`, `IB_PASSWORD`, `VNC_PASSWORD`
- `pip install ib_insync` (déjà dans requirements.txt)

**Référence** : `infrastructure/ib-gateway/README.md`, `docker-compose.yml § ib-gateway`
```

### Cellules récurrentes (à factoriser dans chaque notebook, pas dans un module — les smoke notebooks doivent être autonomes)

```python
# Setup — connexion sandbox (CLIENT_ID hors plage prod)
from ib_insync import IB, Contract, util
util.patchAsyncio()

HOST, PORT, CLIENT_ID = "127.0.0.1", 4002, 199  # ID ≥ 100 = sandbox

ib = IB()
ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=15)
assert ib.isConnected(), "IB Gateway unreachable on 4002"
print(f"connected — server time = {ib.reqCurrentTime()}")
```

```python
# Cleanup (toujours en dernière cellule)
if ib.isConnected():
    ib.disconnect()
```

### Patterns de cellule « Ce que tu dois tester » (markdown)

```markdown
## Étape 2 — `reqAccountSummary` renvoie les tags clés

**Ce que tu dois tester** :
- La cellule suivante doit imprimer `NetLiquidation`, `BuyingPower`,
  `AvailableFunds` avec une valeur numérique **> 0** et `Currency == "USD"`.
- Le numéro de compte doit commencer par `DU` (paper). S'il commence par
  `U`, **STOP** : tu es connecté sur le compte live, débrancher.
- Si la cellule timeout après 10s → IB Gateway n'a pas fini son login IBC.
  Attendre que `docker logs fxvol-ib-gateway --tail 50` montre
  `Login has completed`.
```

### Troubleshooting cheat sheet (fin de chaque notebook)

| Symptôme | Cause probable | Fix |
|---|---|---|
| `ConnectionRefusedError` sur `connect()` | Container pas encore healthy (start_period 90s) | Attendre, ou `docker logs fxvol-ib-gateway` pour vérifier IBC login |
| `Error 326 — Unable to connect: client id is already in use` | Un autre process tient déjà ce CLIENT_ID | Changer pour un ID libre ≥ 100 |
| `Error 502 — Couldn't connect to TWS` | TWS API pas encore prête, ou READ_ONLY_API mal configuré | Restart container, vérifier `docker compose config` |
| `modelGreeks is None` après `reqMktData(..., "100")` | Marché fermé ET `reqMarketDataType(3)` pas appelé | Ajouter `ib.reqMarketDataType(3)` (delayed) avant le `reqMktData` |
| Login crash-loop avec `2FA timeout` | `TWOFA_TIMEOUT_ACTION` mal configuré ou push iPhone non validé | Vérifier compose : `TWOFA_TIMEOUT_ACTION: restart` ; valider la notif IB Key |
| `nc -z 127.0.0.1 4002` OK mais `connect()` hang | Session IB stale (gateway up sans login) | `docker compose --profile ib restart ib-gateway` |

---

## 4. Ce qu'on **ne couvre pas** dans ces smoke tests

- **Test bout-à-bout d'un trade** (place + fill + book) → c'est le job du
  smoke test du `risk-engine` ou de l'`order_ticket_panel` côté UI, pas
  de la validation du container ib-gateway en isolation.
- **Test de la qualité du modèle de vol** (smile fitting, calibration SVI)
  → c'est le job des notebooks `research/` (vol_mid, vol_fair existants)
  et des unit tests de `src/services/vol/`.
- **Test du fork `gnzsnz` vs `unusualalpha`** → couvert au niveau
  `infrastructure/ib-gateway/README.md`, pas un smoke test.
- **Test de la rotation des secrets SSM** → géré par `scripts/load_secrets.ps1`,
  hors scope container.

---

## 5. Ordre de création (sandbox r9 → PRs futures)

1. Sur `sandbox/r9-pipeline-verif` (branche actuelle) : créer les 6 notebooks
   l'un après l'autre, valider chacun en `Restart & Run All`, commiter
   atomiquement (un commit par notebook, sujet `test(ib-gateway): add 0N
   smoke notebook for <aspect>`).
2. Une fois les 6 notebooks verts en local + revue manuelle Valérian, le
   décompactage en PRs propres se fera depuis sandbox vers `feat/r9-*`
   selon `releases/git_management/PLAYBOOK.md`. **Ne pas créer de branche
   feature mid-sandbox** (cf. memory `feedback_sandbox_no_branching`).

---

## 6. Critère d'acceptation global

- `Restart & Run All` des 6 notebooks passe bout-à-bout sur une stack
  fraîchement démarrée (`docker compose --profile ib up -d`) en < 5 min cumulé.
- Chaque notebook est autonome (pas d'import croisé entre eux).
- Aucun secret n'apparaît dans une sortie de cellule, ni dans un éventuel
  CSV exporté.
- Les CLIENT_IDs utilisés sont **tous ≥ 100** pour ne pas entrer en
  collision avec les services prod (1, 2, 3) ni avec les notebooks de
  recherche (14).
