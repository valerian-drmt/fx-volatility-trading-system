# Spec implémentation observabilité LGTM — fxvol-trading

> Cible : Claude Code, exécution autonome multi-phases.
> Solo dev, 1 host (WSL2 dev → EC2 prod), paper trading aujourd'hui, ambition live trading 6-12 mois.
> Stack actuelle : 6 containers Python (api, market-data, vol-engine, risk-engine, db-writer, execution-engine) + Postgres/Redis/nginx/ib-gateway + frontend React+Vite, communication Redis pub/sub + cache, persistance via db-writer unique writer.

---

## 0. Contexte et contraintes

### Pain points observés à résoudre

1. **Heartbeat binaire insuffisant** : on sait que l'engine est "up" mais pas si le cycle a réellement abouti, si IB a renvoyé une chain, si le timeout silent a sauté l'étape.
2. **Cohérence end-to-end non vérifiée** : bug récent `positions.market_price = spot au lieu du contract price` non détecté par tests unitaires.
3. **Diagnostic multi-engine coûteux** : "panel E vide" prend 15 min de creusage pour distinguer "execution n'a pas publié" vs "risk n'a pas lu" vs "serializer ignore le champ".
4. **Smoke notebooks valident isolé, pas pipeline live** : changement dans `bus/publisher.py` qui casse événement écouté par db-writer passe les tests.
5. **IB Gateway flap silencieux** : session coupée détectée 30 min plus tard.

### Contraintes dures

- **Solo dev** → coût de maintenance > coût de setup. Toute techno qui demande tuning continu = perte sèche.
- **1 host** → pas de cluster, pas de HA. Single-binary mode partout.
- **Pas de HFT** → cycle vol 180s, risk 2s, fenêtres minutes/heures. Sampling probablement inutile, full ingestion OK.
- **WSL2 → EC2** : tout doit fonctionner identique en dev et prod, pas de `if ENV == "dev"`.
- **Volume cible** : ~100k DB rows/jour, ~50-200 ticks/s RTH, ~500 strikes scannés par chain refresh, ~5-15 positions ouvertes.
- **Garder `docker compose logs -f` opérationnel** : ne pas régresser l'UX immédiate.
- **Vitesse d'exécution = variable critique roadmap** : ne pas dépasser le budget temps par phase.

### Conventions à respecter

- Tag `v1.0` avant passage phase suivante. Pas de repo à 70%.
- Tous les commits avec préfixe `feat(obs):`, `fix(obs):`, `chore(obs):`.
- Documentation des conventions de naming (spans, labels, metric names) dans `CLAUDE.md` à mesure.
- Aucune feature OTel/LGTM ne doit casser un test existant. Run `pytest` complet à la fin de chaque phase.

---

## 1. Architecture cible

```
                    ┌──────────────────────────────────────────┐
                    │     5 engines Python instrumentés         │
                    │  (market-data, vol, risk, exec, writer)   │
                    └────┬──────────────┬──────────────┬───────┘
                         │ logs         │ traces        │ metrics
                         │ (structlog)  │ (OTel SDK)    │ (/metrics)
                         ▼              ▼               ▼
                    ┌────────────────────────────────────────┐
                    │         otel-collector (port 4317)      │
                    │      batch, retry, fan-out routing      │
                    └───┬───────────────┬────────────────┬───┘
                        ▼               ▼                ▼
                  ┌─────────┐     ┌──────────┐    ┌────────────┐
                  │  Loki   │     │  Tempo   │    │ Prometheus │
                  │ :3100   │     │  :3200   │    │   :9090    │
                  └────┬────┘     └─────┬────┘    └─────┬──────┘
                       └────────────────┼───────────────┘
                                        ▼
                                  ┌──────────┐
                                  │ Grafana  │
                                  │  :3000   │
                                  └──────────┘
```

**Volumes Docker dédiés** : `loki-data`, `tempo-data`, `prom-data`, `grafana-data`. Tous montés en volumes nommés (pas bind mounts) pour portabilité WSL2 → EC2.

---

## 2. Phases d'implémentation

Trois phases séquentielles. Chaque phase est tagguée v1.0 avant passage suivante.

