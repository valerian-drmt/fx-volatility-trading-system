"""GMM diagnostic — explain why predict_proba returns the values it does.

Re-fits the GMM on the current ``feature_history`` and prints, for each
component :
  - weight π_k (mixing prior)
  - mean μ_k (in the original units : vol_level %, vol_of_vol %)
  - covariance Σ_k
  - 1-σ ellipse extent (eigenvalues of Σ_k)
  - distance Mahalanobis from the live point
  - density N(x|μ_k, Σ_k) at the live point

Then walks through the Bayes posterior step by step so you can see why
``predict_proba`` produced the final percentages.

Run :
    docker exec -it fxvol-vol-engine python scripts/gmm_diagnostic.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
from sqlalchemy import desc, select

from core.vol.gmm_regime import MIN_OBS_GMM, fit_gmm
from persistence.db import get_sessionmaker
from persistence.models import FeatureHistory

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gmm_diag")


async def load_data() -> tuple[np.ndarray, np.ndarray]:
    """Returns (X_train, x_live) — training matrix + most recent live point."""
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(FeatureHistory.vol_level_z90, FeatureHistory.vol_of_vol_30d_pct,
                   FeatureHistory.iv_atm_3m_pct, FeatureHistory.timestamp)
            .where(FeatureHistory.symbol == "EURUSD")
            .order_by(desc(FeatureHistory.timestamp))
        )).all()

    train: list[tuple[float, float]] = []
    for r in rows:
        iv = r[2]
        vov = r[1]
        if iv is not None and vov is not None:
            train.append((float(iv), float(vov)))
    train.reverse()  # chronological for fit
    X = np.asarray(train, dtype=float)
    # Live = most recent obs.
    x_live = X[-1] if len(X) else np.array([np.nan, np.nan])
    return X, x_live


def mahalanobis(x: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    diff = x - mu
    inv = np.linalg.inv(sigma)
    return float(np.sqrt(diff @ inv @ diff))


def gaussian_density(x: np.ndarray, mu: np.ndarray, sigma: np.ndarray) -> float:
    d = len(mu)
    diff = x - mu
    inv = np.linalg.inv(sigma)
    det = np.linalg.det(sigma)
    norm = (2 * np.pi) ** (-d / 2) * det ** (-0.5)
    return float(norm * np.exp(-0.5 * diff @ inv @ diff))


def main_sync(X: np.ndarray, x: np.ndarray) -> None:
    if X.shape[0] < MIN_OBS_GMM:
        log.info("X has %d obs (< MIN_OBS_GMM=%d) — GMM would not fit",
                 X.shape[0], MIN_OBS_GMM)
        return

    log.info("=" * 64)
    log.info("=== GMM diagnostic — N=%d training obs, x_live=%s ===", X.shape[0], x)
    log.info("=" * 64)
    log.info("Training data ranges :")
    log.info("  vol_level  : [%.3f, %.3f]  mean=%.3f  std=%.3f",
             X[:, 0].min(), X[:, 0].max(), X[:, 0].mean(), X[:, 0].std())
    log.info("  vol_of_vol : [%.3f, %.3f]  mean=%.3f  std=%.3f",
             X[:, 1].min(), X[:, 1].max(), X[:, 1].mean(), X[:, 1].std())
    log.info("Live x         : vol_level=%.3f  vol_of_vol=%.3f", x[0], x[1])
    out_of_range = (
        (x[0] < X[:, 0].min() or x[0] > X[:, 0].max()) or
        (x[1] < X[:, 1].min() or x[1] > X[:, 1].max())
    )
    log.info("Live in training range ? %s", "no — extrapolation" if out_of_range else "yes")
    log.info("")

    gmm, fit = fit_gmm(X)
    if gmm is None or fit is None:
        log.info("fit_gmm returned None")
        return

    log.info("GMM fit converged=%s, log-likelihood=%.3f", fit.converged,
             gmm.score(X) * X.shape[0])
    log.info("Component → label mapping (sorted by μ_vol_level ascending) :")
    for comp_idx, label in fit.component_to_label.items():
        log.info("  component %d → %s", comp_idx, label)
    log.info("")

    # Per-component diagnostic
    densities = []
    for i in range(gmm.n_components):
        mu = gmm.means_[i]
        sigma = gmm.covariances_[i]
        pi = gmm.weights_[i]
        eigs = np.linalg.eigvalsh(sigma)
        d_maha = mahalanobis(x, mu, sigma)
        density = gaussian_density(x, mu, sigma)
        weighted = pi * density
        densities.append(weighted)
        label = fit.component_to_label.get(i, f"comp{i}")
        log.info("Component %d  (label = %s)", i, label)
        log.info("  weight π_%d           = %.4f", i, pi)
        log.info("  mean μ                = vol_level=%.3f  vol_of_vol=%.3f", mu[0], mu[1])
        log.info("  covariance Σ          = [[%.4f, %.4f], [%.4f, %.4f]]",
                 sigma[0, 0], sigma[0, 1], sigma[1, 0], sigma[1, 1])
        log.info("  1-σ ellipse axes      = √λ_1=%.3f, √λ_2=%.3f", np.sqrt(eigs[0]), np.sqrt(eigs[1]))
        log.info("  mahalanobis(x, μ)     = %.3f", d_maha)
        log.info("  density N(x|μ,Σ)      = %.3e", density)
        log.info("  π · N (numerator)     = %.3e", weighted)
        log.info("")

    # Bayes posterior
    total = sum(densities)
    log.info("Bayes posterior P(k|x) = π_k · N_k / Σ_j π_j · N_j")
    log.info("  Sum of numerators (denominator) = %.3e", total)
    posteriors = [d / total if total > 0 else 0.0 for d in densities]
    for i, p in enumerate(posteriors):
        label = fit.component_to_label.get(i, f"comp{i}")
        log.info("  P(component=%d, label=%s | x) = %.6f  (%.2f %%)", i, label, p, p * 100)
    log.info("")

    # Cross-check vs sklearn
    sklearn_proba = gmm.predict_proba(x.reshape(1, -1))[0]
    log.info("Cross-check sklearn predict_proba : %s", [f"{p:.6f}" for p in sklearn_proba])
    log.info("(should match the manual Bayes computation above)")


async def main() -> None:
    X, x_live = await load_data()
    if np.isnan(x_live).any():
        log.info("No live observation in feature_history — abort")
        return
    main_sync(X, x_live)


if __name__ == "__main__":
    asyncio.run(main())
