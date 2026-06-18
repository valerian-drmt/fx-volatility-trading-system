# FX Vol Trading — Live UI · Design Brief

> Brief autonome pour un outil de design (Claude Design / Figma). Tout le contexte
> nécessaire est ici : contexte projet, design language, **catalogue exhaustif des
> données et actions disponibles**, et une **architecture de navigation proposée**
> (style Binance) avec la composition détaillée de chaque page.
>
> Périmètre : **front-end / esthétique / ergonomie uniquement**. Aucun backend à
> concevoir — toutes les données listées ici existent déjà côté API.

---

## 1. Contexte projet (à comprendre avant de designer)

C'est l'interface d'un **système de trading d'options FX sur la volatilité** (EUR/USD),
construit comme un vrai stack de production. Objectif de cette UI : être **montrée à des
recruteurs** — elle doit respirer le sérieux d'un desk pro (Bloomberg / desk quant /
Binance Pro), pas le projet étudiant.

Ce que l'UI doit faire transparaître (les « flex » à valoriser visuellement) :

- **Infra cloud réelle** : 10 conteneurs Docker, AWS (SSM/KMS pour secrets), Postgres,
  Redis, Nginx, 5 engines Python.
- **Broker réel** : Interactive Brokers (IBKR) via IB Gateway — comptes PAPER et LIVE.
- **Quant sérieux** : PCA sur la surface de vol, détection de régime (GMM), calibration
  SVI/SSVI du smile, forecasts GARCH / HAR-RV, Vol Risk Premium (VRP), Yang-Zhang RV.
- **Gestion du risque complète** : greeks agrégés (Δ/Γ/V/Θ + Vanna/Volga), stress grids
  spot×vol, greeks ladder, pin risk, scénarios, VaR, P&L attribution (décomposition de Taylor).
- **Exécution live** : construction de structures multi-legs, preview avec pricing,
  booking atomique, close atomique.

**Public** : un trader/quant qui pilote le desk + un recruteur qui regarde par-dessus
l'épaule. Densité d'information élevée assumée, mais lisible et hiérarchisée.

---

## 2. État actuel du front (point de départ)

- Le **front « Live » est KO** (cassé / placeholder) — c'est lui qu'on redessine.
- Tout le travail réel et fonctionnel vit aujourd'hui dans une **console « Dev »**
  (route `/dev`, 7 onglets). C'est notre **référence fonctionnelle** : tous les composants,
  toutes les données et toutes les actions décrites plus bas y sont déjà branchés et marchent.
- Stack front : **React 18 + Vite + TypeScript strict + Zustand + Plotly.js**.
- Pas encore de vrai routeur (un switch maison sur `window.location`). Le redesign peut
  introduire une vraie navigation.

> En clair : le design doit **reprendre la substance des onglets Dev** (qui sont riches mais
> bruts/« outil interne ») et la **réorganiser en une UI Live pro et ergonomique** style Binance.

---

## 3. Design language (à respecter / raffiner)

**Thème : dark, dense, tabular.** Tokens CSS existants (point de départ, à faire évoluer) :

| Token | Valeur | Usage |
|---|---|---|
| `--bg` | `#0f1115` | fond global (presque noir, bleuté) |
| `--surface` | `#181b22` | cartes / panels |
| `--border` | `#262a33` | bordures fines |
| `--fg` | `#e6e8ee` | texte principal |
| `--muted` | `#8a90a0` | labels, texte secondaire |
| `--accent` | `#4f9dff` | bleu d'action / liens / traces |
| `--pos` | `#3fb950` | vert (P&L positif, BUY, OK) |
| `--neg` | `#f85149` | rouge (P&L négatif, SELL, DOWN) |
| warn | `#e0b341` | ambre (stale, warning, pré-event) |

**Conventions** :
- Police système, base `13px`, chiffres en `font-variant-numeric: tabular-nums` (alignement
  des colonnes de nombres — essentiel pour un desk).
