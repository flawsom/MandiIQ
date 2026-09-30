"""
MandiIQ - Distribution drift and data-quality monitoring.

A causal result is only as good as its inputs. This module watches the two
failure modes that quietly break agricultural price analytics:

1. Distribution drift - prices or rainfall move to a regime the models were
   not trained on. Detected with Population Stability Index (PSI), a
   Kolmogorov-Smirnov two-sample test, the Page-Hinkley sequential detector
   and an EWMA control chart.
2. Data quality drift - gaps, stale quotes and outlier days in the warehouse.
   Summarised as a 0-100 quality score with its component breakdown.
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np

logger = logging.getLogger(__name__)


def _clean(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    return arr[np.isfinite(arr)]


def population_stability_index(expected, actual, bins: int = 10) -> dict:
    """PSI between a baseline and a recent sample.

    Convention: < 0.10 stable, 0.10-0.25 moderate shift, > 0.25 major shift.
    """
    expected = _clean(expected)
    actual = _clean(actual)
    if expected.size < 30 or actual.size < 30:
        return {"error": "PSI needs at least 30 points per sample"}

    edges = np.unique(np.quantile(expected, np.linspace(0.0, 1.0, bins + 1)))
    if edges.size < 3:
        return {"error": "Baseline sample has no variation"}

    expected_share = np.histogram(expected, bins=edges)[0] / expected.size
    actual_share = np.histogram(actual, bins=edges)[0] / actual.size
    eps = 1e-6
    psi = float(np.sum((actual_share - expected_share) * np.log((actual_share + eps) / (expected_share + eps))))

    if psi < 0.10:
        verdict = "stable"
    elif psi < 0.25:
        verdict = "moderate_shift"
    else:
        verdict = "major_shift"
    return {
        "psi": round(psi, 4),
        "verdict": verdict,
        "n_expected": int(expected.size),
        "n_actual": int(actual.size),
    }


def ks_drift(expected, actual) -> dict:
    """Two-sample Kolmogorov-Smirnov test between baseline and recent data."""
    expected = _clean(expected)
    actual = _clean(actual)
    if expected.size < 20 or actual.size < 20:
        return {"error": "KS test needs at least 20 points per sample"}
    from scipy import stats

    statistic, p_value = stats.ks_2samp(expected, actual)
    return {
        "statistic": round(float(statistic), 4),
        "p_value": round(float(p_value), 4),
        "drift_detected": bool(p_value < 0.05),
        "n_expected": int(expected.size),
        "n_actual": int(actual.size),
    }


def page_hinkley(series, delta: float = 0.005, threshold: float = 25.0) -> dict:
    """Page-Hinkley sequential change detector on a standardised series.

    Returns the indices (and directions) where the cumulative deviation from
    the running mean exceeds ``threshold``.
    """
    x = _clean(series)
    if x.size < 30:
        return {"error": "Page-Hinkley needs at least 30 points"}
    sd = float(np.std(x))
    if sd == 0:
        return {"error": "Series has no variation"}
    z = (x - float(np.mean(x))) / sd

    alarms: list[dict] = []
    for direction, signal in (("up", z), ("down", -z)):
        cumulative = 0.0
        minimum = 0.0
        for t, value in enumerate(signal):
            cumulative += value - delta
            minimum = min(minimum, cumulative)
            if cumulative - minimum > threshold:
                alarms.append({"index": int(t), "direction": direction})
                cumulative = 0.0
                minimum = 0.0

    alarms.sort(key=lambda a: a["index"])
    return {
        "n_alarms": len(alarms),
        "alarms": alarms[:50],
        "delta": delta,
        "threshold": threshold,
    }


def ewma_control_chart(series, lam: float = 0.2, control_limit: float = 3.0) -> dict:
    """EWMA control chart (Montgomery) for gradual mean shifts."""
    x = _clean(series)
    if x.size < 20:
        return {"error": "EWMA needs at least 20 points"}
    sd = float(np.std(x))
    if sd == 0:
        return {"error": "Series has no variation"}
    z = (x - float(np.mean(x))) / sd

    ewma = np.empty_like(z)
    ewma[0] = z[0]
    for t in range(1, z.size):
        ewma[t] = lam * z[t] + (1.0 - lam) * ewma[t - 1]
    sigma = float(np.sqrt(lam / (2.0 - lam)))
    limit = control_limit * sigma
    out = np.where(np.abs(ewma) > limit)[0]

    return {
        "lambda": lam,
        "control_limit": control_limit,
        "sigma_ewma": round(sigma, 4),
        "n_out_of_control": int(out.size),
        "out_of_control_indices": out.tolist()[:50],
        "ewma_final": round(float(ewma[-1]), 4),
    }


def data_quality_score(dates, values, reference_date: date | None = None, expected_cadence_days: int = 1) -> dict:
    """Score a dated price series on completeness, freshness, staleness and outliers."""
    pairs = [(d, v) for d, v in zip(dates, values) if v is not None and np.isfinite(float(v))]
    if not pairs:
        return {"error": "No usable observations"}

    stamps = sorted({d for d, _ in pairs})
    prices = np.array([float(v) for _, v in pairs])
    span_days = max((stamps[-1] - stamps[0]).days, 1)
    expected_points = span_days / max(expected_cadence_days, 1)
    completeness = min(1.0, len(pairs) / expected_points) if expected_points else 0.0

    reference = reference_date or date.today()
    staleness_days = max((reference - stamps[-1]).days, 0)
    freshness = max(0.0, 1.0 - staleness_days / 30.0)

    repeats = float(np.mean(prices[1:] == prices[:-1])) if prices.size > 1 else 0.0
    median = float(np.median(prices))
    mad = float(np.median(np.abs(prices - median)))
    if mad > 0:
        outlier_share = float(np.mean(np.abs(prices - median) / (1.4826 * mad) > 3.5))
    else:
        outlier_share = 0.0

    score = 100.0 * (0.50 * completeness + 0.20 * freshness + 0.20 * (1.0 - repeats) + 0.10 * (1.0 - outlier_share))
    return {
        "score": round(float(np.clip(score, 0.0, 100.0)), 1),
        "n_observations": len(pairs),
        "span_days": span_days,
        "completeness": round(completeness, 4),
        "staleness_days": staleness_days,
        "freshness": round(freshness, 4),
        "repeat_price_share": round(repeats, 4),
        "outlier_share": round(outlier_share, 4),
    }


def monthly_price_series(conn, commodity: str, months: int = 36) -> list[dict]:
    """Monthly average modal prices, most recent ``months`` first-class rows."""
    df = conn.execute(
        """
        SELECT date_trunc('month', arrival_date) AS month,
               AVG(modal_price) AS avg_price,
               COUNT(*) AS n_obs
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY 1
        ORDER BY 1
        """,
        [commodity],
    ).fetchdf()
    if df.empty:
        return []
    df = df.tail(months)
    return [
        {
            "month": str(row["month"])[:10],
            "avg_price": round(float(row["avg_price"]), 2),
            "n_obs": int(row["n_obs"]),
        }
        for _, row in df.iterrows()
    ]


def drift_report(conn, commodity: str, recent_days: int = 90) -> dict:
    """Full drift + quality report for one commodity.

    PSI and the KS test compare the most recent ``recent_days`` of daily
    prices against all older history; Page-Hinkley and the EWMA chart watch
    the monthly series for gradual level shifts.
    """
    series = monthly_price_series(conn, commodity, months=48)
    monthly_values = [row["avg_price"] for row in series]

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
    if len(daily) < recent_days + 30:
        return {"error": f"Not enough daily history for {commodity}: {len(daily)} days"}

    prices = daily["avg_price"].astype(float)
    baseline = prices.iloc[:-recent_days]
    recent = prices.iloc[-recent_days:]

    quality_window = daily.tail(120)
    quality = data_quality_score(
        [d.date() if hasattr(d, "date") else d for d in quality_window["arrival_date"]],
        quality_window["avg_price"].astype(float).tolist(),
    )

    return {
        "commodity": commodity,
        "psi": population_stability_index(baseline, recent),
        "ks": ks_drift(baseline, recent),
        "page_hinkley": page_hinkley(monthly_values),
        "ewma": ewma_control_chart(monthly_values),
        "data_quality": quality,
        "recent_days": recent_days,
        "n_baseline_days": int(baseline.size),
        "n_recent_days": int(recent.size),
    }
