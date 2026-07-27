"""
MandiRDD — Regression Discontinuity Design engine.

Implements a local-linear RDD with a triangular kernel, bandwidth
sensitivity analysis, and placebo tests — all from scratch (no
dependency on R-only packages).

Designed so the estimator can be explained line-by-line in an
interview. The math is straightforward:
1. For each side of the cutoff, fit a weighted linear regression
   of outcome on (running_variable - cutoff), with weights from
   the triangular kernel
2. The discontinuity estimate is the difference between the two
   fitted lines at the cutoff
3. Standard errors via the HC2 sandwich estimator
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple
from scipy import stats
import logging

logger = logging.getLogger(__name__)


def triangular_kernel(x: np.ndarray, cutoff: float, bandwidth: float) -> np.ndarray:
    """
    Triangular kernel: K(u) = (1 - |u|) * 1(|u| <= 1)
    where u = (x - cutoff) / bandwidth
    
    This gives more weight to observations closer to the cutoff,
    which is the standard choice in local-linear RDD.
    """
    u = (x - cutoff) / bandwidth
    weights = np.maximum(0, 1 - np.abs(u))
    return weights


def local_linear_rdd(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    bandwidth: float,
) -> dict:
    """
    Local-linear RDD with triangular kernel.
    
    Fits separate weighted regressions on each side of the cutoff:
        Y = beta_0 + beta_1 * (X - c) + epsilon (for X >= c, right side)
        Y = gamma_0 + gamma_1 * (X - c) + epsilon (for X < c, left side)
    
    The discontinuity effect = beta_0 - gamma_0 (the difference at the cutoff).
    
    Args:
        x: Running variable (e.g., rainfall departure %)
        y: Outcome variable (e.g., avg modal price)
        cutoff: The cutoff/threshold value
        bandwidth: Bandwidth as a percentage (e.g., 20 means +/-20%)
    
    Returns:
        dict with effect, std_error, p_value, n_left, n_right, bandwidth
    """
    # Centered running variable
    x_centered = x - cutoff
    
    # Compute bandwidth in absolute units
    x_range = np.max(x) - np.min(x)
    bw_absolute = bandwidth * x_range / 100.0
    
    # Only use observations within the bandwidth
    in_bandwidth = np.abs(x_centered) <= bw_absolute
    x_bw = x_centered[in_bandwidth]
    y_bw = y[in_bandwidth]
    
    if len(x_bw) < 5:
        return {
            "effect": None,
            "std_error": None,
            "p_value": None,
            "n_left": 0,
            "n_right": 0,
            "bandwidth": bandwidth,
            "error": "Insufficient observations within bandwidth",
        }
    
    # Triangular kernel weights
    weights = triangular_kernel(x_bw, 0.0, bw_absolute)
    
    # Split into left and right of cutoff
    left_mask = x_bw < 0
    right_mask = x_bw >= 0
    
    x_left = x_bw[left_mask]
    y_left = y_bw[left_mask]
    w_left = weights[left_mask]
    
    x_right = x_bw[right_mask]
    y_right = y_bw[right_mask]
    w_right = weights[right_mask]
    
    n_left = len(x_left)
    n_right = len(x_right)
    
    if n_left < 5 or n_right < 5:
        return {
            "effect": None,
            "std_error": None,
            "p_value": None,
            "n_left": n_left,
            "n_right": n_right,
            "bandwidth": bandwidth,
            "error": "Too few observations on one side of cutoff",
        }
    
    # Fit weighted linear regression on each side
    # Left side: Y = gamma_0 + gamma_1 * X + e
    X_left_design = np.column_stack([np.ones(n_left), x_left])
    beta_left = _wls(X_left_design, y_left, w_left)
    gamma_0 = beta_left[0]
    
    # Right side: Y = beta_0 + beta_1 * X + e
    X_right_design = np.column_stack([np.ones(n_right), x_right])
    beta_right = _wls(X_right_design, y_right, w_right)
    beta_0 = beta_right[0]
    
    # Discontinuity effect = beta_0 - gamma_0
    effect = beta_0 - gamma_0
    
    # Standard error via HC2 sandwich estimator
    # Pooled regression with interaction terms for robust SE
    n = n_left + n_right
    X_pooled = np.zeros((n, 4))
    X_pooled[:n_left, 0] = 1.0  # Left intercept
    X_pooled[:n_left, 1] = x_left  # Left slope
    X_pooled[n_left:, 2] = 1.0  # Right intercept
    X_pooled[n_left:, 3] = x_right  # Right slope
    
    y_pooled = np.concatenate([y_left, y_right])
    w_pooled = np.concatenate([w_left, w_right])
    
    # Weighted regression
    beta_pooled = _wls(X_pooled, y_pooled, w_pooled)
    residuals = y_pooled - X_pooled @ beta_pooled
    
    # HC2 sandwich variance
    # S = (X'WX)^{-1} (X' diag(e^2 / (1-h)) W X) (X'WX)^{-1}
    W_sqrt = np.sqrt(w_pooled)
    X_w = X_pooled * W_sqrt[:, np.newaxis]
    y_w = y_pooled * W_sqrt
    
    try:
        XWX_inv = np.linalg.inv(X_w.T @ X_w)
    except np.linalg.LinAlgError:
        # Degenerate design (e.g. a side with no variance) -> use pseudo-inverse
        XWX_inv = np.linalg.pinv(X_w.T @ X_w)

    h = np.diag(X_pooled @ XWX_inv @ (X_pooled * w_pooled[:, np.newaxis]).T)
    h = np.clip(h, 0, 0.99)  # Avoid division by zero
    
    # HC2: e^2 / (1 - h)
    e_adj = residuals**2 / (1 - h)
    
    # Sandwich: (X'WX)^{-1} X' W diag(e_adj) W X (X'WX)^{-1}
    meat = (X_pooled * w_pooled[:, np.newaxis]).T @ np.diag(e_adj) @ (X_pooled * w_pooled[:, np.newaxis])
    varcov = XWX_inv @ meat @ XWX_inv
    
    # The effect is (beta_0 - gamma_0) = beta_pooled[2] - beta_pooled[0]
    # Variance of the difference = var(beta_0) + var(gamma_0) (they're independent)
    se_effect = np.sqrt(varcov[2, 2] + varcov[0, 0])
    
    # t-statistic and p-value
    if se_effect > 0 and effect is not None:
        t_stat = effect / se_effect
        p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=n - 4))
    else:
        t_stat = 0
        p_value = 1.0
    
    return {
        "effect": float(effect),
        "std_error": float(se_effect),
        "p_value": float(p_value),
        "t_stat": float(t_stat),
        "n_left": int(n_left),
        "n_right": int(n_right),
        "n_total": int(n),
        "bandwidth": float(bandwidth),
        "bandwidth_absolute": float(bw_absolute),
        "x_range": float(x_range),
        "gamma_0": float(gamma_0),  # Left intercept (predicted value at cutoff, left)
        "beta_0": float(beta_0),    # Right intercept (predicted value at cutoff, right)
        "error": None,
    }


def _wls(X: np.ndarray, y: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted least squares: beta = (X'WX)^{-1} X'W y."""
    W = np.diag(weights)
    XWX = X.T @ W @ X
    XWy = X.T @ W @ y
    try:
        beta = np.linalg.solve(XWX, XWy)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(XWX, XWy, rcond=None)[0]
    return beta


