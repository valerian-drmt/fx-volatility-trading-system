# Guide utilisateur — Trading vol EUR/USD FOP

Manuel opérationnel du cockpit de trading vol. Explique ce que tu vois
sur chaque panel du frontend, comment lire les signaux, et comment
passer de l'info à l'exécution.

---

## Vue d'ensemble — flux utilisateur

Le cockpit est structuré en 6 panels hiérarchiques. L'ordre n'est pas
décoratif : chaque étape filtre la précédente.

```
┌─────────────────────────────────────────────────────────┐
│ PANEL 1 — Regime Detector         (contexte macro)       │
│ "Est-ce un bon moment pour trader la vol ?"              │
└──────────────────────┬──────────────────────────────────┘
                       │  si régime = calme/stressé → OK
                       │  si régime = pré-événement → STOP
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PANEL 2 — PCA Signal Dashboard    (détection edge)       │
│ "Quels facteurs sont mispriced aujourd'hui ?"            │
└──────────────────────┬──────────────────────────────────┘
                       │  signal |z| > 1.5 → arm trade
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PANEL 3 — Trade Preview           (décision structure)   │
│ "Quelle structure, quelle taille, quels risques ?"       │
└──────────────────────┬──────────────────────────────────┘
                       │  all checks pass → submit
                       ▼
┌─────────────────────────────────────────────────────────┐
│ PANEL 4 — Active Positions        (suivi)                │
│ "Où en sont mes positions, quand sortir ?"               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ PANEL 5 — Surface Diagnostic      (visualisation)        │
│ PANEL 6 — Model Health            (audit, debug)         │
│ Consultation à la demande, pas dans le flux principal    │
└─────────────────────────────────────────────────────────┘
```

---

## Panel 1 — Regime Detector

### Ce que tu vois

```
╔══════════════════════════════════════════════════════════╗
║  RÉGIME COURANT : CALME                 [teal badge]     ║
║                                                          ║
║  Probabilités GMM  ░░░░░░░░░░░░ 78% CALME                ║
║                    ░░░░░ 18% STRESSÉ                     ║
║                    ░ 4% PRÉ-ÉVÉNEMENT                    ║
║                                                          ║
║  Features live                                           ║
║  vol_of_vol   =  0.12    z = -0.3   (faible)             ║
║  vol_level    =  6.05%   z = -0.5   (bas)                ║
║  term_slope   =  +0.18   z = +0.2   (plat)               ║
║                                                          ║
║  Prochain événement : ECB meeting dans 11j 3h            ║
║  VRP attendu (calme) : 1M=+0.4% | 3M=+0.6% | 6M=+0.9%    ║
║                                                          ║
║  event_dampener : OFF                                    ║
╚══════════════════════════════════════════════════════════╝
```

### Comment lire

**Régime courant** détermine si tu as le droit de trader :

| Régime | Action | Raison |
|---|---|---|
| CALME | Trading normal | Distribution IV stable, VRP capturable |
| STRESSÉ | Trading possible, sizing ×0.7 | Vol de vol élevée, bruit d'estimation +++ |
| PRÉ-ÉVÉNEMENT | **NO TRADE** | Surface contaminée par jump premium, modèle cassé |

**event_dampener OFF** = pas d'événement majeur dans les 5 prochains
jours. Si ON, sizing auto-divisé par 2 sur tous les signaux.

**VRP attendu** = la prime de risque variance pour le régime courant.
Si le marché affiche IV − RV inférieur à ce VRP, la vol est *vraiment*
cheap (pas juste artificiellement cheap à cause du VRP structurel).

### Règle de décision

```
IF régime == "PRÉ-ÉVÉNEMENT":
    → STOP. Pas de nouveau trade.
    → Sortir les positions existantes si possible.

IF régime == "STRESSÉ":
    → Trades possibles mais sizing × 0.7
    → Vérifier deux fois les signaux (bruit élevé)

IF régime == "CALME":
    → Procéder au Panel 2
```

---

## Panel 2 — PCA Signal Dashboard

C'est le panel central. Il répond à : **quelles structures de trade
sont justifiées maintenant ?**

