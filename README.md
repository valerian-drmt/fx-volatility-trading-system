## Link
- [SMC library](https://github.com/joshyattridge/smart-money-concepts?tab=readme-ov-file#sessions)


| Indicator             | Theme              |
|-----------------------|--------------------|
| resample_with_vwap    | Liquidity          |
| market_sessions       | Market Context     |
| pivot_point           | Support/Resistance |
| volume_pivot_point    | Volume-based S/R   |
| volume_delta          | Momentum           |
| cvd                   | Momentum           |
| obi                   | Pressure           |
| price_change          | Price Action       |
| reaction_ratio        | Liquidity          |
| rolling_std_price     | Volatility         |
| rolling_mean_cvd      | Smoothed Flow      |



Condition for a data series to be stationary:
- no trend -> test Trend with Ducky-Fuller 
- constant variance
- no dynamics change

Stationarize data with: diff perf, log perf, sqrt perf, relative diff perf



Les séries temporelles présentent des caractéristiques spécifiques (dépendance temporelle, saisonnalité, tendances) qui nécessitent des approches adaptées en machine learning. Voici les principales catégories de modèles de machine learning adaptés aux séries temporelles, avec une explication concise pour chaque catégorie :

1. Modèles statistiques classiques
Description : Ces modèles reposent sur des hypothèses statistiques (souvent linéaires) et modélisent explicitement les composantes temporelles (tendance, saisonnalité, autocorrélation).
Exemples :
ARIMA (AutoRegressive Integrated Moving Average) : Combine composantes autorégressives, différences et moyenne mobile pour les séries stationnaires.
SARIMA : Extension d’ARIMA avec prise en charge de la saisonnalité.
ETS (Exponential Smoothing) : Modélise la série avec des composantes de niveau, tendance et saisonnalité via un lissage exponentiel.
Avantages : Interprétables, adaptés aux séries linéaires et stationnaires, nécessitent moins de données.
Inconvénients : Limités pour les relations non linéaires ou les données complexes.
Cas d’usage : Prévisions économiques, séries avec forte saisonnalité (ex. ventes mensuelles).
2. Modèles d’apprentissage supervisé (basés sur des features)
Description : Les séries temporelles sont transformées en problème supervisé en créant des features comme des décalages (lags), des indicateurs temporels (mois, jour), ou des variables exogènes.
Exemples :
Régression linéaire/logistique : Utilisée avec des features temporelles.
Arbres de décision : Modèles comme Random Forest ou Gradient Boosting (ex. XGBoost, LightGBM, CatBoost).
Avantages : Flexibles, capturent des relations non linéaires, intègrent facilement des variables exogènes.
Inconvénients : Nécessitent un prétraitement (feature engineering) et ne modélisent pas directement la dépendance temporelle.
Cas d’usage : Prévisions avec variables externes (ex. ventes influencées par promotions, météo).
3. Modèles de boosting (Gradient Boosting)
Description : Une sous-catégorie des modèles supervisés, utilisant des techniques d’ensemble séquentiel pour améliorer les prédictions.
Exemples :
XGBoost, LightGBM, CatBoost : Arbres de décision boostés appliqués aux séries temporelles après transformation en données tabulaires.
Avantages : Très performants sur des séries complexes, robustes aux données bruitées.
Inconvénients : Nécessitent un feature engineering poussé et un réglage des hyperparamètres.
Cas d’usage : Prévisions complexes (ex. consommation énergétique, trafic réseau).
4. Réseaux de neurones (Deep Learning)
Description : Modèles basés sur des réseaux de neurones, particulièrement adaptés aux séries temporelles complexes avec des motifs non linéaires ou des données volumineuses.
Exemples :
RNN (Recurrent Neural Networks) : Conçus pour capturer les dépendances temporelles.
LSTM (Long Short-Term Memory) : Variante de RNN pour les longues dépendances.
GRU (Gated Recurrent Unit) : Version simplifiée de LSTM.
Transformers (ex. Temporal Fusion Transformer, Informer) : Utilisent l’attention pour modéliser des dépendances complexes.
CNN (Convolutional Neural Networks) : Parfois utilisés pour extraire des motifs locaux dans les séries.
Avantages : Puissants pour les séries non linéaires, grandes quantités de données, ou motifs complexes.
Inconvénients : Nécessitent beaucoup de données et de ressources computationnelles, moins interprétables.
Cas d’usage : Prévisions à long terme, séries multivariées (ex. prévisions météo, finance).
5. Modèles bayésiens
Description : Utilisent des approches probabilistes pour modéliser l’incertitude dans les séries temporelles.
Exemples :
Modèles bayésiens structurés (ex. Prophet de Facebook) : Modélisent tendance, saisonnalité et effets exogènes avec incertitude.
Filtres de Kalman : Utilisés pour les séries dynamiques avec bruit.
Avantages : Gestion de l’incertitude, interprétabilité, adaptés aux séries irrégulières.
Inconvénients : Moins performants pour les motifs non linéaires complexes.
Cas d’usage : Prévisions avec incertitude (ex. prévisions marketing, séries irrégulières).
6. Modèles hybrides
Description : Combinent plusieurs approches (ex. statistique + machine learning) pour tirer parti des forces de chaque catégorie.
Exemples :
ARIMA + XGBoost : ARIMA pour la partie linéaire, XGBoost pour les résidus non linéaires.
Prophet + Deep Learning : Prophet pour la saisonnalité, réseaux neuronaux pour les motifs complexes.
Avantages : Combine robustesse et précision, adapté aux séries complexes.
Inconvénients : Complexité accrue, nécessite expertise pour combiner les modèles.
Cas d’usage : Séries temporelles avec composantes linéaires et non linéaires (ex. ventes avec promotions et tendances saisonnières).
7. Modèles spécifiques aux séries temporelles irrégulières ou multivariées
Description : Conçus pour des séries avec des observations irrégulières ou des données multivariées (plusieurs séries corrélées).
Exemples :
VAR (Vector AutoRegression) : Pour séries temporelles multivariées.
State Space Models : Pour séries irrégulières ou bruitées.
Temporal Convolutional Networks (TCN) : Adaptés aux séries longues ou multivariées.
Avantages : Adaptés aux séries complexes ou multivariées.
Inconvénients : Complexité accrue, besoin de données riches.
Cas d’usage : Prévisions économiques multivariées, IoT, capteurs multiples.
Conclusion
Le choix de la catégorie dépend de :

Type de série : Linéaire (ARIMA, ETS) vs non linéaire (deep learning, boosting).
Volume de données : Modèles statistiques pour petites données, deep learning pour grandes données.
Complexité : Modèles simples (ARIMA) pour séries simples, hybrides ou deep learning pour séries complexes.
Ressources : Deep learning nécessite plus de calcul que les modèles statistiques.
Interprétabilité : Modèles statistiques ou bayésiens plus interprétables que le deep learning.
Si vous avez un cas spécifique (ex. type de données, objectif) ou souhaitez un exemple avec un modèle particulier, précisez-le, et je peux approfondir ou fournir un exemple pratique !


### test