def bandwidth_sensitivity(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    bandwidths: list[float] = None,
) -> list[dict]:
    """
    Re-run the RDD at multiple bandwidths.
    
    The gold standard for robustness: if the effect flips sign or
    loses significance across reasonable bandwidths, that's the
    honest result — report it.
    """
    if bandwidths is None:
        bandwidths = [10, 15, 20, 25, 30]
    
    results = []
    for bw in bandwidths:
        result = local_linear_rdd(x, y, cutoff, bw)
        result["bandwidth"] = bw
        results.append(result)
    
    return results


def placebo_test(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    placebo_cutoffs: list[float] = None,
    bandwidth: float = 20,
) -> list[dict]:
    """
    Run the identical RDD at fake cutoffs where nothing should happen.
    
    A near-zero, insignificant "effect" at placebo cutoffs is evidence
    that the real cutoff's effect isn't an artifact of the estimator.
    """
    if placebo_cutoffs is None:
        # Use 25th, 50th, 75th percentile-like positions
        x_pctiles = np.percentile(x, [20, 40, 50, 60, 80])
        placebo_cutoffs = [p for p in x_pctiles if abs(p - cutoff) > 2.0]
    
    results = []
    for pc in placebo_cutoffs:
        result = local_linear_rdd(x, y, pc, bandwidth)
        result["placebo_cutoff"] = float(pc)
        results.append(result)
    
    return results


