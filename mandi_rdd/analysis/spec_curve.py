"""Specification curve (multiverse) analysis and false-discovery control.

A single RDD number is one point in a space of defensible analyst choices. The
credibility question a reviewer asks next - "would this survive a different
bandwidth, kernel or polynomial order?" - is answered here by fitting the whole
grid and reporting the distribution of results, which is what a specification
curve is (Simonsohn, Simmons & Nelson 2020).

The second gap this closes is multiplicity. With 400+ commodities each getting
a discontinuity fit at p < 0.05, roughly one in twenty will look significant by
chance. Benjamini-Hochberg q-values are reported alongside the raw p-values so
the count of surviving commodities is honest.

Both entry points are pure numpy over arrays, so they are testable without a
warehouse, and every returned number is JSON-safe: FastAPI refuses to serialise
NaN or Inf, and a degenerate fit produces exactly those.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)

SPEC_CURVE_VERSION = "1.0"

# Same convention as rdd_engine.bandwidth_sensitivity: percentages of the
# observed range of the running variable.
DEFAULT_BANDWIDTHS = (10.0, 15.0, 20.0, 25.0, 30.0)
DEFAULT_KERNELS = ("triangular", "uniform", "epanechnikov")
DEFAULT_ORDERS = (1, 2)
DEFAULT_CUTOFF = -19.0
MIN_SIDE_OBSERVATIONS = 5

# An estimated discontinuity smaller than this many rupees is not a price
# effect, it is the residue of float arithmetic on a collapsed fit. Rupee prices
# sit in the 1e2-1e4 range, so doubles carry ~1e-12 absolute resolution there;
# a real discontinuity is never this small.
DEGENERATE_EFFECT_EPSILON = 1e-6


def _is_degenerate_effect(effect) -> bool:
    """True when an estimate is numerically zero rather than economically zero."""
    if effect is None:
        return True
    try:
        value = float(effect)
    except (TypeError, ValueError):
        return True
    if math.isnan(value) or math.isinf(value):
        return True
    return abs(value) < DEGENERATE_EFFECT_EPSILON


def _json_safe(value):
    """NaN/Inf must become null; FastAPI cannot serialise them."""
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return value
    if math.isnan(as_float) or math.isinf(as_float):
        return None
    return as_float


def kernel_weights(u: np.ndarray, kernel: str) -> np.ndarray:
    """Kernel weights for u = (x - cutoff) / bandwidth, all zero outside |u|<=1.

    The compact-support kernels are the ones used in practice for local
    polynomial RDD; ``uniform`` is included because reporting a result that
    only holds with a triangular kernel is exactly the kind of fragility the
    specification curve is meant to expose.
    """
    u = np.abs(np.asarray(u, dtype=float))
    inside = u <= 1.0
    if kernel == "triangular":
        return np.where(inside, 1.0 - u, 0.0)
    if kernel == "uniform":
        return np.where(inside, 1.0, 0.0)
    if kernel == "epanechnikov":
        return np.where(inside, 0.75 * (1.0 - u**2), 0.0)
    raise ValueError(f"Unknown kernel {kernel!r}")


def _wls(X: np.ndarray, y: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted least squares: beta = (X'WX)^-1 X'Wy."""
    W = np.diag(weights)
    XWX = X.T @ W @ X
    XWy = X.T @ W @ y
    try:
        return np.linalg.solve(XWX, XWy)
    except np.linalg.LinAlgError:
        return np.linalg.pinv(XWX) @ XWy


