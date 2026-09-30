"""
MandiIQ - Debiased / orthogonal machine learning.

The RDD estimator identifies the causal effect of *crossing* the drought
threshold. Double/debiased machine learning (Chernozhukov et al., 2018) answers
the complementary continuous question: how strongly does the full rainfall
departure distribution move prices, after flexible control for seasonality,
price inertia and volatility?

Implementation notes:
- Partially linear model: Y = theta * D + g(X) + e, D = m(X) + v.
- Neyman-orthogonal, cross-fitted residuals for both nuisance functions stop
  overfitting from contaminating the causal parameter.
- Inference uses the influence function, so standard errors stay valid even
  when the nuisance learners are regularised.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def partial_linear_dml(
    y,
    d,
    X,
    n_splits: int = 2,
    random_state: int = 42,
    max_iter: int = 120,
) -> dict:
    """Cross-fitted partially linear DML estimate of theta.

    Args:
        y: Outcome vector (e.g. log modal price).
        d: Treatment/exposure vector (e.g. rainfall departure percent).
        X: Control matrix (2-D array).
        n_splits: Number of cross-fitting folds.
        random_state: Random seed for fold assignment and learners.
        max_iter: Boosting rounds per nuisance learner.

    Returns:
        dict with theta, its standard error, confidence interval and the
        first-stage R-squared for treatment residualisation.
    """
    y = np.asarray(y, dtype=float).ravel()
    d = np.asarray(d, dtype=float).ravel()
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n = min(y.size, d.size, X.shape[0])
    if n < 100:
        return {"error": f"Need at least 100 observations, got {n}"}
    if n_splits < 2:
        return {"error": "n_splits must be at least 2"}
    if np.nanstd(d) == 0:
        return {"error": "Treatment variable has no variation"}

    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.model_selection import KFold
    except ImportError:
        return {"error": "scikit-learn is not installed"}

    y, d, X = y[:n], d[:n], X[:n]
    folds = KFold(n_splits=n_splits, shuffle=True, random_state=random_state)
    y_residual = np.empty(n)
    d_residual = np.empty(n)
    d_hat = np.empty(n)

    for train_idx, test_idx in folds.split(X):
        learner_y = HistGradientBoostingRegressor(
            learning_rate=0.05, max_iter=max_iter, max_depth=3,
            min_samples_leaf=20, random_state=random_state,
        )
        learner_d = HistGradientBoostingRegressor(
            learning_rate=0.05, max_iter=max_iter, max_depth=3,
            min_samples_leaf=20, random_state=random_state,
        )
        learner_y.fit(X[train_idx], y[train_idx])
        learner_d.fit(X[train_idx], d[train_idx])
        y_residual[test_idx] = y[test_idx] - learner_y.predict(X[test_idx])
        d_residual[test_idx] = d[test_idx] - learner_d.predict(X[test_idx])
        d_hat[test_idx] = d[test_idx] - d_residual[test_idx]

    d_var = float(np.mean(d_residual ** 2))
    if d_var <= 0:
        return {"error": "Treatment residual variance is zero after cross-fitting"}

    theta = float(np.mean(d_residual * y_residual) / d_var)
    influence = d_residual * (y_residual - theta * d_residual) / d_var
    se = float(np.sqrt(np.mean(influence ** 2) / n))

    from scipy import stats

    if se > 0:
        z = abs(theta / se)
        p_value = float(2.0 * (1.0 - stats.norm.cdf(z)))
    else:
        p_value = 1.0
    ci_lower = theta - 1.96 * se
    ci_upper = theta + 1.96 * se

    ss_res = float(np.sum((d - d_hat) ** 2))
    ss_tot = float(np.sum((d - np.mean(d)) ** 2))
    first_stage_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

    return {
        "method": "cross_fitted_partial_linear_dml",
        "theta": round(theta, 6),
        "std_error": round(se, 6),
        "ci95_lower": round(ci_lower, 6),
        "ci95_upper": round(ci_upper, 6),
        "p_value": round(p_value, 6),
        "n_obs": int(n),
        "n_splits": int(n_splits),
        "first_stage_r2": None if first_stage_r2 is None else round(float(first_stage_r2), 4),
        "treatment_sd": round(float(np.std(d)), 4),
    }


def dml_report(conn, commodity: str, min_rows: int = 200) -> dict:
    """DML rainfall-price sensitivity estimate for one commodity.

    Panel: district-month average modal price joined with the rainfall
    departure of the district's sub-division. Controls: seasonality, lagged
    price, price volatility and region-year level shifts.
    """
    from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map

    district_map = load_district_subdivision_map()
    price_df = conn.execute(
        """
        SELECT state, district,
               EXTRACT(YEAR FROM arrival_date) AS year,
               EXTRACT(MONTH FROM arrival_date) AS month,
               AVG(modal_price) AS avg_price
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY state, district, year, month
        HAVING COUNT(*) >= 3
        ORDER BY state, district, year, month
        """,
        [commodity],
    ).fetchdf()
    if len(price_df) < min_rows:
        return {"error": f"Not enough monthly district data for {commodity}: {len(price_df)} rows"}

    price_df["sub_division"] = price_df.apply(
        lambda row: district_map.get((row["state"], row["district"])), axis=1
    )
    price_df = price_df.dropna(subset=["sub_division"])
    rainfall = conn.execute("SELECT * FROM rainfall").fetchdf()
    merged = price_df.merge(rainfall, on=["sub_division", "year", "month"], how="inner")
    merged = merged.dropna(subset=["departure_pct", "avg_price"])
    if len(merged) < min_rows:
        return {"error": f"Not enough joined rainfall-price rows for {commodity}: {len(merged)}"}

    merged = merged.sort_values(["state", "district", "year", "month"])
    merged["log_price"] = np.log(merged["avg_price"].clip(lower=1e-6))
    grouped = merged.groupby(["state", "district"])
    merged["lag_price"] = grouped["log_price"].shift(1)
    merged["price_vol"] = grouped["log_price"].transform(
        lambda s: s.rolling(3, min_periods=2).std()
    )
    merged["month_sin"] = np.sin(2 * np.pi * merged["month"] / 12.0)
    merged["month_cos"] = np.cos(2 * np.pi * merged["month"] / 12.0)
    merged = merged.dropna(subset=["lag_price", "price_vol"])

    controls = merged[["lag_price", "price_vol", "month_sin", "month_cos", "year"]].to_numpy()
    result = partial_linear_dml(
        merged["log_price"].to_numpy(),
        merged["departure_pct"].to_numpy(),
        controls,
    )
    if "error" in result:
        return result

    # theta is the per-percentage-point effect on log price.
    result["commodity"] = commodity
    result["effect_10pp_pct"] = round((np.exp(10.0 * result["theta"]) - 1.0) * 100.0, 2)
    result["effect_10pp_ci95_pct"] = [
        round((np.exp(10.0 * result["ci95_lower"]) - 1.0) * 100.0, 2),
        round((np.exp(10.0 * result["ci95_upper"]) - 1.0) * 100.0, 2),
    ]
    result["interpretation"] = (
        "Effect of a 10 percentage-point increase in rainfall departure on the "
        "monthly modal price, holding seasonality, price inertia and volatility "
        "flexibly constant (orthogonalised, cross-fitted)."
    )
    return result