def mccrary_density_test(
    x: np.ndarray,
    cutoff: float,
    bins: int = 20,
) -> dict:
    """
    McCrary-style density test: check for a discontinuity in the
    density of the running variable at the cutoff.
    
    A jump in density suggests manipulation (e.g., districts being
    classified as "just barely deficient"), which would undermine
    the RDD's identification strategy.
    """
    bins = int(bins)
    # Bin the running variable and count frequencies
    x_min, x_max = np.min(x), np.max(x)
    bin_edges = np.linspace(x_min, x_max, bins + 1)
    bin_width = (x_max - x_min) / bins
    
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    counts, _ = np.histogram(x, bins=bin_edges)
    
    # Run RDD on the log-density
    log_density = np.log(counts + 1)  # +1 to avoid log(0)
    
    result = local_linear_rdd(bin_centers, log_density, cutoff, bandwidth=20)
    
    return {
        "density_jump": result.get("effect"),
        "density_p_value": result.get("p_value"),
        "n_bins": bins,
        "bin_width": float(bin_width),
        "bins": {
            "centers": bin_centers.tolist(),
            "counts": counts.tolist(),
            "log_density": log_density.tolist(),
        },
    }


def covariate_balance(
    x: np.ndarray,
    covariates: dict[str, np.ndarray],
    cutoff: float,
    bandwidth: float = 20,
) -> dict:
    """
    Check that pre-treatment covariates don't jump at the cutoff.
    
    If covariates show a discontinuity, the RDD may be picking up
    a spurious correlation rather than a causal effect.
    """
    results = {}
    for name, cov in covariates.items():
        result = local_linear_rdd(x, cov, cutoff, bandwidth)
        results[name] = {
            "effect": result.get("effect"),
            "p_value": result.get("p_value"),
            "std_error": result.get("std_error"),
        }
    
    return results