### Phase 0 — Fondations (1 jour, branche `obs/p0-foundations`)

Objectif : avoir des logs structurés exploitables et des metrics de base, **sans backend externe**. Permet de débugger les bugs simples sans dépendance.

#### Livrables

1. **Cycle ID propagé**. Ajouter dans `src/bus/context.py` (créer si inexistant) :
   ```python
   from contextvars import ContextVar
   from uuid import uuid4

   cycle_id_var: ContextVar[str | None] = ContextVar("cycle_id", default=None)
   trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

   def new_cycle() -> str:
       cid = uuid4().hex
       cycle_id_var.set(cid)
       return cid
   ```
   Modifier chaque entry point de cycle (`src/engines/vol/engine.py:run_cycle`, idem risk, exec, market-data, db-writer) pour appeler `new_cycle()` au début.

2. **Structlog binding automatique**. Modifier la config structlog globale pour binder `cycle_id` et `trace_id` depuis les ContextVar via un processor custom :
   ```python
   def add_context_ids(logger, method_name, event_dict):
       if cid := cycle_id_var.get():
           event_dict["cycle_id"] = cid
       if tid := trace_id_var.get():
           event_dict["trace_id"] = tid
       return event_dict
   ```
   Ajouter ce processor en début de chaîne dans `src/observability/logging.py` (créer si nécessaire).

3. **Endpoint `/metrics` Prometheus sur chaque engine**. Utiliser `prometheus-client` :
   ```python
   from prometheus_client import Counter, Histogram, Gauge, start_http_server

   cycles_total = Counter("engine_cycles_total", "Cycles completed", ["engine", "status"])
   cycle_duration = Histogram("engine_cycle_duration_seconds", "Cycle duration", ["engine"],
                              buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120])
   ib_session_up = Gauge("ib_session_connected", "IB session state", ["client_id"])
   last_cycle_ts = Gauge("engine_last_cycle_timestamp_seconds", "Unix ts of last cycle end", ["engine"])
   ```
   Démarrer le serveur HTTP metrics sur port différent par engine :
   - market-data : 9101
   - vol-engine  : 9102
   - risk-engine : 9103
   - exec-engine : 9104
   - db-writer   : 9105
   Exposer ces ports dans `docker-compose.yml`.

4. **Wrapper de cycle standardisé**. Créer `src/observability/cycle.py` :
   ```python
   from contextlib import contextmanager
   import time

   @contextmanager
   def observed_cycle(engine: str):
       new_cycle()
       start = time.perf_counter()
       status = "ok"
       try:
           yield
       except Exception:
           status = "error"
           raise
       finally:
           duration = time.perf_counter() - start
           cycles_total.labels(engine=engine, status=status).inc()
           cycle_duration.labels(engine=engine).observe(duration)
           last_cycle_ts.labels(engine=engine).set(time.time())
   ```
   Refactor chaque engine pour utiliser `with observed_cycle("vol"): ...` autour du cycle principal.

5. **Log JSON propre vers stdout**. Vérifier que `structlog` est configuré en mode JSON (pas console pretty-print en prod). Format requis :
   ```json
   {"timestamp": "...", "level": "info", "event": "...", "cycle_id": "...", "engine": "vol", ...}
   ```
   En dev WSL2, garder pretty-print conditionnel via `LOG_FORMAT=console` env var.

#### Critères d'acceptance Phase 0

- [ ] `curl http://localhost:9102/metrics` renvoie format Prometheus avec `engine_cycles_total{engine="vol"}` qui s'incrémente.
- [ ] `docker compose logs vol-engine | jq 'select(.cycle_id != null)'` filtre les logs d'un cycle complet.
- [ ] Tous les tests existants (`pytest`) passent.
- [ ] Aucune perf regression mesurable sur cycle vol-engine (mesurer avant/après, tolérance 5%).
- [ ] Documentation : `docs/observability/CONVENTIONS.md` créé avec naming metrics et structure log fields.

#### Anti-patterns à éviter Phase 0

