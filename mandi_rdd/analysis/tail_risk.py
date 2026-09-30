"""
MandiIQ - Extreme value theory and tail risk.

Agricultural prices are notoriously fat-tailed: a single monsoon failure can
move a commodity 50%+ while most months move 1-2%. This module complements the
volatility-blind classifier with:

1. Historical Value-at-Risk and Expected Shortfall (CVaR).
2. Peaks-over-threshold Generalised Pareto tail fits (EVT) for out-of-sample
   tail quantiles beyond the observed sample.
3. Maximum drawdown and annualised volatility for procurement risk sizing.
"""

from __future__ import annotations

import numpy as np


def _clean(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def historical_var_cvar(returns, alpha: float = 0.95) -> dict:
    """Historical VaR/CVaR for a return series (losses are positive numbers)."""
    returns = _clean(returns)
    if returns.size < 30:
        return {"error": "Need at least 30 returns"}
    losses = -returns
    var = float(np.quantile(losses, alpha))
    tail = losses[losses >= var]
    return {
        "method": "historical",
        "alpha": alpha,
        "var": round(var, 4),
        "cvar": round(float(tail.mean()), 4),
        "n_returns": int(returns.size),
    }


def fit_gpd_tail(losses, threshold_quantile: float = 0.9) -> dict:
    """Peaks-over-threshold GPD fit on the losses above a high quantile."""
    losses = _clean(losses)
    if losses.size < 60:
        return {"error": "Need at least 60 returns for an EVT fit"}
    from scipy import stats

    threshold = float(np.quantile(losses, threshold_quantile))
    exceedances = losses[losses > threshold] - threshold
    if exceedances.size < 25:
        return {"error": f"Only {exceedances.size} exceedances; need >= 25"}

    shape, _loc, scale = stats.genpareto.fit(exceedances, floc=0.0)
    return {
        "threshold": round(threshold, 4),
        "threshold_quantile": threshold_quantile,
        "n_exceedances": int(exceedances.size),
        "shape_xi": round(float(shape), 4),
        "scale_sigma": round(float(scale), 4),
        "mean_excess": round(float(exceedances.mean()), 4),
    }


def evt_tail_risk(losses, confidence: float = 0.99, threshold_quantile: float = 0.9) -> dict:
    """EVT (GPD) VaR and Expected Shortfall at the given confidence level."""
    losses = _clean(losses)
    fit = fit_gpd_tail(losses, threshold_quantile=threshold_quantile)
    if "error" in fit:
        return fit

    threshold = fit["threshold"]
    xi = fit["shape_xi"]
    sigma = fit["scale_sigma"]
    if not 0.5 < confidence < 1.0:
        return {"error": "confidence must be between 0.5 and 1.0"}
    n = losses.size
    alpha_u = fit["n_exceedances"] / n
    tail_probability = 1.0 - confidence  # P(loss > VaR)
    if tail_probability >= alpha_u:
        return {"error": "confidence too low relative to the fitted threshold"}

    if xi >= 1.0:
        return {"error": "Estimated tail index >= 1; expected shortfall undefined"}

    ratio = tail_probability / alpha_u
    if abs(xi) < 1e-6:
        var = threshold + sigma * np.log(alpha_u / tail_probability)
    else:
        var = threshold + (sigma / xi) * (ratio ** (-xi) - 1.0)
    es = (var + sigma - xi * threshold) / (1.0 - xi)

    return {
        "method": "evt_gpd",
        "confidence": confidence,
        "tail_probability": tail_probability,
        "var": round(float(var), 4),
        "expected_shortfall": round(float(es), 4),
        "shape_xi": round(float(xi), 4),
        "scale_sigma": round(float(sigma), 4),
        "threshold": threshold,
        "n_returns": n,
        "n_exceedances": fit["n_exceedances"],
        "tail_note": (
            "positive shape_xi means a heavy tail (Fréchet domain): losses can "
            "exceed the historical sample"
        ),
    }


def max_drawdown(prices) -> dict:
    """Largest peak-to-trough decline of a price series."""
    prices = _clean(prices)
    if prices.size < 10:
        return {"error": "Need at least 10 prices"}
    running_max = np.maximum.accumulate(prices)
    drawdowns = prices / running_max - 1.0
    trough = int(np.argmin(drawdowns))
    peak = int(np.argmax(prices[: trough + 1])) if trough > 0 else 0
    return {
        "max_drawdown_pct": round(float(drawdowns[trough] * 100.0), 2),
        "peak_index": peak,
        "trough_index": trough,
        "recovery_index": None,
        "n_prices": int(prices.size),
    }


def monthly_rise_frequency(returns, monthly_threshold: float = 0.10) -> dict:
    """Share of observed months with a rise above the threshold."""
    returns = _clean(returns)
    if returns.size < 12:
        return {"error": "Need at least 12 monthly returns"}
    rises = returns > monthly_threshold
    return {
        "threshold_pct": round(monthly_threshold * 100.0, 2),
        "n_months": int(returns.size),
        "n_rises": int(rises.sum()),
        "frequency": round(float(rises.mean()), 4),
    }


def tail_risk_report(conn, commodity: str, lookback_days: int = 730) -> dict:
    """Tail-risk dashboard for one commodity, built from the daily price panel."""
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
    if len(daily) < 60:
        return {"error": f"Not enough daily history for {commodity}: {len(daily)} days"}

    daily = daily.tail(lookback_days)
    prices = daily["avg_price"].astype(float).to_numpy()
    returns = np.diff(prices) / prices[:-1]
    returns = returns[np.isfinite(returns)]

    if returns.size < 60:
        return {"error": f"Not enough returns for {commodity}: {returns.size}"}

    monthly = daily.copy()
    monthly["month"] = monthly["arrival_date"].astype(str).str.slice(0, 7)
    month_last = monthly.groupby("month")["avg_price"].last().astype(float).to_numpy()
    monthly_returns = np.diff(month_last) / month_last[:-1]

    return {
        "commodity": commodity,
        "lookback_days": lookback_days,
        "n_prices": int(prices.size),
        "var_95": historical_var_cvar(returns, 0.95),
        "var_99": historical_var_cvar(returns, 0.99),
        "evt_99": evt_tail_risk(-(returns), confidence=0.99),
        "max_drawdown": max_drawdown(prices),
        "annualised_volatility_pct": round(float(np.std(returns) * np.sqrt(252.0) * 100.0), 2),
        "monthly_rise": monthly_rise_frequency(monthly_returns, 0.10),
    }