def run_rdd(
    conn,
    commodity: str = "Onion",
    state: Optional[str] = None,
    bandwidths: list[float] = None,
    cutoff: float = -19.0,
) -> dict:
    """
    Full RDD pipeline: join prices with rainfall, estimate discontinuity,
    run robustness checks.
    
    Args:
        conn: SQLite connection
        commodity: Commodity to analyze
        state: Optional state filter
        bandwidths: Bandwidths to test for sensitivity
        cutoff: RDD cutoff (IMD's deficient rainfall threshold = -19%)
    
    Returns:
        Dict with main RDD result + robustness checks
    """
    from mandi_rdd.storage.duckdb_store import get_monthly_avg_prices
    from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
    
    if bandwidths is None:
        bandwidths = [15, 20, 25, 30]
    
    # 1. Get monthly average prices for this commodity
    if state is None:
        price_df = conn.execute(
            """
            SELECT
                EXTRACT(YEAR FROM arrival_date)  AS year,
                EXTRACT(MONTH FROM arrival_date) AS month,
                AVG(modal_price)                 AS avg_modal_price,
                COUNT(*)                        AS n_observations
            FROM prices
            WHERE commodity = ? AND modal_price IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(*) >= 1
            ORDER BY 1, 2
            """,
            [commodity],
        ).fetchdf()
        price_df["state"] = "All-India"
        price_df["district"] = "All-India"
    else:
        price_df = get_monthly_avg_prices(conn, commodity=commodity, state=state)
    
    if len(price_df) < 1:
        return {
            "commodity": commodity,
            "effect": None,
            "std_error": None,
            "p_value": None,
            "bandwidth_sensitivity": None,
            "error": f"Insufficient price data: {len(price_df)} monthly observations",
        }
    
    # 2. Load district → sub-division mapping
    district_map = load_district_subdivision_map()
    
    # 3. Map each district to its sub-division (national path already has it)
    if state is not None:
        def _map_subdiv(r):
            return district_map.get((r["state"], r["district"]), None)
        price_df["sub_division"] = price_df.apply(_map_subdiv, axis=1)
        # 4. Drop rows with no sub-division mapping
        price_df = price_df.dropna(subset=["sub_division"])
    
    if len(price_df) < 1:
        return {
            "commodity": commodity,
            "effect": None,
            "bandwidth_sensitivity": None,
            "error": f"Insufficient district-subdivision mappings: {len(price_df)} rows",
        }
    
    # 5. Load rainfall (daily granularity, real departure_pct)
    rainfall_df = conn.execute(
        """
        SELECT sub_division, month,
               rainfall_mm, normal_mm, departure_pct
        FROM rainfall
        """
    ).fetchdf()

    # Normalise departure scale defensively (ratio -> percentage) if needed.
    _maxabs = float(rainfall_df["departure_pct"].abs().max())
    if _maxabs < 1.0:
        rainfall_df["departure_pct"] = rainfall_df["departure_pct"] * 100.0

    if state is None:
        # Each national price (year-month) row is joined to EVERY daily
        # rainfall departure observation for that month -> a panel with real
        # spread in the running variable around the 0% deficiency cutoff.
        price_df = price_df.merge(
            rainfall_df[["month", "sub_division", "departure_pct",
                         "rainfall_mm", "normal_mm"]],
            on="month", how="left",
        )
    
    if len(rainfall_df) < 5:
        return {
            "commodity": commodity,
            "effect": None,
            "bandwidth_sensitivity": None,
            "error": f"Insufficient rainfall data: {len(rainfall_df)} records",
        }
    
    # Merge on sub_division, month (climatological rainfall signal)
    if state is None:
        # National path already carries sub_division + departure_pct from step 5
        merged = price_df
    else:
        merged = price_df.merge(
            rainfall_df,
            on=["sub_division", "month"],
            how="inner",
        )
    
    if len(merged) < 5:
        return {
            "commodity": commodity,
            "effect": None,
            "bandwidth_sensitivity": None,
            "error": f"Insufficient matched data: {len(merged)} observations",
        }
    
    # 6. Drop NaN departure values
    merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])
    
    if len(merged) < 5:
        return {
            "commodity": commodity,
            "effect": None,
            "bandwidth_sensitivity": None,
            "error": "Too few non-null observations after merge",
        }
    
    x = merged["departure_pct"].values
    y = merged["avg_modal_price"].values
    
    # 7. Run main RDD
    main_result = local_linear_rdd(x, y, cutoff, bandwidth=20)
    
    # 8. Bandwidth sensitivity
    sensitivity = bandwidth_sensitivity(x, y, cutoff, bandwidths)
    
    # 9. Placebo tests
    placebos = placebo_test(x, y, cutoff)
    
    # 10. McCrary density test
    density = mccrary_density_test(x, cutoff)
    
    # 11. Covariate balance (check if number of observations jumps at cutoff)
    covariates = {"n_observations": merged["n_observations"].values}
    
    # Also check if avg_price in the prior period jumps
    if "avg_modal_price" in merged.columns:
        # Use log of observations as a pseudo-covariate
        covariates["log_n_obs"] = np.log(merged["n_observations"].values + 1)
    
    balance = covariate_balance(x, covariates, cutoff)
    
    # 12. Compile full result
    effect = main_result.get("effect")
    p_value = main_result.get("p_value")
    
    result = {
        "commodity": commodity,
        "state": state or "All",
        "effect": effect,
        "std_error": main_result.get("std_error"),
        "p_value": p_value,
        "t_stat": main_result.get("t_stat"),
        "n_left": main_result.get("n_left"),
        "n_right": main_result.get("n_right"),
        "n_total": main_result.get("n_total"),
        "bandwidth": main_result.get("bandwidth"),
        "bandwidth_absolute": main_result.get("bandwidth_absolute"),
        "gamma_0": main_result.get("gamma_0"),
        "beta_0": main_result.get("beta_0"),
        "x_range": main_result.get("x_range"),
        "n_observations": len(merged),
        "n_districts": merged["district"].nunique(),
        "n_months": merged["year"].nunique() * 12,
        "bandwidth_sensitivity": sensitivity,
        "placebo_tests": placebos,
        "density_test": density,
        "covariate_balance": balance,
        "data_sample": merged.head(100).to_dict("records"),
        "error": main_result.get("error"),
    }
    
    # Interpret the result
    if effect is not None and p_value is not None:
        if p_value < 0.05:
            result["interpretation"] = (
                f"Statistically significant discontinuity at cutoff (p={p_value:.4f}). "
                f"Price changes by ₹{effect:.2f} ({(effect / (result.get('gamma_0', 1) or 1)) * 100:.1f}%) "
                f"at the -19% rainfall deficiency threshold."
            )
        elif p_value < 0.1:
            result["interpretation"] = (
                f"Marginally significant discontinuity (p={p_value:.4f}). "
                f"Effect of ₹{effect:.2f} at cutoff — suggestive but not definitive."
            )
        else:
            result["interpretation"] = (
                f"No statistically significant discontinuity detected (p={p_value:.4f}). "
                f"Estimated effect: ₹{effect:.2f} at cutoff. "
                "Rainfall deficiency alone may not drive price jumps for this commodity."
            )
    
    return result