- Ne PAS instrumenter chaque sous-fonction du cycle. Un seul `observed_cycle` par cycle racine suffit.
- Ne PAS exposer de label high-cardinality dans Prometheus (pas `instrument_id`, pas `trade_id`). Si nécessaire, log-only.
- Ne PAS supprimer le `set_heartbeat` actuel — il reste utile pour le frontend React. Coexiste avec metrics.

---

### Phase 1 — Backends LGTM en compose (2 jours, branche `obs/p1-lgtm`)

Objectif : centraliser logs et metrics dans Loki + Prometheus, visualisables via Grafana. Pas encore de traces (Phase 2).

#### Livrables

1. **Profile Docker Compose dédié** `obs`. Ajouter dans `docker-compose.yml` :
   ```yaml
   services:
     prometheus:
       image: prom/prometheus:v2.55.0
       profiles: ["obs"]
       volumes:
         - ./obs/prometheus.yml:/etc/prometheus/prometheus.yml:ro
         - prom-data:/prometheus
       ports: ["9090:9090"]
       command:
         - '--config.file=/etc/prometheus/prometheus.yml'
         - '--storage.tsdb.retention.time=30d'
         - '--storage.tsdb.retention.size=2GB'

     loki:
       image: grafana/loki:3.2.0
       profiles: ["obs"]
       volumes:
         - ./obs/loki.yml:/etc/loki/local-config.yaml:ro
         - loki-data:/loki
       ports: ["3100:3100"]
       command: -config.file=/etc/loki/local-config.yaml

     promtail:
       image: grafana/promtail:3.2.0
       profiles: ["obs"]
       volumes:
         - /var/lib/docker/containers:/var/lib/docker/containers:ro
         - /var/run/docker.sock:/var/run/docker.sock
         - ./obs/promtail.yml:/etc/promtail/config.yml:ro
       command: -config.file=/etc/promtail/config.yml

     grafana:
       image: grafana/grafana-oss:11.3.0
       profiles: ["obs"]
       volumes:
         - grafana-data:/var/lib/grafana
         - ./obs/grafana/provisioning:/etc/grafana/provisioning:ro
         - ./obs/grafana/dashboards:/var/lib/grafana/dashboards:ro
       ports: ["3000:3000"]
       environment:
         GF_AUTH_ANONYMOUS_ENABLED: "true"
         GF_AUTH_ANONYMOUS_ORG_ROLE: "Admin"
         GF_AUTH_DISABLE_LOGIN_FORM: "true"
         GF_SECURITY_ALLOW_EMBEDDING: "true"
         GF_SECURITY_COOKIE_SAMESITE: "none"

   volumes:
     prom-data:
     loki-data:
     grafana-data:
   ```
   Démarré avec `docker compose --profile obs up -d`.

2. **Configuration Prometheus** `obs/prometheus.yml` :
   ```yaml
   global:
     scrape_interval: 15s
     evaluation_interval: 15s

   scrape_configs:
     - job_name: 'engines'
       static_configs:
         - targets:
             - 'market-data:9101'
             - 'vol-engine:9102'
             - 'risk-engine:9103'
             - 'execution-engine:9104'
             - 'db-writer:9105'
           labels:
             environment: 'dev'
   ```

3. **Configuration Loki** `obs/loki.yml` (single-binary, filesystem storage) :
   ```yaml
   auth_enabled: false

   server:
     http_listen_port: 3100

   common:
     instance_addr: 127.0.0.1
     path_prefix: /loki
     storage:
       filesystem:
         chunks_directory: /loki/chunks
         rules_directory: /loki/rules
     replication_factor: 1
     ring:
       kvstore:
         store: inmemory

   schema_config:
     configs:
       - from: 2024-01-01
         store: tsdb
         object_store: filesystem
         schema: v13
         index:
           prefix: index_
           period: 24h

   limits_config:
     retention_period: 336h  # 14 jours
     ingestion_rate_mb: 16
     ingestion_burst_size_mb: 32
     max_global_streams_per_user: 5000
   ```