- Sémantique couleur stricte : **vert = positif/buy/sain**, **rouge = négatif/sell/down**,
  **ambre = attention/stale/pré-event**. Ne jamais inverser.
- Cartes : `border-radius` ~6px, header de panel discret (titre 13px + compteur muted).
- Nombres compacts : suffixes K/M/B au-delà de 1000 ; signes explicites (`+1.23k`, `-0.4M`).

**Inspiration ergonomie : Binance (Pro / Futures).**
- **Barre supérieure horizontale** fine : logo, sélecteur de paire, ticker prix live,
  badges d'état (marché ouvert/fermé, PAPER/LIVE), stats compte rapides, santé connexion.
- **Rail vertical de navigation à gauche** avec icônes + labels (onglets de sections).
- Zone centrale = graphe + données ; colonne(s) latérale(s) = ordre / positions.
- Tables denses, sticky headers, lignes colorées par signe, hover discret.

---

## 4. Catalogue exhaustif des DONNÉES disponibles

> Tout ce qui suit existe **déjà** (REST + WebSocket). C'est la matière première du design :
> n'importe quel écran peut piocher ici. Base URL REST : `/api/v1/...`.

### 4.1 Temps réel (WebSocket)

| Canal | Fréquence | Contenu | Pour quoi |
|---|---|---|---|
| `/ws/ticks` | ~5/s | `{symbol, bid, ask, mid, ts}` | prix live, ticker, mini-chart |
| `/ws/vol` | ~1/3min | surface de vol (fin de cycle engine) | refresh surface/smile |
| `/ws/risk` | ~1/2s | `{delta, gamma, vega, theta, ts}` | greeks portefeuille live |
| `/ws/positions` | ~1/cycle | snapshot MTM des positions | P&L live des positions |
| `/ws/orders/{id}` | événementiel | statut ordre / fill / reject / cancel | suivi exécution structure |
| `/ws/exit_alerts` | événementiel | déclenchement règle de sortie | alertes de sortie |
| `/ws/system_alerts` | événementiel | `{severity, message, ts}` | bandeau d'alertes système |
| `/ws/account` | ~10s | snapshot compte (net liq, cash, funds) | stats compte temps réel |

### 4.2 Marché & Volatilité (REST)

| Endpoint | Donne |
|---|---|
| `GET /vol/surface?symbol` | surface complète **6 tenors × 5 deltas** → IV (grille 3D) |
| `GET /vol/surface/at/{ts}` | surface historique à un instant T |
| `GET /vol/term-structure` | par tenor : `sigma_atm_pct`, `sigma_fair_pct` (GARCH), `rv_pct` (Yang-Zhang) |
| `GET /vol/smile/{tenor}` | 5 points (10P/25P/ATM/25C/10C) : strike, IV, delta_label, **courbe SVI**, σ_fair, RV |
| `GET /vol-history?limit` | N derniers snapshots de surface (spot, forward) |

Tenors : **1M, 2M, 3M, 4M, 5M, 6M**. Piliers delta : **10Δp, 25Δp, ATM, 25Δc, 10Δc**.

### 4.3 Signaux — PCA, Régime, Events (REST)