def rdd_plot_data(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    bandwidth: float = 20,
    n_bins: int = 15,
) -> dict:
    """
    Generate binned scatter plot data for the RDD visualization.
    
    Returns:
        dict with bin_centers, bin_means, fitted_left, fitted_right,
        and the raw data for scatter points
    """
    x_centered = x - cutoff
    x_range = np.max(x) - np.min(x)
    bw_absolute = bandwidth * x_range / 100.0
    
    # Binned averages for the scatter plot
    mask = np.abs(x_centered) <= bw_absolute * 1.5  # Slightly wider for context
    x_bw = x[mask]
    y_bw = y[mask]
    
    # Create bins
    bin_edges = np.linspace(np.min(x_bw), np.max(x_bw), n_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_means = np.array([np.mean(y_bw[(x_bw >= bin_edges[i]) & (x_bw < bin_edges[i + 1])]) for i in range(n_bins)])
    bin_stds = np.array([np.std(y_bw[(x_bw >= bin_edges[i]) & (x_bw < bin_edges[i + 1])]) for i in range(n_bins)])
    bin_counts = np.array([np.sum((x_bw >= bin_edges[i]) & (x_bw < bin_edges[i + 1])) for i in range(n_bins)])
    
    # Fitted line from the local-linear RDD
    # Left side: predicted values for x < cutoff
    left_x = np.linspace(np.min(x_bw), cutoff, 50)
    right_x = np.linspace(cutoff, np.max(x_bw), 50)
    
    _, left_intercept, _ = _fit_polynomial(x_bw, y_bw, cutoff, "left")
    _, right_intercept, _ = _fit_polynomial(x_bw, y_bw, cutoff, "right")
    
    left_fit_y = left_intercept + np.zeros_like(left_x)
    right_fit_y = right_intercept + np.zeros_like(right_x)
    
    # Get slopes for the full fitted lines
    left_slope = _fit_slope(x_bw[(x_bw < cutoff)], y_bw[(x_bw < cutoff)])
    right_slope = _fit_slope(x_bw[(x_bw >= cutoff)], y_bw[(x_bw >= cutoff)])
    
    left_y = left_intercept + left_slope * (left_x - cutoff)
    right_y = right_intercept + right_slope * (right_x - cutoff)
    
    return {
        "raw_x": x.tolist(),
        "raw_y": y.tolist(),
        "bin_centers": bin_centers.tolist(),
        "bin_means": bin_means.tolist(),
        "bin_stds": [float(s) if not np.isnan(s) else 0 for s in bin_stds],
        "bin_counts": bin_counts.tolist(),
        "left_x": left_x.tolist(),
        "left_y": left_y.tolist(),
        "right_x": right_x.tolist(),
        "right_y": right_y.tolist(),
        "cutoff": float(cutoff),
        "bandwidth": float(bandwidth),
        "bandwidth_absolute": float(bw_absolute),
    }


def _fit_polynomial(x, y, cutoff, side):
    """Fit a local polynomial and return the intercept at cutoff."""
    if side == "left":
        mask = x < cutoff
    else:
        mask = x >= cutoff
    
    x_s = x[mask] - cutoff
    y_s = y[mask]
    
    if len(x_s) < 3:
        return 0, 0, 0
    
    X = np.column_stack([np.ones(len(x_s)), x_s])
    try:
        beta = np.linalg.lstsq(X, y_s, rcond=None)[0]
        return beta[0], beta[0], beta[1]
    except Exception:
        return 0, np.mean(y_s), 0


def _fit_slope(x, y):
    """Fit a simple slope for visualization."""
    if len(x) < 3:
        return 0
    x_c = x - np.mean(x)
    y_c = y - np.mean(y)
    slope = np.sum(x_c * y_c) / (np.sum(x_c ** 2) + 1e-10)
    return slope