### Ce que tu vois

Trois colonnes, une par facteur PCA orthogonal :

```
┌─────────────────┬─────────────────┬─────────────────┐
│  PC1 (Level)    │  PC2 (Slope)    │  PC3 (Smile)    │
├─────────────────┼─────────────────┼─────────────────┤
│ ▓▓▓▓▓▓▒▒▒▒▒▒▒▒  │ ▒▒▒▒▒░░░░░░░░  │ ▓▓▓▓▓▓▓▓▒▒▒▒▒▒ │
│ z = +1.8        │ z = -0.4        │ z = +2.3        │
│ CHEAP ●         │ FAIR            │ CHEAP ●         │
│                 │                 │                 │
│ Last 3 months   │ Last 3 months   │ Last 3 months   │
│ ∿∿∿╱╲╱╲╱╲∿∿    │ ∿∿∿∿∿∿∿∿       │ ╱╲╱╲╱╲╱╱╱╲    │
│                 │                 │                 │
│ Sub-signals :   │ Sub-signals :   │ skew  z = +0.8  │
│ —               │ —               │ convex z = +2.5 │
│                 │                 │                 │
│ Rec. structure: │ Rec. structure: │ Rec. structure: │
│ Straddle ATM 3M │ (no trade)      │ Long BF25 3M    │
│                 │                 │                 │
│ [ Arm trade ]   │                 │ [ Arm trade ]   │
└─────────────────┴─────────────────┴─────────────────┘
```

### Les trois facteurs — schéma conceptuel

Chaque PC est une **direction de mouvement** de la surface IV,
orthogonale aux autres. Quand ton snapshot est "loin" d'une direction
vs sa moyenne historique, c'est un signal de mispricing sur CE facteur.

```
                  PC1 : LEVEL
            IV
            │    ↑ tout monte ou tout descend
        ▓▓▓▓│▓▓▓▓▓▓▓▓▓▓▓
        ────┼───────────── delta
        ▒▒▒▒│▒▒▒▒▒▒▒▒▒▒▒
            │    ↓
           put          call

                  PC2 : TERM SLOPE
            IV
  1M ────────▓▓▓▓▓▓       court monte
  3M ──────────▒▒▒▒       médian stable
  6M ────────────░░       long descend
  
                  PC3 : SMILE
            IV
            │
        ▓▓▓▓│▓▓▓▓   wings montent
        ────┼────   ATM stable
            │       (ou inverse)
           put   call
```

### Seuils d'action

| z-score | Interprétation | Action |
|---|---|---|
| \|z\| < 1.0 | Pas de signal | WAIT |
| 1.0 ≤ \|z\| < 1.5 | Signal faible | WAIT, surveiller |
| 1.5 ≤ \|z\| < 2.0 | Signal modéré | ARM TRADE possible |
| 2.0 ≤ \|z\| < 3.0 | Signal fort | ARM TRADE recommandé |
| \|z\| ≥ 3.0 | Signal extrême | VÉRIFIER (régime change ? data issue ?) |

### Règle critique de cohérence

**Si deux signaux se contredisent, ne trade AUCUN des deux.**

Exemple de contradiction : PC1 dit CHEAP (z=+2.0) mais PC2 dit
EXPENSIVE avec loading positif sur courts tenors. Ça signifie que les
courts tenors sont EXPENSIVE isolément mais qu'en moyenne la surface
est CHEAP. Le modèle te dit deux choses opposées → la matrice PCA
n'est pas fiable dans ce snapshot, probablement régime change en cours.

Trade uniquement les signaux mutuellement **indépendants** (z-scores
stables pendant au moins 2-3 cycles de 30s) ou **cohérents** (pointent
dans le même sens trading).

### Bouton "Arm trade"

Cliquer "Arm trade" sur une colonne déclenche le Panel 3 avec :
- Signal ID pré-rempli
- Structure recommandée pré-sélectionnée
- Tenor par défaut (3M sauf cas spécifique)
- Sizing initial basé sur |z-score|

---

## Panel 3 — Trade Preview

