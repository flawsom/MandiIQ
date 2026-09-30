"""
MandiIQ - State-space nowcasting.

Mandi reporting arrives with a lag: at mid-month the latest price is not the
month's price, and naive month-to-date means are biased by reporting gaps.
This module fits a local linear trend state-space model (Kalman filter with
RTS smoother, parameters by maximum likelihood) and projects the current
month's level to month-end with a calibrated uncertainty band.

The model handles irregular reporting naturally: missing days simply skip the
measurement update and widen the predictive variance.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np


def build_daily_grid(dates, prices) -> dict | None:
    """Collapse (date, price) pairs into a regular calendar grid with NaNs."""
    buckets: dict[date, list[float]] = {}
    for d, v in zip(dates, prices):
        if v is None:
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(value):
            continue
        key = d.date() if hasattr(d, "date") else d
        buckets.setdefault(key, []).append(value)
    if not buckets:
        return None

    start, end = min(buckets), max(buckets)
    n_days = (end - start).days + 1
    grid = np.full(n_days, np.nan)
    for key, values in buckets.items():
        grid[(key - start).days] = float(np.mean(values))
    return {"start": start, "end": end, "values": grid}


def _kalman_filter(y: np.ndarray, sigma_obs: float, sigma_level: float, sigma_slope: float) -> dict | None:
    """Local linear trend filter; NaN observations are treated as missing."""
    first = next((i for i, v in enumerate(y) if np.isfinite(v)), None)
    if first is None:
        return None

    n = y.size
    transition = np.array([[1.0, 1.0], [0.0, 1.0]])
    state_cov = np.diag([sigma_level ** 2, sigma_slope ** 2])
    obs = np.array([[1.0, 0.0]])
    obs_var = sigma_obs ** 2

    filtered_mean = np.zeros((n, 2))
    filtered_cov = np.zeros((n, 2, 2))
    predicted_mean = np.zeros((n, 2))
    predicted_cov = np.zeros((n, 2, 2))
    loglik = 0.0

    mean = np.array([y[first], 0.0])
    cov = np.eye(2) * max(sigma_level ** 2, sigma_slope ** 2, 1e-8)

    for t in range(n):
        predicted_mean[t], predicted_cov[t] = mean, cov
        if np.isfinite(y[t]):
            innovation = y[t] - (obs @ mean)[0]
            innovation_var = (obs @ cov @ obs.T)[0, 0] + obs_var
            gain = (cov @ obs.T).ravel() / innovation_var
            mean = mean + gain * innovation
            cov = cov - np.outer(gain, obs @ cov)
            loglik += -0.5 * (np.log(2.0 * np.pi) + np.log(innovation_var) + innovation ** 2 / innovation_var)
        filtered_mean[t], filtered_cov[t] = mean, cov
        mean = transition @ mean
        cov = transition @ cov @ transition.T + state_cov

    return {
        "filtered_mean": filtered_mean,
        "filtered_cov": filtered_cov,
        "predicted_mean": predicted_mean,
        "predicted_cov": predicted_cov,
        "loglik": float(loglik),
        "first_index": first,
    }


def _rts_smoother(filt: dict, sigma_level: float, sigma_slope: float) -> dict:
    """Rauch-Tung-Striebel smoother on the stored filter output."""
    transition = np.array([[1.0, 1.0], [0.0, 1.0]])
    filtered_mean = filt["filtered_mean"]
    filtered_cov = filt["filtered_cov"]
    predicted_mean = filt["predicted_mean"]
    predicted_cov = filt["predicted_cov"]
    n = filtered_mean.shape[0]

    smoothed_mean = filtered_mean.copy()
    smoothed_cov = filtered_cov.copy()
    for t in range(n - 2, -1, -1):
        next_cov = predicted_cov[t + 1]
        gain = filtered_cov[t] @ transition.T @ np.linalg.pinv(next_cov)
        smoothed_mean[t] = filtered_mean[t] + gain @ (smoothed_mean[t + 1] - predicted_mean[t + 1])
        smoothed_cov[t] = filtered_cov[t] + gain @ (smoothed_cov[t + 1] - next_cov) @ gain.T

    return {"smoothed_mean": smoothed_mean, "smoothed_cov": smoothed_cov}


def kalman_local_linear_trend(y, fit_params: bool = True) -> dict:
    """Fit a local linear trend model and return smoothed states.

    Returns levels, slopes, innovation scale parameters and log-likelihood.
    """
    y = np.asarray(y, dtype=float).ravel()
    valid = y[np.isfinite(y)]
    if valid.size < 30:
        return {"error": f"Need at least 30 observations, got {valid.size}"}
    scale = float(np.std(valid))
    if scale <= 0:
        return {"error": "Series has no variation"}

    if fit_params:
        from scipy.optimize import minimize

        def negative_loglik(params):
            sigma_obs, sigma_level, sigma_slope = np.exp(params)
            result = _kalman_filter(y, sigma_obs, sigma_level, sigma_slope)
            return 1e12 if result is None else -result["loglik"]

        start = np.log([scale * 0.5, scale * 0.05, scale * 0.005])
        optimum = minimize(
            negative_loglik,
            start,
            method="Nelder-Mead",
            options={"maxiter": 300, "xatol": 1e-4, "fatol": 1e-4},
        )
        sigma_obs, sigma_level, sigma_slope = np.exp(optimum.x)
    else:
        sigma_obs, sigma_level, sigma_slope = scale * 0.5, scale * 0.05, scale * 0.005

    filt = _kalman_filter(y, float(sigma_obs), float(sigma_level), float(sigma_slope))
    if filt is None:
        return {"error": "Filter could not be initialised"}
    smoothed = _rts_smoother(filt, float(sigma_level), float(sigma_slope))

    valid_mask = np.isfinite(y)
    return {
        "sigma_obs": round(float(sigma_obs), 6),
        "sigma_level": round(float(sigma_level), 6),
        "sigma_slope": round(float(sigma_slope), 6),
        "level": smoothed["smoothed_mean"][:, 0],
        "slope": smoothed["smoothed_mean"][:, 1],
        "level_variance": smoothed["smoothed_cov"][:, 0, 0],
        "final_cov": smoothed["smoothed_cov"][-1].tolist(),
        "loglik": round(float(filt["loglik"]), 2),
        "n_obs": int(valid_mask.sum()),
        "n_days": int(y.size),
    }


def nowcast_month_end(dates, prices, confidence: float = 0.90) -> dict:
    """Project the current reporting month's price level to month-end.

    Args:
        dates: Observation dates (``date`` or pandas Timestamp).
        prices: Modal prices aligned with ``dates``.
        confidence: Two-sided confidence level for the band.

    Returns:
        dict with the nowcast, its interval band, and reporting completeness.
    """
    grid = build_daily_grid(dates, prices)
    if grid is None:
        return {"error": "No usable daily observations"}

    fitted = kalman_local_linear_trend(grid["values"])
    if "error" in fitted:
        return fitted

    end = grid["end"]
    month_end = (end.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    horizon = (month_end - end).days
    last = fitted["level"].size - 1

    level = float(fitted["level"][last])
    slope = float(fitted["slope"][last])
    nowcast = level + slope * horizon
    # Level h steps ahead: propagate the smoothed state covariance (P00 + 2h P01
    # + h^2 P11) and add the slope random-walk contribution (sigma^2 h^3 / 3).
    final_cov = np.asarray(fitted["final_cov"], dtype=float)
    variance = (
        final_cov[0, 0]
        + 2.0 * horizon * final_cov[0, 1]
        + horizon ** 2 * final_cov[1, 1]
        + (horizon ** 3) * max(float(fitted["sigma_slope"]) ** 2, 0.0) / 3.0
    )

    from scipy import stats

    z = float(stats.norm.ppf(0.5 + confidence / 2.0))
    half_width = z * float(np.sqrt(max(variance, 0.0)))

    month_days = month_end.day
    observed_in_month = sum(
        1 for d, v in zip(dates, prices)
        if v is not None and (d.date() if hasattr(d, "date") else d).month == end.month
        and (d.date() if hasattr(d, "date") else d).year == end.year
    )
    completion = observed_in_month / month_days

    return {
        "reporting_month": f"{end.year:04d}-{end.month:02d}",
        "last_observation": end.isoformat(),
        "month_end": month_end.isoformat(),
        "days_remaining": int(horizon),
        "observed_days_in_month": int(observed_in_month),
        "days_in_month": int(month_days),
        "completion_pct": round(100.0 * completion, 1),
        "nowcast_price": round(nowcast, 2),
        "smoothed_level": round(level, 2),
        "trend_per_day": round(slope, 4),
        "ci_lower": round(nowcast - half_width, 2),
        "ci_upper": round(nowcast + half_width, 2),
        "confidence": confidence,
        "sigma_obs": fitted["sigma_obs"],
        "sigma_level": fitted["sigma_level"],
        "sigma_slope": fitted["sigma_slope"],
        "n_days_used": grid["values"].size,
    }


def nowcast_report(conn, commodity: str, lookback_days: int = 210) -> dict:
    """Nowcast the current month for a commodity from the daily price panel."""
    daily = conn.execute(
        """
        SELECT arrival_date, AVG(modal_price) AS avg_price
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY arrival_date
        ORDER BY arrival_date
        """,
        [commodity],
    ).fetchdf()
    if len(daily) < 30:
        return {"error": f"Not enough daily history for {commodity}: {len(daily)} days"}

    daily = daily.tail(lookback_days)
    result = nowcast_month_end(
        list(daily["arrival_date"]),
        daily["avg_price"].astype(float).tolist(),
    )
    if "error" not in result:
        result["commodity"] = commodity
    return result