| Endpoint | Donne |
|---|---|
| `GET /signals/pca/state` | par PC (PC1/PC2/PC3) : `z_score`, `label` (CHEAP/FAIR/EXPENSIVE), `actionable`, `recommended_structure`, **variance expliquée** (pc1/2/3/cumul), `coherence` (contradictions entre PC), loadings stability |
| `GET /signals/pca/history?n` | N derniers signaux PCA |
| `GET /signals/pca/model` | métadonnées du modèle (n_obs, fenêtre de fit, stabilité loadings) |
| `GET /vol/regime` (ou `/regime/state`) | régime (`calm`/`stressed`/`pre_event`), probabilités, features (`vol_level`, `vol_of_vol`, `term_slope`), **VRP par tenor**, `event_dampener`, gate (trading autorisé ? `reason`, `size_mult`) |
| `GET /regime/features` | features régime détaillées : valeur, **z-score** (fenêtre 90j), bucket (`--/-/0/+/++`), Δz/1h, signal level (`noise/weak/strong/tail`), contexte μ±σ, synthèse multivariée (joint_pattern, dominant, vs_expected) |
| `GET /regime/history?n` | N snapshots régime |
| `GET /regime/transitions?n` | log des changements de régime |
| `GET /regime/events` | events économiques à venir (date, pays, impact, type, description) — FRED/ECB/BoE/FOMC/Eurostat/ONS |
| `GET /regime/gmm/shadow` | probas postérieures GMM (classification régime) |

### 4.4 Pricing & Greeks à la demande (REST)

| Endpoint | Donne |
|---|---|
| `POST /price` | prix Black-Scholes d'une option |
| `POST /greeks` | `price, delta, gamma, vega, theta` |
| `POST /iv` | IV implicite inversée depuis un prix marché |
| `POST /vol/trade-preview` | preview d'une **structure** (StraddleATM / RiskReversal25d / Butterfly25d / CalendarSpread) → legs détaillées (instrument, side, qty, strike, tenor, IV, prime/contrat) + **net Δ/Γ/V/Θ** + prime totale |

### 4.5 Positions & Portefeuille (REST)

| Endpoint | Donne |
|---|---|
| `GET /positions/open` | **table riche des positions ouvertes** (voir colonnes §4.7) |
| `GET /positions?status&limit` | toutes positions (OPEN/CLOSED/EXPIRED) |
| `GET /positions/{id}` | détail d'une position |
| `GET /positions/{id}/mtm-history` | historique MTM d'une position (pour plot) |
| `GET /positions/{id}/alerts` | alertes de sortie d'une position |
| `GET /positions/{id}/hedges` | delta hedges d'une position |
| `GET /risk` | greeks agrégés portefeuille (temps réel via engine) |
| `GET /pnl-curve` | courbe P&L vs spot (~31 points) |
| `GET /portfolio/header` | résumé book : P&L, greeks nets, VaR |
| `GET /portfolio/account` | compte : net liq, cash, unrealized P&L, marge init/maint, excess liquidity, cushion, # positions (+ delta 24h) |
| `GET /portfolio/equity-curve?window` | courbe Net Liq dans le temps (1d/7d/30d/1y/all, points EOD marqués) |
| `GET /portfolio/vega-per-tenor` | vega par bucket de tenor (DTE) + % + # positions |
| `GET /portfolio/stress-grid` | **matrice P&L spot×vol** (ΔIV −5..+5vp × ΔSpot −200..+200bp) |
| `GET /portfolio/greeks-ladder` | échelle par niveau de spot (±200bp) : P&L, Δ, Γ, Vega, hedge Δ |
| `GET /portfolio/pin-risk` | par option : DTE, distance au strike (pips), P&L now / if pin / breach ±50bp |
| `GET /portfolio/scenarios` | courbes P&L / Δ / Γ / Vega / Θ vs choc (spot ou vol) |
| `GET /portfolio/pnl-attribution?lookback_hours` | décomposition de Taylor par position : contrib Δ/Γ/Vega/Θ + résidu vs P&L réel |

### 4.6 Système / Admin / Diagnostics (REST)

| Endpoint | Donne |
|---|---|
| `GET /health` · `/health/extended` | liveness + readiness (redis/db/engines) |
| `GET /system-stats` | row counts Postgres + heartbeat ages des engines |
| `GET /backtest` | runs de backtest : Sharpe, max drawdown, total return, # trades |
| `GET /admin/config` (+ history/schema/revert) | **config de trading versionnée** (signal, regime, sizing, exit_rules, surface, calibration, delta_hedge, structures) — éditable, append-only, hot-reload |
| `GET /vol/model-health` | # surfaces, # calibrations SVI, dernière MAJ, PCA prêt ? |
| `GET /dev/stack` · `/dev/engines` · `/dev/redis/*` · `/dev/db-schema` · `/dev/tables/*` · `/dev/cycle-progress` | diagnostics infra (santé conteneurs, Redis, schéma DB, tables, progression du cycle engine) |