C'est là où tu décides vraiment. Le panel te montre la structure en
détail avant soumission.

### Ce que tu vois

```
╔══════════════════════════════════════════════════════════╗
║  STRUCTURE : Straddle ATM 3M (BUY)                       ║
║  Signal : PC1 CHEAP (z=+1.8)                             ║
╠══════════════════════════════════════════════════════════╣
║  SECTION A — Legs                                        ║
║  ┌──────┬──────────┬────────┬────┬─────┬──────┬──────┐  ║
║  │ Leg  │ Contract │ Strike │DTE │ Qty │ Side │  IV  │  ║
║  ├──────┼──────────┼────────┼────┼─────┼──────┼──────┤  ║
║  │ 1    │ EUU C Jul│ 1.1800 │ 90 │ 10  │ BUY  │ 6.05%│  ║
║  │ 2    │ EUU P Jul│ 1.1800 │ 90 │ 10  │ BUY  │ 6.05%│  ║
║  │ 3    │ 6E future│ —      │ 90 │ -3  │ SELL │  —   │  ║
║  └──────┴──────────┴────────┴────┴─────┴──────┴──────┘  ║
║                                                          ║
║  SECTION B — Greeks net                                  ║
║  Vega    : +847  $/vol_pt                                ║
║  Gamma   : +2.3  $/($0.01 spot move)²                    ║
║  Theta   : -89   $/day                                   ║
║  Delta   : ~0    (hedged)                                ║
║                                                          ║
║  SECTION C — Pricing                                     ║
║  Premium : -$3,420 (paid)                                ║
║  Breakeven spot : ±380 pips from 1.1800                  ║
║  Max loss : $3,420 (if nothing moves)                    ║
║  Vega edge : ≈ +$847 × expected IV reprice ~0.8%  ≈ +$680║
║                                                          ║
║  SECTION D — Scenarios forecast                          ║
║  ┌──────────────────┬──────┬──────┬──────┐              ║
║  │                  │ Sc.A │ Sc.B │ Sc.C │              ║
║  │ spot move        │  2%  │  0%  │  0.5%│              ║
║  │ IV reprice       │ +1.0%│  0%  │ -1.0%│              ║
║  │ P&L gamma/theta  │+1200 │ -800 │ -500 │              ║
║  │ P&L vega         │ +847 │   0  │ -847 │              ║
║  │ TOTAL P&L        │+2047 │ -800 │-1347 │              ║
║  └──────────────────┴──────┴──────┴──────┘              ║
║                                                          ║
║  SECTION E — Sizing                                      ║
║  Base size       : 10 contracts                          ║
║  × |z-score|/1.5 : × 1.2  (= 1.8/1.5)                    ║
║  × book penalty  : × 0.9  (some vega already long)       ║
║  × event damper  : × 1.0  (not active)                   ║
║  → FINAL QTY     : 11 contracts each leg                 ║
║                                                          ║
║  [ Submit to execution queue ]  [ Cancel ]               ║
╚══════════════════════════════════════════════════════════╝
```

### Comment lire les sections

**Section A — Legs** : ce que tu vas exécuter concrètement. Un straddle
= 2 options options (call + put même strike + même maturité). La ligne
"6E future" est le delta hedge automatique.

**Section B — Greeks nets** : ton exposition résultante.

- **Vega positif** = tu gagnes si IV monte. Ici +847 $/vol pt → si IV
  passe de 6.05% à 7.05%, gain mark-to-market = +$847.
- **Gamma positif** = tu gagnes sur les gros mouvements dans les deux
  sens. Ici +2.3 $/(0.01 spot move)² → un mouvement de 0.02 sur spot
  génère ~+$9 de P&L convexe (avant delta hedge).
- **Theta négatif** = tu payes le temps qui passe. Ici -$89/jour si
  rien ne bouge.
- **Delta ~0** = pas de pari directionnel (hedgé).

**Section C — Pricing** :

- **Premium** : cash out (pour un BUY).
- **Breakeven** : amplitude de mouvement spot nécessaire pour rentrer
  dans ses frais (si aucun reprice de IV).