def local_polynomial_rdd(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float = DEFAULT_CUTOFF,
    bandwidth: float = 20.0,
    order: int = 1,
    kernel: str = "triangular",
) -> dict:
    """Local polynomial RDD with a selectable kernel and polynomial order.

    ``order=1, kernel="triangular"`` reproduces
    :func:`mandi_rdd.analysis.rdd_engine.local_linear_rdd` exactly, including
    the HC2 sandwich standard error and its n-2(order+1) degrees of freedom;
    ``tests/test_spec_curve.py`` asserts that equivalence so the two estimators
    cannot drift apart.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if order < 0:
        raise ValueError("order must be >= 0")

    x_centered = x - cutoff
    x_range = float(np.max(x) - np.min(x)) if len(x) else 0.0
    bw_absolute = bandwidth * x_range / 100.0

    blank = {
        "effect": None, "std_error": None, "p_value": None, "t_stat": None,
        "n_left": 0, "n_right": 0, "n_total": 0,
        "bandwidth": float(bandwidth), "bandwidth_absolute": float(bw_absolute),
        "order": int(order), "kernel": kernel,
        "x_range": x_range, "left_at_cutoff": None, "right_at_cutoff": None,
    }
    if bw_absolute <= 0 or len(x) < 2 * MIN_SIDE_OBSERVATIONS:
        return {**blank, "error": "Insufficient observations within bandwidth"}

    in_bandwidth = np.abs(x_centered) <= bw_absolute
    x_bw = x_centered[in_bandwidth]
    y_bw = y[in_bandwidth]
    if len(x_bw) < 2 * MIN_SIDE_OBSERVATIONS:
        return {**blank, "error": "Insufficient observations within bandwidth"}

    weights = kernel_weights(x_bw / bw_absolute, kernel)
    left = x_bw < 0
    right = x_bw >= 0
    n_left, n_right = int(left.sum()), int(right.sum())
    blank = {**blank, "n_left": n_left, "n_right": n_right, "n_total": n_left + n_right}
    if n_left < MIN_SIDE_OBSERVATIONS or n_right < MIN_SIDE_OBSERVATIONS:
        return {**blank, "error": "Too few observations on one side of cutoff"}

    n_cols = order + 1

    def _design(xs: np.ndarray) -> np.ndarray:
        return np.column_stack([xs**power for power in range(n_cols)])

    design_left = _design(x_bw[left])
    design_right = _design(x_bw[right])
    coef_left = _wls(design_left, y_bw[left], weights[left])
    coef_right = _wls(design_right, y_bw[right], weights[right])
    effect = float(coef_right[0] - coef_left[0])

    # Pooled block design: one polynomial per side. The effect is the
    # difference of the two intercepts, so its variance is the sum of theirs.
    n = n_left + n_right
    pooled = np.zeros((n, 2 * n_cols))
    pooled[:n_left, :n_cols] = design_left
    pooled[n_left:, n_cols:] = design_right
    y_pooled = np.concatenate([y_bw[left], y_bw[right]])
    w_pooled = np.concatenate([weights[left], weights[right]])

    beta_pooled = _wls(pooled, y_pooled, w_pooled)
    residuals = y_pooled - pooled @ beta_pooled

    weighted_design = pooled * np.sqrt(w_pooled)[:, np.newaxis]
    try:
        xwx_inv = np.linalg.inv(weighted_design.T @ weighted_design)
    except np.linalg.LinAlgError:
        xwx_inv = np.linalg.pinv(weighted_design.T @ weighted_design)

    leverage = np.diag(pooled @ xwx_inv @ (pooled * w_pooled[:, np.newaxis]).T)
    leverage = np.clip(leverage, 0, 0.99)
    e_adj = residuals**2 / (1 - leverage)

    meat = (pooled * w_pooled[:, np.newaxis]).T @ np.diag(e_adj) @ (pooled * w_pooled[:, np.newaxis])
    varcov = xwx_inv @ meat @ xwx_inv

    var_effect = varcov[n_cols, n_cols] + varcov[0, 0]
    se_effect = float(np.sqrt(var_effect)) if var_effect > 0 else 0.0
    df = n - 2 * n_cols
    if se_effect > 0 and df > 0:
        t_stat = effect / se_effect
        p_value = float(2 * (1 - stats.t.cdf(abs(t_stat), df=df)))
    else:
        t_stat = 0.0
        p_value = 1.0

    return {
        "effect": _json_safe(effect),
        "std_error": _json_safe(se_effect),
        "p_value": _json_safe(p_value),
        "t_stat": _json_safe(t_stat),
        "n_left": n_left,
        "n_right": n_right,
        "n_total": n,
        "bandwidth": float(bandwidth),
        "bandwidth_absolute": float(bw_absolute),
        "order": int(order),
        "kernel": kernel,
        "x_range": x_range,
        "left_at_cutoff": _json_safe(coef_left[0]),
        "right_at_cutoff": _json_safe(coef_right[0]),
        "error": None,
    }


def specification_curve(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float = DEFAULT_CUTOFF,
    bandwidths=DEFAULT_BANDWIDTHS,
    kernels=DEFAULT_KERNELS,
    orders=DEFAULT_ORDERS,
    alpha: float = 0.05,
) -> dict:
    """Fit every defensible specification and summarise the distribution.

    The summary is the honest headline: the median estimate across the curve,
    how many specifications are significant, and whether the sign is stable.
    A median near zero next to a single significant headline number is the
    finding, not a footnote.
    """
    specs = []
    for bandwidth in bandwidths:
        for kernel in kernels:
            for order in orders:
                result = local_polynomial_rdd(x, y, cutoff, bandwidth, order, kernel)
                specs.append(result)

    effects = [s["effect"] for s in specs if s.get("effect") is not None]
    p_values = [s["p_value"] for s in specs if s.get("p_value") is not None]
    significant = sum(1 for p in p_values if p is not None and p < alpha)

    summary = {
        "n_specifications": len(specs),
        "n_estimated": len(effects),
        "n_significant": significant,
        "share_significant": _json_safe(significant / len(effects)) if effects else None,
        "median_effect": _json_safe(float(np.median(effects))) if effects else None,
        "mean_effect": _json_safe(float(np.mean(effects))) if effects else None,
        "min_effect": _json_safe(float(np.min(effects))) if effects else None,
        "max_effect": _json_safe(float(np.max(effects))) if effects else None,
        "iqr_effect": _json_safe(float(np.percentile(effects, 75) - np.percentile(effects, 25)))
        if effects else None,
        "share_positive": _json_safe(sum(1 for e in effects if e > 0) / len(effects))
        if effects else None,
        "alpha": alpha,
    }
    if effects:
        sign_stable = summary["share_positive"] in (0.0, 1.0)
        majority_significant = (summary["share_significant"] or 0.0) >= 0.5
        summary["sign_stable"] = sign_stable
        summary["verdict"] = "stable" if (sign_stable and majority_significant) else "fragile"
        # How many of the significant specifications are expected to be noise.
        summary["expected_false_positives"] = _json_safe(alpha * len(effects))
    else:
        summary["sign_stable"] = None
        summary["verdict"] = "insufficient_data"

    return {
        "version": SPEC_CURVE_VERSION,
        "cutoff": cutoff,
        "summary": summary,
        "specifications": specs,
    }


def benjamini_hochberg(p_values: list, alpha: float = 0.05) -> list:
    """Benjamini-Hochberg q-values, returned in the input order.

    q = min over all thresholds at or above the ranked p of (p * n / rank).
    None entries stay None and are excluded from the ranking.
    """
    indexed = [(i, float(p)) for i, p in enumerate(p_values) if p is not None]
    q_values: list = [None] * len(p_values)
    if not indexed:
        return q_values

    n = len(indexed)
    indexed.sort(key=lambda item: item[1])
    running_min = 1.0
    # Walk from the largest p down so the monotonicity correction can be applied
    # in one pass.
    for rank, (original_index, p) in enumerate(reversed(indexed), start=1):
        position = n - rank + 1
        q = p * n / position
        running_min = min(running_min, q)
        q_values[original_index] = min(1.0, running_min)
    return q_values


def fdr_report(conn, alpha: float = 0.05) -> dict:
    """Multiple-testing control across every stored commodity estimate.

    Uses the most recent row per commodity, because older rows are superseded
    fits rather than additional hypotheses.

    Estimates whose effect is numerically indistinguishable from zero are not
    hypotheses. A collapsed fit - for example a commodity whose outcome series
    is flat within the bandwidth - produces a discontinuity of ~1e-12 rupees and
    a zero-variance standard error, which in turn drives the p-value to 0. Those
    rows used to be reported as the *strongest* findings in the catalog while
    every economically meaningful estimate was reported as a non-finding. They
    are excluded from the family and counted separately so the exclusion is
    visible rather than silent.
    """
    rows = conn.execute(
        """
        SELECT commodity, effect, p_value, n_left, n_right
        FROM rdd_results
        WHERE p_value IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY commodity ORDER BY computed_at DESC, id DESC
        ) = 1
        ORDER BY p_value
        """
    ).fetchall()

    degenerate = [r[0] for r in rows if _is_degenerate_effect(r[1])]
    rows = [r for r in rows if not _is_degenerate_effect(r[1])]

    commodities = [r[0] for r in rows]
    p_values = [float(r[2]) for r in rows]
    q_values = benjamini_hochberg(p_values, alpha)

    entries = []
    for commodity, effect, p_value, n_left, n_right, q_value in zip(
        commodities, [r[1] for r in rows], p_values, [r[3] for r in rows],
        [r[4] for r in rows], q_values,
    ):
        entries.append({
            "commodity": commodity,
            "effect": _json_safe(effect),
            "p_value": _json_safe(p_value),
            "q_value": _json_safe(q_value),
            "n_left": n_left,
            "n_right": n_right,
            "significant_raw": bool(p_value < alpha),
            "significant_fdr": bool(q_value is not None and q_value < alpha),
        })

    raw_count = sum(1 for e in entries if e["significant_raw"])
    fdr_count = sum(1 for e in entries if e["significant_fdr"])
    return {
        "version": SPEC_CURVE_VERSION,
        "alpha": alpha,
        "n_hypotheses": len(entries),
        "n_significant_raw": raw_count,
        "n_significant_fdr": fdr_count,
        "n_excluded_degenerate": len(degenerate),
        "degenerate": degenerate,
        "expected_false_positives": _json_safe(alpha * len(entries)),
        "survivors": [e["commodity"] for e in entries if e["significant_fdr"]],
        "entries": entries,
    }


def _sample_from_warehouse(conn, commodity: str) -> tuple[np.ndarray, np.ndarray]:
    """Monthly average price against rainfall departure, the national path.

    Mirrors the sample ``rdd_engine.run_rdd`` builds for ``state=None``: monthly
    average modal price per (year, month) crossed with every rainfall departure
    observation recorded in that month.
    """
    price_df = conn.execute(
        """
        SELECT EXTRACT(YEAR FROM arrival_date)  AS year,
               EXTRACT(MONTH FROM arrival_date) AS month,
               AVG(modal_price)                 AS avg_modal_price
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
        """,
        [commodity],
    ).fetchdf()
    if price_df.empty:
        return np.array([]), np.array([])

    rainfall_df = conn.execute(
        "SELECT month, departure_pct FROM rainfall WHERE departure_pct IS NOT NULL"
    ).fetchdf()
    if rainfall_df.empty:
        return np.array([]), np.array([])
    if float(rainfall_df["departure_pct"].abs().max()) < 1.0:
        # Defensive: ratio-scaled departures become percentages.
        rainfall_df["departure_pct"] = rainfall_df["departure_pct"] * 100.0

    merged = price_df.merge(rainfall_df, on="month", how="inner")
    merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])
    return (
        merged["departure_pct"].to_numpy(dtype=float),
        merged["avg_modal_price"].to_numpy(dtype=float),
    )


def spec_curve_report(conn, commodity: str, cutoff: float = DEFAULT_CUTOFF) -> dict:
    """Specification curve for one commodity, straight from the warehouse."""
    try:
        x, y = _sample_from_warehouse(conn, commodity)
    except Exception as exc:  # warehouse unavailable / empty
        logger.warning("Specification curve sample failed for %s: %s", commodity, exc)
        return {"commodity": commodity, "error": str(exc), "summary": None,
                "specifications": []}

    if len(x) < 2 * MIN_SIDE_OBSERVATIONS:
        return {
            "commodity": commodity,
            "error": f"Insufficient matched observations: {len(x)}",
            "summary": None,
            "specifications": [],
        }

    report = specification_curve(x, y, cutoff=cutoff)
    report["commodity"] = commodity
    report["n_observations"] = int(len(x))
    report["error"] = None
    return report