### 4.7 Colonnes de la table « Positions ouvertes » (`OpenPositionsTable`, composant partagé)

Identité : ID · Package ID · Trade ID · Contract ID (IB conId) · Product label · Structure
(label IB) · Side (BUY/SELL coloré). Spéc : Quantity · Tenor · Expiry. P&L/prix : **P&L USD
(coloré)** · Market price · Entry price · Nominal (EUR). Greeks : Δ (USD) · Γ (USD/pip) ·
Vega (USD/vp) · Θ (USD/day) · IV % · **Vanna** · **Volga**. Méta : Last update · Opened at.
Tri par package → trade → id (les legs d'une même structure restent groupées).

---

## 5. Catalogue exhaustif des ACTIONS possibles

**Trading**
- Construire une structure : choisir **produit** (Future / Butterfly / Straddle / Strangle /
  Calendar / Vanilla call / Vanilla put), **side** (BUY/SELL), **tenor** (1M–6M), **far tenor**
  (calendar), **strike** (saisie libre **ou boutons piliers delta** 10Δp/25Δp/ATM/25Δc/10Δc qui
  auto-remplissent depuis la surface live), **taille** (contrats), **contract size** future (6E €125k / M6E €12.5k).
- **Preview** : pricing complet → coût/contrat, commission, coût total, greeks par leg + **NET** (Δ/Γ/V/Θ).
- **Book** : ouvre un panneau d'ordre (snapshot figé, badge PAPER/LIVE, raisons de blocage éventuelles) → **Send** / Cancel.
- **Close** : fermer un contrat (qty partielle, cappée) **ou** fermer une trade entière (toutes les legs, atomique), avec résumé risque pré/post close.

**Configuration**
- Éditer la config de trading versionnée (seuil de signal, modèle de forecast HAR/GARCH/EWMA, etc.), commenter, **revert** vers une version antérieure.

**Navigation / monitoring** (lecture)
- Sélection globale **symbol / tenor / strike** (partagée entre panneaux).
- Sélecteurs de fenêtre temporelle (equity curve, P&L attribution, scénarios).
- Sélecteur d'horizon pour le calendrier d'events.
- Rotation/zoom des graphes 3D ; pan/zoom de l'ER diagram.

**État disponible côté store (Zustand)** : `selectionStore` (symbol/tenor/strike),
`orderDraftStore` (brouillon d'ordre), `connectionStore` (état WS global).

---

## 6. Architecture de navigation proposée (recommandation)

Ta base (Dashboard / Trade / PCA Indicator / Risk Matrix + positions) est bonne. Vu la
richesse des données, je propose de l'**étendre légèrement** pour ne pas entasser tout le
quant dans un seul onglet, tout en gardant l'ergonomie Binance.

### 6.1 Coque (layout global)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ TOP BAR  logo · ◤EURUSD▾ · 1.0842 ▲ (bid/ask) · ●Marché ouvert · PAPER ·   │  ← horizontale, fine
│          NetLiq $X (+0.4%) · P&L jour +$1.2k · ●WS sain · ⚙               │
├────┬─────────────────────────────────────────────────────────────────────┤
│ N  │                                                                       │
│ A  │                                                                       │
│ V  │                     ZONE DE CONTENU (par section)                     │  ← rail gauche
│    │                                                                       │     icônes+labels
│ R  │                                                                       │
│ A  │                                                                       │
│ I  │                                                                       │
│ L  │                                                                       │
└────┴─────────────────────────────────────────────────────────────────────┘
```

**Top bar (toujours visible)** : logo · sélecteur de paire (EURUSD pour l'instant) · **ticker
prix live** (mid + bid/ask + flèche colorée) · badge marché ouvert/fermé · badge compte
**PAPER/LIVE** · **Net Liq + P&L du jour** (raccourci) · indicateur santé WS (point coloré) · ⚙ settings.

**Rail gauche (sections)** — icône + label, item actif surligné accent :

1. **Dashboard** — vue d'ensemble / command center
2. **Trade** — exécution (le cœur)
3. **Volatility** — surface / smile / term structure
4. **Signals** — PCA + régime + events
5. **Risk** — matrice de risque, stress, scénarios
6. **Portfolio** — compte, equity, positions détaillées, P&L attribution
7. *(optionnel, flex recruteur)* **System** — santé du stack 10 conteneurs + schéma DB
8. **Settings** (bas du rail, séparé) — config versionnée

> Variante minimale si tu veux coller à ta proposition initiale (4 onglets) : fusionne
> **Volatility+Signals → « PCA / Vol »** et **Risk+Portfolio → « Risk & Positions »**.
> Je recommande quand même de **séparer Trade, Risk et Portfolio** : ce sont 3 modes mentaux
> différents et ça évite la page fourre-tout.

### 6.2 Composition page par page

#### ① Dashboard (command center)
But : tout voir en 5 secondes. Grille de tuiles.
- **Bandeau compte** : Net Liq · Cash · Unrealized P&L · P&L jour · marge init/maint % · excess liquidity (chacune avec delta 24h coloré). → `/portfolio/account`, `/portfolio/header`.
- **Mini equity curve** (sparkline 7d). → `/portfolio/equity-curve`.
- **Greeks nets du book** (Δ/Γ/Vega/Θ + VaR) en tuiles. → `/risk`, `/portfolio/header`.
- **Carte régime** : badge couleur (calm/stressed/pre_event) + 3 features. → `/vol/regime`.
- **Top signal PCA** : la PC la plus actionnable (z-score, label CHEAP/EXPENSIVE, structure reco). → `/signals/pca/state`.
- **Prochain event** à fort impact + countdown. → `/regime/events`.
- **Strip santé engines** : 4 engines + IB Gateway, points colorés. → `/system-stats` / `/dev/engines`.
- **Positions ouvertes (résumé)** : count + P&L total, lien vers Portfolio.
- **Bandeau d'alertes système** (toasts/ligne) via `/ws/system_alerts`.

#### ② Trade (le cœur — layout Binance Futures)
3 zones : centre = graphe + marché, droite = ordre, bas = positions + cash.
- **Centre haut — Chart prix** : **candlestick OHLC EURUSD** (cf. §8 gap) avec overlay spot live. À défaut, le tick line-chart actuel (`useTicks`). Sélecteur de timeframe.
- **Centre milieu — Market data block** : spot, **âge de la surface**, IV à chaque pilier/tenor (read-only, auto-fetch). → `/vol/surface`, `/vol/term-structure`.
- **Droite — Order builder** (3 blocs colorés repris du Dev) :
  - *Inputs (vert)* : produit · side · tenor (+ far) · strike (+ boutons piliers delta) · taille · contract size.
  - *Market (jaune)* : spot, surface age, IV par pilier.
  - *Outputs (rouge, après Preview)* : coût/contrat, commission, coût total, **table greeks par leg + ligne NET**.
  - Boutons **Preview** → **Book** (panneau ordre : KV récap, badge PAPER/LIVE, blocages, **Send**/Cancel) → bandeau résultat.
- **Bas — Open Positions** (table riche partagée, §4.7) avec **P&L live**. → `/positions/open` + `/ws/positions`.
- **Bas droite — Cash / Currency holdings** (style IBKR) : balances par devise. → `/portfolio/account` (currency summary).
- **Panneau Close** (à côté) : close contrat (qty) ou close trade entière, avec risque pré/post.

#### ③ Volatility (le showcase quant)
- **Surface 3D** (Plotly, tenor×delta×IV, rotation/zoom, colorscale froid→chaud). → `/vol/surface`. Composant `Plot3DSurface` existant.
- **Smile par tenor** : courbe + **fit SVI** + σ_fair (GARCH) + RV (Yang-Zhang) + table (strike, IV, **skew vs ATM** coloré). Sélecteur de tenor. → `/vol/smile/{tenor}`.
- **Term structure** : σ mid / σ fair / RV across tenors. → `/vol/term-structure`.
- **Model health** : # surfaces, # calibrations SVI, dernière MAJ, PCA prêt ?. → `/vol/model-health`.

#### ④ Signals (PCA + régime + events) — « PCA Indicator » enrichi
- **Cycle timer** : countdown 180s + arbre de progression du cycle engine (Vol Surface → Regime → PCA → Publish). → `/dev/cycle-progress`.
- **3 cartes PC (PC1/PC2/PC3)** : z-score (grand chiffre ±), percentile + courbe gaussienne avec marqueur, label CHEAP/FAIR/EXPENSIVE, **heatmap loadings 6×5** (tenor×delta, vert/rouge), structure recommandée, sous-signaux skew_z/convex_z. → `/signals/pca/state`.
- **Variance expliquée** PC1/2/3/cumul + stabilité (warning si cumul < 85%).
- **Coherence badge** : contradictions entre PC.
- **Features live** : table z-scores (value, z, bucket, Δz/1h, signal level, μ±σ) + synthèse multivariée. → `/regime/features`.
- **Régime** : badge + probabilités + VRP par tenor. → `/vol/regime`.
- **Calendrier d'events** : table (date, pays, impact coloré, countdown, type) + sélecteur d'horizon (1w–3m). → `/regime/events`.

#### ⑤ Risk (matrice de risque)
- **Matrice de stress spot×vol** (heatmap P&L, ΔIV × ΔSpot, cellule centrale = courant). → `/portfolio/stress-grid`.
- **Greeks ladder** : par niveau de spot (±200bp) → P&L, Δ, Γ, Vega, hedge Δ. → `/portfolio/greeks-ladder`.
- **Vega par tenor** : buckets DTE + % + # positions. → `/portfolio/vega-per-tenor`.
- **Pin risk** : par option, DTE (ambre si ≤7j), distance pips, P&L now/if pin/breach. → `/portfolio/pin-risk`.
- **Scénarios** : mini-charts P&L/Δ/Γ/Vega/Θ vs choc (toggle spot/vol). → `/portfolio/scenarios`.
- **Risk utilization** : marge init/maint %, exposition Δ/Γ/Vega/Θ en % du Net Liq, buffers — colorés vert/ambre/rouge par seuil.
- **VaR 1d 99%** + greeks nets.

#### ⑥ Portfolio (compte & historique)
- **Account summary** complet (toutes les lignes §4.5 avec deltas 24h). → `/portfolio/account`.
- **Equity curve** plein écran + sélecteur fenêtre (1d/7d/30d/1y/all). → `/portfolio/equity-curve`.
- **Currency summary** (par devise). 
- **Positions détaillées** (table riche §4.7) + historique MTM au clic. → `/positions/open`, `/positions/{id}/mtm-history`.
- **P&L attribution** : décomposition de Taylor par position (Δ/Γ/Vega/Θ + résidu vs réel) + sélecteur lookback (1h/6h/1d/7d). → `/portfolio/pnl-attribution`.

#### ⑦ System (optionnel — flex infra pour recruteur)
- **Diagramme du stack** : 10 conteneurs en couches (DATA/ENGINES/APP/EDGE/OBS), statuts colorés, flèches de dépendances. → `/dev/stack`.
- **Engine health cards** (heartbeat, stale threshold, IB Gateway). → `/dev/engines`.
- **ER diagram** de la base (groupes par domaine, pan/zoom). → `/dev/db-schema`.
- *(la console Dev brute reste accessible séparément ; ici c'est une version « vitrine » polie.)*

#### ⑧ Settings
- Éditeur de config versionnée (seuil signal, modèle forecast, …) + historique + revert. → `/admin/config*`.

---

## 7. Composants existants à réutiliser (mapping)

| Existant | Réutiliser pour |
|---|---|
| `OpenPositionsTable` | Trade (bas), Portfolio, Dashboard (résumé) |
| `Plot3DSurface` | Volatility (surface 3D), Signals |
| `SmileChart` / `SmileChartPanel` | Volatility (smile) |
| `TermStructureChart` / `TermStructurePanel` | Volatility (term structure) |
| `TickChart` / `ChartPanel` | Trade (chart prix, fallback avant candlestick) |
| `RegimeDetectorPanel` | Signals, Dashboard (carte régime) |
| `PCASignalPanel` / `Step2Pca` (heatmaps, gaussiennes) | Signals |
| `FeaturesLivePanel` | Signals |
| `ModelHealthPanel` | Volatility |
| `PlotlyChart` (wrapper dark) | toutes les courbes (equity, scénarios) |
| `MetricTile` | toutes les tuiles (Dashboard, account) |
| `DataTable` | toutes les tables génériques |
| `ConnectionIndicator` / `StatusBadge` | top bar |
| Stress grid / greeks ladder / vega-per-tenor / pin-risk / scenarios / P&L attribution (depuis `Portfolio.tsx` Dev) | Risk, Portfolio |

> La console Dev contient déjà des versions fonctionnelles de **quasiment tous** ces blocs.
> Le travail de design = **réhabiller + réorganiser**, pas réinventer la donnée.

---

## 8. Gaps / éléments à ajouter (honnête)

- **Chart candlestick OHLC** : aujourd'hui seul un **tick line-chart** existe (`/ws/ticks`).
  Pour le rendu « Binance » (bougies + volume + timeframes), il faudra un endpoint OHLC
  (l'engine market-data produit déjà des bars en interne). À designer : zone chart avec
  sélecteur de timeframe, crosshair, éventuellement overlays (VWAP, niveaux de strike actifs).
  Recommandation lib : `lightweight-charts` (TradingView) ou candlestick Plotly.
- **Sélecteur de paire** : le système est mono-paire (EURUSD) pour l'instant — prévoir le
  composant mais il peut n'avoir qu'une entrée au début.
- **Overlay des strikes/positions sur le chart** (nice-to-have) : marquer les strikes des
  options ouvertes sur le graphe prix.
- **Densité responsive** : prévoir un breakpoint « écran large desk » (3 colonnes) et un
  fallback laptop (colonnes empilables).

---

## 9. Priorités de design (ordre suggéré)

1. **Coque** (top bar + rail gauche + thème) — pose l'identité pro.
2. **Trade** — la page qui « fait trader », la plus impressionnante en démo.
3. **Dashboard** — la vue d'accueil qui résume tout.
4. **Risk** + **Portfolio** — le sérieux gestion du risque.
5. **Volatility** + **Signals** — le showcase quant (PCA, surface, SVI).
6. **System** + **Settings** — le flex infra + l'admin.

---

### TL;DR pour le designer
Dark, dense, tabular, sémantique vert/rouge/ambre stricte. Top bar horizontale + rail gauche
façon Binance. 6 sections cœur (Dashboard, Trade, Volatility, Signals, Risk, Portfolio) +
2 annexes (System, Settings). **Toute la donnée existe déjà** (§4) — surface de vol 3D, PCA
z-scores + loadings, régime, stress grids, greeks live, positions multi-legs, order builder
avec preview/book/close. Seul vrai manque visuel : un **chart candlestick** à câbler. Objectif :
que ça ressemble à un desk pro qu'on montre fièrement à un recruteur.
