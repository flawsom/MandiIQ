"""Tests for the specification curve and multiple-testing control.

The value of these two tools is that they are allowed to disagree with the
headline RDD number, so the tests are mostly about the properties that make
that disagreement trustworthy:

  * the general estimator reproduces the existing local-linear one exactly,
    so the curve is measuring specification choice and not a second bug,
  * an effect that is genuinely there survives every specification,
  * an effect that is not there does not,
  * BH q-values are monotone, never below their own p-value, and reduce to
    ``p * n / rank`` in the simple case.
"""

from __future__ import annotations

import numpy as np
import pytest

from mandi_rdd.analysis import rdd_engine, spec_curve


def _synthetic(n: int = 4000, jump: float = 250.0, seed: int = 7):
    """Rainfall departure with a real discontinuity at the -19% cutoff."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(-80, 60, n)
    y = 1200 + (-x) * 4.0 + rng.normal(0, 120, n)
    y = np.where(x >= -19.0, y + jump, y)
    return x, y


# ── estimator equivalence ───────────────────────────────────────────────────

def test_general_estimator_reproduces_the_local_linear_one():
    """order=1 + triangular must be the same estimator, to the last bit.

    Without this the specification curve would be reporting the difference
    between two implementations rather than between analyst choices.
    """
    x, y = _synthetic()
    reference = rdd_engine.local_linear_rdd(x, y, -19.0, bandwidth=20)
    candidate = spec_curve.local_polynomial_rdd(x, y, -19.0, bandwidth=20, order=1)

    for field in ("effect", "std_error", "p_value", "n_left", "n_right", "n_total"):
        assert candidate[field] == pytest.approx(reference[field], rel=1e-9, abs=1e-9), field


def test_higher_orders_are_actually_a_different_fit():
    x, y = _synthetic()
    linear = spec_curve.local_polynomial_rdd(x, y, -19.0, 20, order=1)
    quadratic = spec_curve.local_polynomial_rdd(x, y, -19.0, 20, order=2)
    assert quadratic["effect"] != pytest.approx(linear["effect"], rel=1e-6)


def test_kernels_change_the_weights_but_not_the_target():
    x, y = _synthetic(n=2000, jump=400.0)
    effects = [
        spec_curve.local_polynomial_rdd(x, y, -19.0, 25, order=1, kernel=k)["effect"]
        for k in ("triangular", "uniform", "epanechnikov")
    ]
    assert all(e > 0 for e in effects), effects
    assert len({round(e, 6) for e in effects}) > 1, "kernels were ignored"


def test_unknown_kernel_is_rejected():
    with pytest.raises(ValueError, match="Unknown kernel"):
        spec_curve.kernel_weights(np.array([0.1]), "gaussian")


def test_kernel_weights_vanish_outside_the_support():
    u = np.array([-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5])
    for kernel in ("triangular", "uniform", "epanechnikov"):
        w = spec_curve.kernel_weights(u, kernel)
        assert w[0] == 0.0 and w[-1] == 0.0, kernel
        assert np.all(w >= 0), kernel
        assert w[3] == pytest.approx(1.0 if kernel != "epanechnikov" else 0.75), kernel


def test_degenerate_input_returns_nulls_not_nan():
    """FastAPI cannot serialise NaN, so nothing may ever return it."""
    x = np.array([1.0, 2.0, 3.0])
    y = np.array([1.0, 2.0, 3.0])
    result = spec_curve.local_polynomial_rdd(x, y, -19.0, 20)
    assert result["effect"] is None
    assert result["error"]


# ── the specification curve itself ──────────────────────────────────────────

def test_a_real_discontinuity_survives_every_specification():
    x, y = _synthetic(jump=500.0)
    curve = spec_curve.specification_curve(x, y)

    summary = curve["summary"]
    assert summary["n_specifications"] == 30  # 5 bandwidths x 3 kernels x 2 orders
    assert summary["n_estimated"] == 30
    assert summary["verdict"] == "stable"
    assert summary["sign_stable"] is True
    assert summary["median_effect"] == pytest.approx(500.0, abs=120.0)
    assert summary["share_significant"] >= 0.9


def test_the_curve_exposes_a_fragile_result():
    """No jump: the verdict must not be 'stable' just because one cell is."""
    rng = np.random.default_rng(11)
    x = rng.uniform(-80, 60, 3000)
    y = 1200 - x * 4.0 + rng.normal(0, 400, 3000)  # no discontinuity anywhere
    curve = spec_curve.specification_curve(x, y)

    assert curve["summary"]["median_effect"] == pytest.approx(0.0, abs=200.0)
    assert curve["summary"]["verdict"] == "fragile"


def test_every_specification_is_reported_for_plotting():
    x, y = _synthetic(n=800)
    curve = spec_curve.specification_curve(x, y)
    assert len(curve["specifications"]) == curve["summary"]["n_specifications"]
    for spec in curve["specifications"]:
        assert {"bandwidth", "kernel", "order", "effect", "p_value"} <= set(spec)


def test_specifications_are_json_safe():
    import json

    x, y = _synthetic(n=600)
    json.dumps(spec_curve.specification_curve(x, y))  # must not raise


# ── multiple testing ────────────────────────────────────────────────────────

def test_benjamini_hochberg_matches_the_textbook_example():
    # Classic worked example: p = .01 .02 .03 .04 across n=4.
    q = spec_curve.benjamini_hochberg([0.01, 0.02, 0.03, 0.04], alpha=0.05)
    assert q == pytest.approx([0.04, 0.04, 0.04, 0.04])


def test_q_values_are_monotone_and_never_below_their_p_value():
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    qs = spec_curve.benjamini_hochberg(ps, alpha=0.05)
    assert all(q >= p for p, q in zip(ps, qs))
    assert qs == sorted(qs)
    assert all(0 <= q <= 1 for q in qs)


def test_missing_p_values_are_preserved_as_none():
    qs = spec_curve.benjamini_hochberg([0.02, None, 0.3])
    assert qs[1] is None
    assert qs[0] is not None and qs[2] is not None


def test_fdr_report_counts_survivors_from_the_warehouse():
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, sql, params=None):
            # 10 hypotheses, p increasing: only the first is truly small.
            return _Result([
                ("A", 100.0, 0.0001, 50, 50),
                ("B", 10.0, 0.04, 50, 50),
                ("C", 5.0, 0.04, 50, 50),
                ("D", 1.0, 0.04, 50, 50),
                ("E", 1.0, 0.05, 50, 50),
                ("F", 1.0, 0.20, 50, 50),
                ("G", 1.0, 0.30, 50, 50),
                ("H", 1.0, 0.40, 50, 50),
                ("I", 1.0, 0.60, 50, 50),
                ("J", 1.0, 0.90, 50, 50),
            ])

    report = spec_curve.fdr_report(_Conn(), alpha=0.05)
    assert report["n_hypotheses"] == 10
    assert report["n_significant_raw"] == 4  # 0.05 itself is not below alpha
    assert report["n_significant_fdr"] < report["n_significant_raw"]
    assert report["expected_false_positives"] == pytest.approx(0.5)
    assert all(e["q_value"] is not None for e in report["entries"])


# ── warehouse-backed report ─────────────────────────────────────────────────

@pytest.fixture()
def warehouse(tmp_path):
    import duckdb
    from mandi_rdd.storage import duckdb_store

    conn = duckdb.connect(":memory:")
    duckdb_store.init_schema(conn)
    yield conn
    conn.close()


def test_spec_curve_report_degrades_gracefully_on_an_empty_warehouse(warehouse):
    report = spec_curve.spec_curve_report(warehouse, "Onion")
    assert report["error"]
    assert report["summary"] is None


def test_spec_curve_report_runs_end_to_end_on_real_rows(warehouse):
    from mandi_rdd.storage import duckdb_store

    rng = np.random.default_rng(3)
    rows = []
    for month in range(1, 13):
        for day in (5, 15, 25):
            price = 1000 + month * 12 + rng.normal(0, 20)
            rows.append({
                "state": "Maharashtra", "district": "Pune", "market": "Pune",
                "commodity": "Onion", "variety": "Other", "grade": "FAQ",
                # 2025, not the current year: a "future" arrival date is
                # rejected by the ingest guard and would leave the fixture
                # dependent on the day the suite happens to run.
                "arrival_date": f"2025-{month:02d}-{day:02d}",
                "min_price": price - 50, "max_price": price + 50,
                "modal_price": price,
            })
    duckdb_store.upsert_prices(warehouse, rows)
    for month in range(1, 13):
        warehouse.execute(
            "INSERT INTO rainfall (sub_division, year, month, rainfall_mm, normal_mm, departure_pct) "
            "VALUES ('Pune', 2025, ?, 100, 120, ?)",
            [month, -30.0 + month * 5],
        )

    report = spec_curve.spec_curve_report(warehouse, "Onion")
    assert report["error"] is None
    assert report["n_observations"] > 0
    assert report["summary"]["n_specifications"] == 30