- **Max loss** = premium total (tu ne peux pas perdre plus que ce que
  tu as payé, propriété des options longues).
- **Vega edge** = gain attendu si la thèse se réalise (IV reprice).

**Section D — Scenarios** : stress test ex-ante. Si même le scénario
C (adverse) ne dépasse pas 50% de ton capital alloué au trade, c'est
OK. Sinon → réduis la taille.

**Section E — Sizing** : la taille finale est mécanique, pas discrétionnaire :

```
final_qty = base_size
          × (|z-score| / threshold_min)      # conviction scaling
          × (1 - α_book × |book_ratio|)       # book penalty
          × (0.5 if event_dampener else 1.0)  # event caution
```

### Checks avant submit

Le bouton "Submit" est grisé tant que :

1. Régime ≠ PRÉ-ÉVÉNEMENT
2. Signal encore actif (z-score n'a pas flipped depuis l'arm)
3. Max loss < 2% du capital total
4. Vega total du book post-trade < limite de risque
5. IV data fraîche (< 2 min)

Si un check fail, le bouton affiche la raison du blocage.

---

## Panel 4 — Active Positions Monitor

### Ce que tu vois

```
╔══════════════════════════════════════════════════════════╗
║  OPEN STRUCTURES (3 active)                              ║
║  ┌─────┬──────┬───┬──────┬──────┬──────┬──────┬──────┐   ║
║  │ ID  │Struct│DTE│Entry │Curr. │ P&L  │ Vega │Action│   ║
║  │     │      │   │signal│signal│  $   │  $   │      │   ║
║  ├─────┼──────┼───┼──────┼──────┼──────┼──────┼──────┤   ║
║  │ T01 │STR 3M│ 72│PC1+1.│PC1+1.│+1,240│ +980 │ HOLD │   ║
║  │     │      │   │ 8    │ 4    │      │      │      │   ║
║  │ T02 │BF 3M │ 72│PC3+2.│PC3+0.│ +340 │ +125 │ EXIT │   ║
║  │     │      │   │ 3    │ 2 !  │      │      │      │   ║
║  │ T03 │CAL   │ 35│PC2-1.│PC2-1.│ -180 │ +410 │ HOLD │   ║
║  │     │1M/3M │   │ 7    │ 8    │      │      │      │   ║
║  └─────┴──────┴───┴──────┴──────┴──────┴──────┴──────┘   ║
║                                                          ║
║  AGGREGATE GREEKS                                        ║
║                                                          ║
║  Vega by tenor  : 1M ▓▓▓▓░░░░░░  +410 $                  ║
║                   3M ▓▓▓▓▓▓▓▓▓░ +1,105 $                 ║
║                   6M ░░░░░░░░░░    0  $                  ║
║  Total vega     : +1,515                                 ║
║  Total gamma    :   +5.2                                 ║
║  Total theta    :    -180 $/day                          ║
║  Net delta      :  +0.03 (within hedge band)             ║
║                                                          ║
║  DELTA HEDGE STATUS                                      ║
║  Current imbalance  : +0.03                              ║
║  Rebalance trigger  : |Δ| > 0.05                         ║
║  Last hedge         : 2h 14min ago (-3 EUR future lots)  ║
║                                                          ║
║  EXIT ALERTS                                             ║
║  ● T02 : Current signal z=0.2 (flipped below threshold)  ║
║          → EXIT RECOMMENDED                              ║
╚══════════════════════════════════════════════════════════╝
```

### Comment lire

**Open structures** : vue unifiée de toutes tes positions vol. Pas de
vue "par contrat" — on regroupe par structure parce que c'est l'unité
de décision, pas le contrat individuel.

**Colonne Current signal** : indique si ton thèse d'entrée est toujours
valide.