4. **Configuration Promtail** `obs/promtail.yml` (scrape les logs Docker via socket) :
   ```yaml
   server:
     http_listen_port: 9080

   clients:
     - url: http://loki:3100/loki/api/v1/push

   scrape_configs:
     - job_name: docker
       docker_sd_configs:
         - host: unix:///var/run/docker.sock
           refresh_interval: 5s
       relabel_configs:
         - source_labels: ['__meta_docker_container_name']
           regex: '/(.*)'
           target_label: 'container'
         - source_labels: ['__meta_docker_container_label_com_docker_compose_service']
           target_label: 'engine'
       pipeline_stages:
         - json:
             expressions:
               level: level
               cycle_id: cycle_id
               trace_id: trace_id
               event: event
         - labels:
             level:
   ```
   Note : on extrait `level` comme label (low-cardinality) mais on garde `cycle_id` et `trace_id` dans le contenu du log (high-cardinality).

5. **Provisioning Grafana** auto-charge datasources et dashboards. Créer :
   - `obs/grafana/provisioning/datasources/datasources.yml` :
     ```yaml
     apiVersion: 1
     datasources:
       - name: Prometheus
         type: prometheus
         access: proxy
         url: http://prometheus:9090
         isDefault: true
       - name: Loki
         type: loki
         access: proxy
         url: http://loki:3100
     ```
   - `obs/grafana/provisioning/dashboards/dashboards.yml` :
     ```yaml
     apiVersion: 1
     providers:
       - name: 'fxvol'
         folder: 'fxvol'
         type: file
         options:
           path: /var/lib/grafana/dashboards
     ```

6. **Dashboard "engines overview"** dans `obs/grafana/dashboards/engines-overview.json`. 5 panels :
   - **Panel 1 — Cycle rate par engine** : `sum by (engine) (rate(engine_cycles_total[5m]))`
   - **Panel 2 — Cycle duration p50/p99** : `histogram_quantile(0.99, sum by (engine, le) (rate(engine_cycle_duration_seconds_bucket[5m])))`
   - **Panel 3 — Error rate** : `sum by (engine) (rate(engine_cycles_total{status="error"}[5m]))` — alerte si > 0
   - **Panel 4 — Last cycle age** : `time() - engine_last_cycle_timestamp_seconds` — alerte si > 5×cycle_period
   - **Panel 5 — IB session uptime** : `ib_session_connected` (gauge 0/1)
   - **Panel 6 — Live logs errors** (Loki) : `{level="error"}` last 15min

   Génère le JSON via Grafana UI en exportant après config manuelle, ou écris le directement (format Grafana 11 schema v40+).

7. **Endpoint frontend** : ajouter dans `src/api/routers/dev.py` un endpoint `/api/v1/dev/grafana-url` qui renvoie l'URL embed du dashboard avec time range courant. Modifier `frontend/src/pages/dev/StackCombined.tsx` pour ajouter un lien "Open in Grafana →" par engine et un onglet `/dev/stack/grafana` qui contient `<iframe src={url} />`.

#### Critères d'acceptance Phase 1

- [ ] `docker compose --profile obs up -d` démarre les 4 services sans erreur.
- [ ] `http://localhost:3000` ouvre Grafana sans login (anonyme admin).
- [ ] Dashboard "engines overview" pré-chargé, tous panels rendent.
- [ ] LogQL query `{engine="vol-engine"} | json | cycle_id != ""` renvoie les logs avec cycle_id.
- [ ] PromQL query `rate(engine_cycles_total[5m])` renvoie une valeur non nulle après 1 min.
- [ ] Tuer un engine → alerte "last cycle age > seuil" se déclenche dans Grafana en < 1 min.
- [ ] Frontend `/dev/stack/grafana` charge l'iframe correctement (pas d'erreur CORS, X-Frame-Options OK grâce à `GF_SECURITY_ALLOW_EMBEDDING=true`).
- [ ] Mémoire totale obs < 1 GB (mesurer `docker stats`).
- [ ] Tous tests existants passent.

#### Anti-patterns à éviter Phase 1

- Ne PAS configurer Loki/Prometheus en mode distributed/microservices. Single-binary mode obligatoire à ce scale.
- Ne PAS exposer Grafana sur 0.0.0.0 en EC2 sans auth. En prod EC2, désactiver `GF_AUTH_ANONYMOUS_ENABLED` et configurer un basic auth ou OAuth.
- Ne PAS supprimer le React panel `/dev/stack` — il reste l'entrée M1 (santé 2s). Grafana est pour M2/M3.
- Ne PAS mettre `instrument_id`, `trade_id`, `order_id` en labels Prometheus. High-cardinality kills TSDB.

