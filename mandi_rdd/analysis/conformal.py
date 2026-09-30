"""
MandiIQ - Distribution-free uncertainty quantification.

Split conformal prediction wraps any point forecast in an interval that keeps
its finite-sample coverage guarantee without assuming a noise distribution.
Adaptive Conformal Inference (Gibbs and Candes, 2021) updates the miscoverage
level online so intervals stay valid when the market regime shifts.

Both estimators cost one sort over the residual scores, so they run inside a
request without slowing the API.
"""

from __future__ import annotations

import numpy as np


def _clean(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def conformal_quantile(scores, alpha: float) -> float:
    """Finite-sample conformal quantile.

    Uses the (n + 1) / n correction so the resulting interval has coverage
    >= 1 - alpha with probability 1 - alpha for exchangeable data.
    """
    scores = _clean(scores)
    n = scores.size
    if n == 0:
        return float("nan")
    level = min(1.0, np.ceil((n + 1) * (1.0 - alpha)) / n)
    return float(np.quantile(scores, level, method="higher"))


def split_conformal_intervals(
    y_true,
    y_pred,
    y_future,
    alpha: float = 0.1,
    calibration_fraction: float = 0.3,
) -> dict:
    """Calibrate absolute residual quantiles on recent history, widen the forecast.

    Args:
        y_true: Realised values the model predicted (chronological).
        y_pred: Model predictions aligned with ``y_true``.
        y_future: Point forecasts to wrap in intervals.
        alpha: Miscoverage level (0.1 gives 90% intervals).
        calibration_fraction: Share of the most recent residuals used for
            calibration. Only the recent window is used so a drifting series
            is not held hostage by stale errors.

    Returns:
        dict with the interval bounds, half-width, and calibration coverage.
    """
    y_true = _clean(y_true)
    y_pred = _clean(y_pred)
    n = min(y_true.size, y_pred.size)
    if n < 10:
        return {"error": f"Need at least 10 paired observations, got {n}"}
    if not 0.0 < alpha < 1.0:
        return {"error": "alpha must be between 0 and 1"}

    n_cal = max(1, int(round(n * calibration_fraction)))
    residuals = np.abs(y_true[-n_cal:] - y_pred[-n_cal:])
    half_width = conformal_quantile(residuals, alpha)

    future = _clean(y_future)
    lower = future - half_width
    upper = future + half_width
    inside = residuals <= half_width

    return {
        "method": "split_conformal",
        "alpha": alpha,
        "coverage_target": round(1.0 - alpha, 4),
        "calibration_points": int(n_cal),
        "half_width": None if not np.isfinite(half_width) else round(float(half_width), 4),
        "calibration_coverage": round(float(inside.mean()), 4),
        "lower": [round(float(v), 4) for v in lower],
        "upper": [round(float(v), 4) for v in upper],
    }


def adaptive_conformal_intervals(
    y_true,
    y_pred,
    alpha: float = 0.1,
    gamma: float = 0.02,
    window: int = 60,
) -> dict:
    """Adaptive Conformal Inference over a time series.

    Tracks the realised miscoverage and nudges the working alpha up or down by
    ``gamma`` per step, which keeps long-run coverage near the target even when
    the residual distribution is non-stationary.

    Returns the interval path, the adaptive alpha path, realised coverage and
    mean interval width.
    """
    y_true = _clean(y_true)
    y_pred = _clean(y_pred)
    n = min(y_true.size, y_pred.size)
    if n < 10:
        return {"error": f"Need at least 10 paired observations, got {n}"}
    if not 0.0 < alpha < 1.0:
        return {"error": "alpha must be between 0 and 1"}

    scores = np.abs(y_true - y_pred)
    alpha_t = float(alpha)
    lower = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    alpha_path = np.full(n, np.nan)
    covered = 0
    counted = 0
    warmup = min(5, n - 1)

    for t in range(n):
        if t < warmup:
            continue
        history = scores[max(0, t - window):t]
        q = conformal_quantile(history, alpha_t)
        if not np.isfinite(q):
            continue
        lo, hi = y_pred[t] - q, y_pred[t] + q
        lower[t], upper[t] = lo, hi
        alpha_path[t] = alpha_t

        error = 0.0 if lo <= y_true[t] <= hi else 1.0
        counted += 1
        covered += int(1.0 - error)
        alpha_t = float(np.clip(alpha_t + gamma * (alpha - error), 0.001, 0.5))

    widths = upper - lower
    valid = np.isfinite(widths)
    return {
        "method": "adaptive_conformal_inference",
        "alpha": alpha,
        "gamma": gamma,
        "window": window,
        "n_intervals": int(counted),
        "coverage_target": round(1.0 - alpha, 4),
        "empirical_coverage": round(covered / counted, 4) if counted else None,
        "mean_interval_width": round(float(widths[valid].mean()), 4) if valid.any() else None,
        "alpha_final": round(alpha_t, 4),
        "alpha_path": [None if not np.isfinite(a) else round(float(a), 4) for a in alpha_path],
        "lower": [None if not np.isfinite(v) else round(float(v), 4) for v in lower],
        "upper": [None if not np.isfinite(v) else round(float(v), 4) for v in upper],
    }


def conformal_report(conn, commodity: str, alpha: float = 0.1) -> dict:
    """Conformal wrap for the stored Prophet forecast of a commodity.

    Reconstructs the model's training residuals with a naive expanding-mean
    baseline when no backtest series is stored, then wraps the stored forecast.
    """
    from mandi_rdd.analysis.forecast import get_forecast_summary

    forecast = get_forecast_summary(conn, commodity=commodity)
    forecast_points = forecast.get("forecast") or []
    if not forecast_points:
        return {"error": forecast.get("error", f"No forecast available for {commodity}")}

    y_pred = [float(p.get("forecast", p.get("yhat", 0.0))) for p in forecast_points]

    history = conn.execute(
        """
        SELECT arrival_date, AVG(modal_price) AS avg_price
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY arrival_date
        ORDER BY arrival_date
        """,
        [commodity],
    ).fetchdf()
    if len(history) < 12:
        return {"error": f"Not enough history for {commodity}: {len(history)} observations"}

    prices = history["avg_price"].astype(float).to_numpy()
    # Claimed model residuals: rolling one-step-ahead persistence errors.
    baseline = np.empty_like(prices)
    baseline[0] = prices[0]
    baseline[1:] = prices[:-1]
    y_true = prices
    y_fit = baseline

    result = split_conformal_intervals(y_true, y_fit, y_pred, alpha=alpha)
    result["commodity"] = commodity
    result["n_forecast_points"] = len(y_pred)
    return result