- Vert : signal encore dans la même direction avec même intensité
- Orange (!) : signal affaibli (z-score s'est réduit de > 50%)
- Rouge : signal flipped (exit recommandé)

**Action** :

| Status signal | Action recommandée |
|---|---|
| HOLD | Signal actif, garder la position |
| TRIM | Signal s'affaiblit, réduire 50% |
| EXIT | Signal flipped ou timeout, sortir complètement |

### Règles de sortie systématique

Indépendamment du signal, une position sort automatiquement si :

1. **Signal reverse** : le z-score d'entrée flippe de signe OU descend
   sous 0.5 en valeur absolue
2. **Time-based** : T_remaining < 0.3 × T_entry (theta devient
   dominant, plus d'edge vega)
3. **Stop loss en vega** : P&L < -3 × vega_at_entry (= IV a bougé
   3 vol pts contre nous sur le strike entré)
4. **Time to expiry < 7 jours** : sortie forcée (théta extrême,
   gamma inmaîtrisable)

Le panel affiche EXIT ALERTS pour toute position matching ces critères.

---

## Panel 5 — Surface Diagnostic

Outil de visualisation, pas de décision. Utile pour comprendre
visuellement ce que font les modèles et détecter des anomalies.

### 4 tabs

**Tab 1 — Live Smile** (par tenor)

Le smile observé + fair smile + bandes ±1σ.

```
  IV
   │
   │  ╱───── historical fair ±1σ (band)
   │ ╱
   ●  ←── observed point OUTSIDE band = anomalie
   │  ╲_____
   │       ╲___
   │           ●────●      SVI fit (current)
   │              ╲___
   │                  ╲___
   │                      ●     fair smile (EWMA)
   └────────────────────────── delta
   10P    25P   ATM  25C  10C
```

**Tab 2 — Parameter Dynamics**

Time series des 5 paramètres SVI. Permet d'identifier visuellement les
régimes.

```
  a (level)      ∿∿∿∿╱╲╱╲∿∿∿∿╱╲∿∿     ← mean reversion normale
  b (tightness)  ∿∿∿∿∿∿∿╱╲╱╲╱╲╱╲╱∿    ← regime shift vers wings larges
  ρ (skew)       ╲╲╲╲╲_______╱╱╱╱╱    ← skew flipping, inhabituel
  m (shift)      ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿    ← stable, pas de spot lean
  σ (convex)     ___╱╲___╱╲___╱╲___    ← oscillation event-driven
```

**Tab 3 — Surface Heatmap**

Vue 2D tenor × delta_pillar, couleur = z-score vs fair.

```
        10P    25P    ATM    25C    10C
  1M  │ ░░  │ ▒▒  │ ▓▓  │ ▒▒  │ ░░  │    z=+2.3 ATM
  2M  │ ░░  │ ░░  │ ▓▓  │ ▒▒  │ ░░  │    cheap sur ATM 3M
  3M  │ ▒▒  │ ▒▒  │ ▓▓  │ ▓▓  │ ▓▓  │    ← ligne la plus interessante
  4M  │ ░░  │ ░░  │ ▒▒  │ ▒▒  │ ░░  │
  6M  │ ░░  │ ░░  │ ░░  │ ░░  │ ░░  │

  Legend: ░ z<1, ▒ 1<z<2, ▓ z>2
```

**Tab 4 — No-arb Health**

Table de checks :

| Tenor | g(k) min | ∂w/∂T | SVI RMSE | Status |
|---|---|---|---|---|
| 1M | +0.003 | OK | 0.0012 | ✓ |
| 2M | +0.001 | OK | 0.0018 | ✓ |
| 3M | -0.002 | OK | 0.0024 | ⚠ butterfly violated |
| 4M | +0.004 | OK | 0.0021 | ✓ |

Si ⚠ apparaît → le smile fitted a de l'arbitrage, signal peu fiable
sur ce tenor → ne trade pas ce tenor avant correction.

---

## Panel 6 — Model Health

Panel d'audit, consulté hebdomadairement pour valider que le modèle
fonctionne bien.

### 4 sections

**A — VRP Validation** : scatter plot VRP prédit vs réalisé. Si les
points sont alignés sur y=x, le modèle prédit bien. Si systématiquement
au-dessus → modèle biaisé haut (sur-estime VRP, sous-estime IV fair,
génère faux CHEAP).

**B — Signal/Residual Health** : distribution des z-scores PC. Doit
ressembler à une N(0,1). Si bimodal ou skewed → modèle mal calibré.

**C — PCA Health** : stabilité des loadings. Si les loadings du PC1
varient beaucoup entre deux fits mensuels, la réduction de dim n'est
pas fiable.

**D — Data Quality** : latence du pipeline, heartbeats, taux de
validation failure. Si quelque chose est rouge, les signaux actuels
peuvent être compromis.

---

## Workflow type — journée de trading

### 09:00 — Market open

1. Ouvrir le frontend, regarder Panel 1 (Regime Detector).
2. Si régime = PRÉ-ÉVÉNEMENT → fermer le trading pour la journée,
   vérifier que toutes les positions critiques sont ok.
3. Sinon → passer au Panel 2.

### 09:00-12:00 — Observation passive

4. Regarder Panel 2. Si aucun signal |z| > 1.5 → ne rien faire.
   Observer le marché, vérifier que les metrics bougent normalement.
5. Consulter Panel 5 Tab 3 (heatmap) pour vue synoptique.
6. Si quelque chose semble anormal → Panel 5 Tab 4 (no-arb), Panel 6
   (model health).

### Signal se déclenche

7. Panel 2 : |z| franchit 1.5 sur PC1/PC2/PC3.
8. Cliquer "Arm trade" → Panel 3.
9. Vérifier sections A-E. Valider que sizing, greeks, scenarios sont
   cohérents avec ce que tu attendais.
10. Si oui → Submit. Sinon → Cancel, retour Panel 2.

### Position active

11. Monitoring continu via Panel 4.
12. Checks à chaque cycle 30s : signal toujours actif ? P&L
    raisonnable ? delta hedge dans la bande ?
13. Si exit alert apparaît → exécuter la sortie immédiatement, pas de
    discrétion.

### End of day

14. Panel 4 : récap des positions, greeks totaux, theta bleed anticipé.
15. Panel 6 weekly : valider que le modèle produit des signaux
    cohérents, pas de drift détecté.

---

## Erreurs typiques à éviter

**E1 — Trader contre un signal faible "parce que l'intuition dit autre chose"**

Si le modèle dit z=+0.8, ne trade pas. Ne trade pas non plus l'inverse.
Le modèle dit "pas de signal clair", point. Ta discrétion n'ajoute pas
d'edge, elle ajoute du bruit.

**E2 — Augmenter la taille après une perte**

Martingale = suicide en vol trading. Si une position perd, c'est que
le signal s'affaiblit ou que le régime a changé. La bonne action est
de réduire, pas doubler.

**E3 — Ignorer les exit alerts "parce qu'il reste du time value"**

Le time value, c'est du theta qui te bouffe pendant que tu espères.
Exit signal = exit. Le modèle a détecté que ton edge initial a
disparu.

**E4 — Tout trader simultanément quand plusieurs signaux se déclenchent**

Trois signaux |z| > 2 en même temps sur PC1/PC2/PC3 ≠ trois trades
géniaux. Ça peut être un régime change qui produit transitoirement
des z-scores élevés partout. Attendre 2-3 cycles que les signaux se
stabilisent avant d'armer.

**E5 — Sortir en panique sur un drawdown mark-to-market**

Les straddles longs ont une distribution P&L skewed : petits drawdowns
fréquents, gros gains rares. Le mark-to-market en milieu de vie peut
être négatif même si la position finit profitable. Laisser les règles
de sortie décider, pas l'émotion.

---

## Limitations du cockpit

- **Pas de trading autonome** : tous les trades passent par une action
  humaine (bouton Submit). Le cockpit propose, l'utilisateur dispose.
- **Pas de detection intraday de régime switch** : le régime est
  recalculé toutes les 30s, mais un régime change réel prend plusieurs
  heures à être détecté robustement. Attention aux whipsaws.
- **Pas de gestion multi-sous-jacent** : tout est EUR/USD. Pour
  extension à GBP/USD ou USD/JPY, dupliquer le stack.
- **Pas de liquidité checking** : le bouton Submit ne vérifie pas que
  la taille demandée est absorbable par le book IB. Commencer petit,
  augmenter progressivement.