---

### Phase 2 — Traces distribuées OTel + Tempo (2 jours, branche `obs/p2-traces`)

Objectif : visualiser le flow end-to-end d'un cycle (resolve pain points #2 et #3 du brief).

#### Livrables

1. **Ajout Tempo dans compose** :
   ```yaml
   tempo:
     image: grafana/tempo:2.6.0
     profiles: ["obs"]
     command: -config.file=/etc/tempo.yml
     volumes:
       - ./obs/tempo.yml:/etc/tempo.yml:ro
       - tempo-data:/var/tempo
     ports:
       - "3200:3200"   # tempo
       - "4317:4317"   # otlp grpc
   ```
   Config `obs/tempo.yml` (single-binary monolithic) :
   ```yaml
   server:
     http_listen_port: 3200

   distributor:
     receivers:
       otlp:
         protocols:
           grpc:
             endpoint: 0.0.0.0:4317
           http:
             endpoint: 0.0.0.0:4318

   storage:
     trace:
       backend: local
       local:
         path: /var/tempo/traces
       wal:
         path: /var/tempo/wal

   compactor:
     compaction:
       block_retention: 168h  # 7 jours
   ```

2. **OTel collector** (proxy entre engines et Tempo, permet batching et fan-out futur) :
   ```yaml
   otel-collector:
     image: otel/opentelemetry-collector-contrib:0.110.0
     profiles: ["obs"]
     volumes:
       - ./obs/otel-collector.yml:/etc/otel-collector.yml:ro
     command: ["--config=/etc/otel-collector.yml"]
     ports:
       - "4319:4317"   # OTLP grpc receiver (engines envoient ici)
   ```
   Config `obs/otel-collector.yml` :
   ```yaml
   receivers:
     otlp:
       protocols:
         grpc:
           endpoint: 0.0.0.0:4317

   processors:
     batch:
       timeout: 5s
       send_batch_size: 1000

   exporters:
     otlp/tempo:
       endpoint: tempo:4317
       tls:
         insecure: true

   service:
     pipelines:
       traces:
         receivers: [otlp]
         processors: [batch]
         exporters: [otlp/tempo]
   ```

3. **Datasource Tempo dans Grafana** : ajouter dans `datasources.yml` :
   ```yaml
   - name: Tempo
     type: tempo
     access: proxy
     url: http://tempo:3200
     jsonData:
       tracesToLogs:
         datasourceUid: loki
         tags: ['cycle_id', 'trace_id']
   ```

4. **Instrumentation OTel SDK dans les engines**. Dépendances :
   ```
   opentelemetry-api
   opentelemetry-sdk
   opentelemetry-exporter-otlp-proto-grpc
   opentelemetry-instrumentation-sqlalchemy
   opentelemetry-instrumentation-redis
   ```
   Créer `src/observability/tracing.py` :
   ```python
   from opentelemetry import trace
   from opentelemetry.sdk.trace import TracerProvider
   from opentelemetry.sdk.trace.export import BatchSpanProcessor
   from opentelemetry.sdk.resources import Resource
   from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
   from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
   from opentelemetry.instrumentation.redis import RedisInstrumentor
   import os

   def init_tracing(service_name: str):
       resource = Resource.create({"service.name": service_name})
       provider = TracerProvider(resource=resource)
       endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317")
       exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
       provider.add_span_processor(BatchSpanProcessor(exporter))
       trace.set_tracer_provider(provider)
       SQLAlchemyInstrumentor().instrument()
       RedisInstrumentor().instrument()
       return trace.get_tracer(service_name)
   ```

