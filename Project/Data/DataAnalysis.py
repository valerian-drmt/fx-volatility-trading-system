import pandas as pd
from sklearn.feature_selection import mutual_info_classif
from sklearn.feature_selection import RFE
from sklearn.ensemble import RandomForestClassifier
from sklearn.decomposition import PCA
import plotly.express as px
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.tools.tools import add_constant

# 🔧 Config import
import os
from Project.Config.LoggerConfig import *
logger = colored_logger()
current_file = os.path.basename(__file__)
logger.info(f"Logger initialized ({current_file})")

class DataAnalysis:
    def __init__(self, data, label_columns):
        self.data = data.dropna()
        self.label_columns = label_columns
        self.feature_columns = [col for col in self.data.columns if col not in label_columns]

    def plot_full_correlation_matrix(self):
        """
        Affiche une matrice de corrélation complète (features + labels) en heatmap interactive avec Plotly.
        """
        all_columns = self.data.columns
        corr_matrix = self.data[all_columns].corr()

        fig = px.imshow(
            corr_matrix,
            labels=dict(x="Variables", y="Variables", color="Correlation"),
            x=all_columns,
            y=all_columns,
            color_continuous_scale="RdBu",
            zmin=-1, zmax=1,
            title="Full Correlation Matrix"
        )
        fig.update_layout(height=800)
        fig.show()

    def plot_mutual_information_classification(self):

        for label in self.label_columns:
            x = self.data[self.feature_columns]
            y = self.data[label]

            # Mutual Information
            mi = mutual_info_classif(x, y, random_state=42)
            mi_series = pd.Series(mi, index=self.feature_columns).sort_values(ascending=False)

            # Plotly bar chart
            fig = px.bar(
                mi_series,
                x=mi_series.index,
                y=mi_series.values,
                labels={'x': 'Features', 'y': 'Mutual Information'},
                title=f"Mutual Information (Classification) with Target: {label}"
            )
            fig.update_layout(xaxis_tickangle=-45)
            fig.show()

    def plot_random_forest_importance(self):
        """
        Calcule et affiche l'importance des features pour chaque label binaire
        en utilisant un RandomForestClassifier. Affichage interactif avec Plotly.
        """
        for label in self.label_columns:
            x = self.data[self.feature_columns]
            y = self.data[label]

            model = RandomForestClassifier(n_estimators=100, random_state=42)
            model.fit(x, y)

            importances = pd.Series(model.feature_importances_, index=self.feature_columns)
            importances = importances.sort_values(ascending=False)

            fig = px.bar(
                importances,
                x=importances.index,
                y=importances.values,
                labels={'x': 'Features', 'y': 'Feature Importance'},
                title=f"Random Forest Feature Importance with Target: {label}"
            )
            fig.update_layout(xaxis_tickangle=-45)
            fig.show()

    def plot_rfe_selected_features(self, n_features_to_select=10):
        """
        Applique Recursive Feature Elimination (RFE) avec RandomForestClassifier
        et affiche les features sélectionnées pour chaque label binaire via Plotly.

        Parameters:
        - n_features_to_select (int): nombre de features à conserver
        """
        for label in self.label_columns:
            x = self.data[self.feature_columns]
            y = self.data[label]

            estimator = RandomForestClassifier(n_estimators=100, random_state=42)
            selector = RFE(estimator=estimator, n_features_to_select=n_features_to_select, step=1)
            selector.fit(x, y)

            ranking = pd.Series(selector.ranking_, index=self.feature_columns)
            selected_features = ranking[ranking == 1].sort_index()

            # Plot des features sélectionnées
            fig = px.bar(
                selected_features,
                x=selected_features.index,
                y=selected_features.values,
                labels={'x': 'Features', 'y': 'Ranking (1 = selected)'},
                title=f"RFE Selected Features for Target: {label}"
            )
            fig.update_layout(xaxis_tickangle=-45)
            fig.show()

            # Print complet optionnel
            print(f"\nFull RFE ranking for target: {label}")
            print(ranking.sort_values())

    def plot_pca_analysis(self):
        """
        Applique l'analyse en composantes principales (PCA) sur les features et
        affiche deux graphiques :
        - L'importance des features dans la première composante (PC1)
        - La variance expliquée cumulée par les composantes principales
        """
        x = self.data[self.feature_columns]

        # Appliquer PCA
        pca = PCA(n_components=len(self.feature_columns))
        pca.fit_transform(x)

        # Loadings des features sur chaque composante
        loadings = pd.DataFrame(
            pca.components_.T,
            index=self.feature_columns,
            columns=[f"PC{i + 1}" for i in range(len(self.feature_columns))]
        )

        # Variance expliquée
        explained_var = pca.explained_variance_ratio_
        cum_explained_var = explained_var.cumsum()

        # Graphe 1 : contribution des features à PC1
        pc1_loadings = loadings["PC1"].sort_values(key=abs, ascending=False)

        fig1 = px.bar(
            pc1_loadings,
            x=pc1_loadings.index,
            y=pc1_loadings.values,
            title="Feature Importance in Principal Component 1 (PC1)",
            labels={"x": "Features", "y": "Loading (Weight in PC1)"}
        )
        fig1.update_layout(xaxis_tickangle=-45)
        fig1.show()

        # Graphe 2 : variance expliquée cumulée
        pca_df = pd.DataFrame({
            "Principal Component": [f"PC{i + 1}" for i in range(len(explained_var))],
            "Explained Variance": explained_var,
            "Cumulative Variance": cum_explained_var
        })

        fig2 = px.line(
            pca_df,
            x="Principal Component",
            y="Cumulative Variance",
            markers=True,
            title="Cumulative Explained Variance by PCA Components",
            labels={"Cumulative Variance": "Cumulative Variance Ratio"}
        )
        fig2.update_layout(yaxis_range=[0, 1.05])
        fig2.show()

    def plot_cross_correlation(self, max_lag=20):
        """
        Affiche une heatmap de la cross-correlation entre chaque feature et chaque label
        binaire pour une plage de décalages (lags) allant de -max_lag à +max_lag.

        Parameters:
        - max_lag (int): Décalage maximal à tester dans les deux directions (futur/passé)
        """
        for label in self.label_columns:
            y = self.data[label]
            correlations = {}

            for feature in self.feature_columns:
                corr_values = []
                for lag in range(-max_lag, max_lag + 1):
                    shifted_y = y.shift(-lag)
                    correlation = self.data[feature].corr(shifted_y)
                    corr_values.append(correlation)
                correlations[feature] = corr_values

            lags = list(range(-max_lag, max_lag + 1))
            corr_df = pd.DataFrame(correlations, index=lags).T  # features en lignes, lags en colonnes

            fig = px.imshow(
                corr_df,
                labels=dict(x="Lag", y="Feature", color="Correlation"),
                x=lags,
                y=self.feature_columns,
                title=f"Cross-Correlation with Target: {label}",
                color_continuous_scale="RdBu",
                zmin=-1, zmax=1
            )
            fig.update_layout(height=600)
            fig.show()

    def plot_vif_scores(self):
        """
        Calcule le Variance Inflation Factor (VIF) pour chaque feature afin d'évaluer
        la multicolinéarité. Affichage interactif avec Plotly.
        """
        x = self.data[self.feature_columns]

        # Ajouter constante pour statsmodels
        x_const = add_constant(x)

        # Calcul du VIF (on ignore l'intercept => i + 1)
        vif_data = pd.DataFrame()
        vif_data["Feature"] = x.columns
        vif_data["VIF"] = [variance_inflation_factor(x_const.values, i + 1) for i in range(len(x.columns))]

        # Trier les résultats
        vif_data = vif_data.sort_values(by="VIF", ascending=False)

        # Affichage Plotly
        fig = px.bar(
            vif_data,
            x="Feature",
            y="VIF",
            title="Variance Inflation Factor (VIF) for Features",
            labels={"VIF": "VIF Score", "Feature": "Feature"}
        )
        fig.update_layout(xaxis_tickangle=-45)
        fig.show()