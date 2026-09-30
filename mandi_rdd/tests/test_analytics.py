"""Tests for the advanced analytics engine.

Every estimator is exercised on synthetic data with known ground truth, so the
suite runs in CI without a warehouse, external API keys or model artefacts.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from mandi_rdd.analysis import conformal, dml, drift, nowcast, tail_risk


# ── Conformal prediction ────────────────────────────────────────────

def test_split_conformal_produces_ordered_calibrated_intervals():
    rng = np.random.default_rng(7)
    y_true = np.linspace(100, 130, 200) + rng.normal(0, 3, 200)
    y_pred = y_true - rng.normal(0, 2.5, 200)

    result = conformal.split_conformal_intervals(y_true, y_pred, [140.0, 145.0], alpha=0.1)

    assert result["half_width"] > 0
    assert result["calibration_coverage"] >= 0.85
    assert all(lo < hi for lo, hi in zip(result["lower"], result["upper"]))


def test_adaptive_conformal_tracks_a_regime_shift():
    rng = np.random.default_rng(11)
    n = 400
    level = np.cumsum(rng.normal(0, 1, n)) + 100
    y_true = level + rng.normal(0, 1, n)
    y_pred = level + rng.normal(0, 1, n)
    y_true[n // 2:] += 10  # regime shift the point forecast does not know about

    result = conformal.adaptive_conformal_intervals(y_true, y_pred, alpha=0.1, gamma=0.02)

    assert result["n_intervals"] > 300
    assert 0.6 <= result["empirical_coverage"] <= 1.0
    assert result["mean_interval_width"] > 0
    assert result["alpha_final"] >= 0.001


# ── Drift monitoring ────────────────────────────────────────────────

def test_psi_flags_a_major_shift():
    rng = np.random.default_rng(3)
    baseline = rng.normal(100, 5, 500)
    stable = rng.normal(100, 5, 500)
    shifted = rng.normal(130, 5, 500)

    assert drift.population_stability_index(baseline, stable)["psi"] < 0.25
    shifted_result = drift.population_stability_index(baseline, shifted)
    assert shifted_result["psi"] > 0.25
    assert shifted_result["verdict"] == "major_shift"


def test_ks_drift_separates_shifted_samples():
    rng = np.random.default_rng(4)
    baseline = rng.normal(100, 5, 400)
    stable = rng.normal(100, 5, 400)
    shifted = rng.normal(130, 5, 400)

    assert drift.ks_drift(baseline, shifted)["drift_detected"] is True
    assert drift.ks_drift(baseline, stable)["p_value"] > 0.01


def test_page_hinkley_finds_the_level_shift():
    rng = np.random.default_rng(6)
    series = np.concatenate([rng.normal(0, 1, 200), rng.normal(8, 1, 200)])

    result = drift.page_hinkley(series, delta=0.005, threshold=25)

    assert result["n_alarms"] >= 1
    assert any(150 <= alarm["index"] <= 280 for alarm in result["alarms"])


def test_ewma_flags_gradual_shift():
    rng = np.random.default_rng(8)
    series = np.concatenate([rng.normal(0, 1, 100), rng.normal(2.5, 1, 100)])

    result = drift.ewma_control_chart(series, lam=0.2, control_limit=3.0)

    assert result["n_out_of_control"] >= 1


def test_data_quality_score_penalises_gaps_and_staleness():
    start = dt.date(2026, 1, 1)
    reference = dt.date(2026, 3, 1)
    full_dates = [start + dt.timedelta(days=i) for i in range(60)]
    full_values = [100 + 0.1 * i for i in range(60)]

    full = drift.data_quality_score(full_dates, full_values, reference_date=reference)
    sparse = drift.data_quality_score(full_dates[:20], full_values[:20], reference_date=reference)

    assert full["score"] > sparse["score"]
    assert full["completeness"] == 1.0
    assert sparse["staleness_days"] > 0


# ── Tail risk / EVT ─────────────────────────────────────────────────

def test_historical_var_cvar_ordering():
    rng = np.random.default_rng(5)
    returns = rng.standard_t(4, 2000) * 0.02

    result = tail_risk.historical_var_cvar(returns, 0.95)

    assert result["var"] > 0
    assert result["cvar"] >= result["var"]


def test_evt_tail_estimates_beyond_the_threshold():
    rng = np.random.default_rng(9)
    returns = rng.standard_t(3, 3000) * 0.02
    losses = -returns

    evt = tail_risk.evt_tail_risk(losses, confidence=0.99)

    assert evt["var"] > evt["threshold"]
    assert evt["expected_shortfall"] >= evt["var"]
    assert evt["shape_xi"] > 0  # Student-t losses have a heavy right tail


def test_max_drawdown_finds_peak_to_trough():
    prices = np.array([100.0, 120.0, 110.0, 115.0, 90.0, 95.0, 80.0, 130.0, 125.0, 140.0, 135.0, 150.0])

    result = tail_risk.max_drawdown(prices)

    assert round(result["max_drawdown_pct"], 1) == round((80.0 / 120.0 - 1.0) * 100.0, 1)
    assert result["peak_index"] == 1
    assert result["trough_index"] == 6


# ── Debiased ML ─────────────────────────────────────────────────────

def test_dml_recovers_a_known_linear_effect():
    rng = np.random.default_rng(42)
    n = 1500
    X = rng.normal(size=(n, 4))
    coefficients = np.array([0.5, -0.3, 0.2, 0.0])
    treatment = X @ coefficients + rng.normal(0, 1, n)
    true_theta = 2.5
    outcome = true_theta * treatment + X @ np.array([1.0, 1.0, -1.0, 0.5]) + rng.normal(0, 1, n)

    result = dml.partial_linear_dml(outcome, treatment, X, n_splits=2, random_state=0)

    assert abs(result["theta"] - true_theta) < 0.15
    assert result["ci95_lower"] < true_theta < result["ci95_upper"]
    assert result["p_value"] < 0.001
    assert result["n_obs"] == n


def test_dml_rejects_small_samples():
    result = dml.partial_linear_dml([1.0] * 50, [0.0] * 50, [[1.0]] * 50)
    assert "error" in result


# ── Nowcasting ──────────────────────────────────────────────────────

def test_kalman_smoother_beats_raw_noise():
    rng = np.random.default_rng(13)
    n = 120
    true_level = 100 + 0.3 * np.arange(n) + rng.normal(0, 0.5, n)
    observed = true_level + rng.normal(0, 2.0, n)

    result = nowcast.kalman_local_linear_trend(observed)

    assert "error" not in result
    smoothed_rmse = float(np.sqrt(np.mean((result["level"][: n] - true_level) ** 2)))
    raw_rmse = float(np.sqrt(np.mean((observed - true_level) ** 2)))
    assert smoothed_rmse < raw_rmse


def test_nowcast_projects_incomplete_month_to_month_end():
    # Two months of history ending 20 June: the June reporting month is 2/3 done.
    start = dt.date(2026, 5, 1)
    dates = [start + dt.timedelta(days=i) for i in range(51)]
    prices = [95.0 + 0.1 * i for i in range(51)]

    result = nowcast.nowcast_month_end(dates, prices)

    assert result["days_remaining"] == 10
    assert 90 <= result["nowcast_price"] <= 115
    assert result["ci_lower"] < result["nowcast_price"] < result["ci_upper"]
    assert result["completion_pct"] < 100.0