5. **Patcher `observed_cycle`** pour créer un span racine :
   ```python
   from opentelemetry import trace
   tracer = trace.get_tracer(__name__)

   @contextmanager
   def observed_cycle(engine: str):
       cid = new_cycle()
       with tracer.start_as_current_span(
           f"{engine}_cycle",
           attributes={"engine": engine, "cycle_id": cid}
       ) as span:
           # propager trace_id pour les logs
           ctx = span.get_span_context()
           trace_id_var.set(format(ctx.trace_id, "032x"))
           start = time.perf_counter()
           status = "ok"
           try:
               yield span
           except Exception as e:
               status = "error"
               span.record_exception(e)
               span.set_status(trace.Status(trace.StatusCode.ERROR))
               raise
           finally:
               duration = time.perf_counter() - start
               cycles_total.labels(engine=engine, status=status).inc()
               cycle_duration.labels(engine=engine).observe(duration)
               last_cycle_ts.labels(engine=engine).set(time.time())
   ```

6. **Spans par stage** dans vol-engine. Identifier les ~5 stages clés (`fetch_chain`, `calibrate_garch`, `compute_har_rv`, `fit_svi`, `regime_detect`, `redis_publish`). Wrap chacun avec `tracer.start_as_current_span("stage_name", attributes={...})`. **Granularité importante** : 1 span par stage, PAS 1 span par strike. Ajouter `n_strikes`, `duration_ms`, paramètres clés en attributes du span de stage.

7. **Propagation trace_id via Redis pub/sub**. Modifier `src/bus/publisher.py` pour ajouter `trace_id` dans le payload :
   ```python
   def publish(channel: str, payload: dict):
       payload["_trace_id"] = trace_id_var.get()
       payload["_cycle_id"] = cycle_id_var.get()
       redis.publish(channel, json.dumps(payload))
   ```
   Modifier les subscribers (db-writer principalement) pour extraire `_trace_id` et créer un span enfant lié :
   ```python
   from opentelemetry.trace import Link, SpanContext, TraceFlags

   def consume(payload: dict):
       parent_trace_id = payload.pop("_trace_id", None)
       if parent_trace_id:
           ctx = SpanContext(
               trace_id=int(parent_trace_id, 16),
               span_id=0,  # nouveau span
               is_remote=True,
               trace_flags=TraceFlags(0x01),
           )
           link = Link(ctx)
           with tracer.start_as_current_span("db_write", links=[link]):
               ...
   ```

8. **Vérification compatibilité ib_insync + asyncio**. `ib_insync` patche le loop avec `nest_asyncio`. À tester :
   - Spans OTel dans coroutines `await ib.reqMktDataAsync(...)` se propagent correctement (ContextVar OK).
   - Pas d'auto-instrumentation `opentelemetry-instrumentation-asyncio` (risque de double-wrap).
   - Si problème : wrapper manuel `contextvars.copy_context().run(...)` autour des `loop.run_in_executor`.

   Test minimal à écrire dans `tests/integration/test_otel_ib_compat.py` : créer un cycle minimal qui appelle un mock IB async, vérifier que le span enfant a bien le bon `parent_span_id`.

9. **Dashboard "cycle drill-down"** dans Grafana : panel TraceQL pour query traces récentes, panel logs corrélés (tracesToLogs auto-config grâce au datasource).

#### Critères d'acceptance Phase 2

- [ ] `docker compose --profile obs up -d` inclut tempo et otel-collector sans erreur.
- [ ] Lancer 1 cycle vol-engine → trace visible dans Grafana Explore (Tempo) avec span racine + 5+ spans enfants en flame graph.
- [ ] Ouvrir une trace → cliquer "View logs" → ouvre Loki filtré sur `trace_id` correspondant, montre les logs du cycle.
- [ ] Trace inclut span `db_write` côté db-writer, lié au cycle racine du producteur.
- [ ] TraceQL query `{ resource.service.name = "vol-engine" && duration > 500ms }` renvoie les cycles lents.
- [ ] Test `tests/integration/test_otel_ib_compat.py` passe.
- [ ] Overhead spans < 1% du temps de cycle (mesurer p99 cycle_duration avant/après).
- [ ] Cardinalité Prometheus inchangée (pas de nouveau label injecté par OTel).
- [ ] Tous tests existants passent.

#### Anti-patterns à éviter Phase 2

- **NE PAS** instrumenter par strike. Limite : 1 span par stage, attributes pour les détails. 47 strikes × 5 stages × 1 cycle/180s = 1.3 spans/s, parfait. 47 strikes × 5 stages × 47 = 11k spans/cycle = catastrophe Tempo storage.
- **NE PAS** activer `opentelemetry-instrumentation-asyncio` auto. Conflit potentiel avec `nest_asyncio` de `ib_insync`. Instrumentation manuelle uniquement.
- **NE PAS** propager le contexte OTel via headers HTTP custom. Le mécanisme officiel (W3C Trace Context) suffit pour les futurs appels HTTP, pour Redis pub/sub on injecte manuellement comme spec'd.
- **NE PAS** logger le payload entier d'un span en log structlog. Le span est déjà dans Tempo. Doublon = waste de stockage Loki.

---

## 3. Conventions de naming (à respecter strictement)

### Metrics Prometheus

Format : `<namespace>_<subsystem>_<name>_<unit>`. Exemples :

```
engine_cycles_total                    # Counter
engine_cycle_duration_seconds          # Histogram
engine_last_cycle_timestamp_seconds    # Gauge
ib_session_connected                   # Gauge (0/1)
ib_request_duration_seconds            # Histogram
ib_request_errors_total                # Counter
redis_publish_total                    # Counter
db_write_rows_total                    # Counter
db_write_duration_seconds              # Histogram
```

**Labels autorisés** (low-cardinality, < 50 valeurs cumulées) :
- `engine` ∈ {market-data, vol-engine, risk-engine, execution-engine, db-writer}
- `status` ∈ {ok, error, timeout}
- `symbol` ∈ {EURUSD, ...} (max 10)
- `client_id` ∈ {1, 2, 3, 5}

**Labels interdits** (high-cardinality) :
- `instrument_id`, `contract_id`, `trade_id`, `order_id`, `cycle_id`, `trace_id`, `user_id`

### Spans OTel

Format : `<engine>_<verb>` ou `<engine>_<noun>`. Exemples :
```
vol_cycle                # span racine cycle
vol_fetch_chain
vol_calibrate_garch
vol_fit_svi
risk_cycle
risk_compute_greeks
exec_position_sync
exec_order_submit
db_write
```

**Attributes utiles** : `symbol`, `n_strikes`, `n_rows`, `duration_ms`, paramètres clés du calcul (`alpha`, `beta`).

### Log fields (structlog)

Auto-injectés via processor :
- `cycle_id` (uuid hex)
- `trace_id` (32 char hex, OTel format)
- `engine` (depuis structlog binding initial du service)

À ajouter par log :
- `event` (verbe court : `cycle_start`, `chain_fetched`, `db_inserted`)
- Variables de contexte : `symbol`, `n_strikes`, `duration_ms`, `error_type`

---

## 4. Tests de validation end-to-end

À écrire en Phase 1 et étendre en Phase 2. Fichier : `tests/observability/test_e2e_obs.py`.

```python
def test_cycle_emits_metric():
    """Un cycle vol-engine incrémente engine_cycles_total."""
    before = get_prometheus_metric("engine_cycles_total", {"engine": "vol-engine"})
    trigger_vol_cycle()
    after = get_prometheus_metric("engine_cycles_total", {"engine": "vol-engine"})
    assert after == before + 1

def test_cycle_emits_log_with_cycle_id():
    """Un cycle vol-engine produit des logs avec cycle_id propagé."""
    cid = trigger_vol_cycle()
    logs = query_loki(f'{{engine="vol-engine"}} | json | cycle_id="{cid}"', last="1m")
    assert len(logs) > 0

def test_cycle_emits_trace():  # Phase 2 only
    """Un cycle vol-engine produit une trace Tempo avec spans attendus."""
    cid = trigger_vol_cycle()
    trace = wait_for_trace_by_attribute("cycle_id", cid, timeout=10)
    span_names = {s.name for s in trace.spans}
    assert "vol_cycle" in span_names
    assert "vol_fetch_chain" in span_names
    assert "vol_calibrate_garch" in span_names

def test_db_write_linked_to_producer_cycle():  # Phase 2 only
    """Un span db_write est lié à la trace du cycle producteur."""
    cid = trigger_vol_cycle()
    trace = wait_for_trace_by_attribute("cycle_id", cid, timeout=10)
    db_spans = [s for s in trace.spans if s.name == "db_write"]
    assert len(db_spans) > 0
    assert db_spans[0].parent_span_id is not None
```

---

## 5. Plan de rollback

Chaque phase doit être reversible sans perte de fonctionnalité existante.

| Phase | Rollback |
|---|---|
| P0 | Retirer `observed_cycle` wrappers, retirer `prometheus-client` dep, retirer endpoint `/metrics`. Logs structlog déjà présents, juste retirer le processor `add_context_ids`. |
| P1 | `docker compose --profile obs down -v`. Supprimer dossier `obs/`. Frontend `/dev/stack/grafana` → 404 mais `/dev/stack` reste opérationnel. |
| P2 | Retirer `init_tracing()` calls, retirer dep `opentelemetry-*`. `observed_cycle` reverts à version Phase 0 (metrics + logs only). |

---

## 6. Documentation à produire/mettre à jour

À chaque phase, mettre à jour :

- `docs/observability/CONVENTIONS.md` (nouveau) : naming metrics, spans, log fields.
- `docs/observability/RUNBOOKS.md` (nouveau) : "comment debug X avec la stack obs". Au moins 3 runbooks à la fin de P2 :
  1. "Le cycle vol-engine n'avance plus" → query LogQL/Tempo type
  2. "Les writes Postgres sont lents" → query Prometheus + drill Tempo
  3. "IB Gateway s'est déconnecté" → query gauge + logs reconnect
- `CLAUDE.md` : ajouter section "Observability" avec liens vers les conventions, dashboards URL, et règles d'instrumentation pour code futur.
- `README.md` : ajouter section "Observability stack" avec quickstart `docker compose --profile obs up -d` et URL Grafana.

---

## 7. Critères globaux de complétion (avant tag v1.0 final)

- [ ] Phase 0, 1, 2 toutes mergées.
- [ ] Tous tests passent (`pytest`, smoke notebooks).
- [ ] Aucune régression de perf > 5% sur cycle vol-engine.
- [ ] Mémoire stack obs en idle < 1 GB.
- [ ] 3 runbooks documentés.
- [ ] Dashboard Grafana "engines overview" + "cycle drill-down" provisionnés.
- [ ] Frontend React `/dev/stack` modifié avec liens Grafana, `/dev/stack/grafana` iframe fonctionnel.
- [ ] EC2 deploy testé (au moins le profile obs démarre proprement sur EC2 avec mêmes configs, modulo binding ports).
- [ ] `CLAUDE.md` mis à jour avec section Observability.

---

## 8. Out of scope (à ne PAS implémenter)

- Sampling de traces (head ou tail-based). Volume actuel ne le justifie pas.
- Alertmanager + intégration Slack/PagerDuty. Alertes Grafana suffisent à ce stade.
- Mimir / Cortex (Prometheus distributed). Single Prom suffit.
- Pyroscope (continuous profiling). À reconsidérer en step quant researcher.
- Exemplars Prometheus → traces. Feature avancée, à ajouter en Phase 3 future.
- Authentification Grafana en dev. À ajouter en prod EC2 only.
- Storage objet S3 pour Loki/Tempo. Filesystem local suffit < 50 GB. À migrer si retention augmentée.
- eBPF-based observability (Pixie, Parca). Hors scope solo dev.

---

## 9. Estimation effort

| Phase | Estimation | Variable critique |
|---|---|---|
| P0 | 1 j | Refactor des entry points cycle (5 engines) |
| P1 | 2 j | Config files (Loki, Promtail, Grafana provisioning), 1 dashboard JSON |
| P2 | 2 j | Compat OTel + ib_insync (point d'incertitude principal), propagation trace_id Redis |
| **Total** | **5 j** | Tester P2 OTel sur 1 engine (vol) avant rollout aux 4 autres |

Si compat OTel + ib_insync casse, fallback : garder logs structurés (P1) sans OTel SDK, propager `cycle_id` manuellement dans payloads Redis. Perte = pas de flame graph, mais 80% du value (corrélation logs cross-engine via cycle_id).
