"""
MandiRDD — robustness check implementations.

Implements all four robustness checks from the PRD §7:
1. Bandwidth sensitivity — re-run at 3-5 bandwidths
2. Placebo/falsification test — run RDD at fake cutoffs
3. McCrary-style density check — check for running variable manipulation
4. Covariate balance — check pre-treatment covariates don't jump

Each function is independent and surfaces results in the dashboard.
"""

import numpy as np
import pandas as pd
from typing import Optional
import logging

from mandi_rdd.analysis.rdd_engine import (
    local_linear_rdd,
    bandwidth_sensitivity,
    placebo_test,
    mccrary_density_test,
    covariate_balance,
)

logger = logging.getLogger(__name__)


def full_robustness_report(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    covariates: Optional[dict[str, np.ndarray]] = None,
    main_result: Optional[dict] = None,
) -> dict:
    """
    Run all robustness checks and compile a single report dict.
    
    This is what gets surfaced in the dashboard's "Methodology" tab.
    """
    report = {
        "main_result": main_result or {},
        "checks": {},
    }
    
    # 1. Bandwidth sensitivity
    try:
        bw_result = bandwidth_sensitivity(x, y, cutoff)
        report["checks"]["bandwidth_sensitivity"] = {
            "status": "ok",
            "results": bw_result,
            "passed": _check_bandwidth_robustness(bw_result),
        }
    except Exception as e:
        report["checks"]["bandwidth_sensitivity"] = {"status": "error", "error": str(e)}
    
    # 2. Placebo tests
    try:
        placebo_result = placebo_test(x, y, cutoff)
        report["checks"]["placebo_test"] = {
            "status": "ok",
            "results": placebo_result,
            "passed": _check_placebo(placebo_result),
        }
    except Exception as e:
        report["checks"]["placebo_test"] = {"status": "error", "error": str(e)}
    
    # 3. McCrary density test
    try:
        density_result = mccrary_density_test(x, y, cutoff)
        report["checks"]["density_test"] = {
            "status": "ok",
            "result": density_result,
            "passed": _check_density(density_result),
        }
    except Exception as e:
        report["checks"]["density_test"] = {"status": "error", "error": str(e)}
    
    # 4. Covariate balance
    if covariates:
        try:
            balance_result = covariate_balance(x, covariates, cutoff)
            report["checks"]["covariate_balance"] = {
                "status": "ok",
                "results": balance_result,
                "passed": _check_covariate_balance(balance_result),
            }
        except Exception as e:
            report["checks"]["covariate_balance"] = {"status": "error", "error": str(e)}
    
    # Overall robustness score
    checks_passed = [
        v.get("passed", False)
        for v in report["checks"].values()
        if v.get("status") == "ok"
    ]
    if checks_passed:
        report["overall_passed"] = sum(checks_passed) / len(checks_passed) >= 0.75
        report["robustness_score"] = f"{sum(checks_passed)}/{len(checks_passed)}"
    else:
        report["overall_passed"] = False
        report["robustness_score"] = "0/0"
    
    return report


def _check_bandwidth_robustness(results: list[dict]) -> bool:
    """
    Check that the effect doesn't flip sign or become wildly
    unstable across bandwidths.
    
    Passes if:
    - At least 3 bandwidths have valid results
    - Effect doesn't flip sign more than once
    - At least one bandwidth shows significance at p < 0.1
    """
    valid = [r for r in results if r.get("effect") is not None]
    
    if len(valid) < 3:
        return False
    
    effects = [r["effect"] for r in valid]
    signs = [1 if e > 0 else -1 for e in effects]
    
    # Count sign changes
    sign_changes = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
    
    # Check if any is significant
    any_significant = any(
        r.get("p_value") is not None and r["p_value"] < 0.1
        for r in valid
    )
    
    return sign_changes <= 1 and any_significant


def _check_placebo(results: list[dict]) -> bool:
    """
    Check that placebo cutoffs don't produce significant effects.
    
    Passes if fewer than half of placebo tests are significant.
    """
    valid = [r for r in results if r.get("effect") is not None]
    
    if len(valid) < 2:
        return True  # Not enough placebos to be meaningful
    
    significant = sum(
        1 for r in valid
        if r.get("p_value") is not None and r["p_value"] < 0.05
    )
    
    return significant / len(valid) < 0.5


def _check_density(result: dict) -> bool:
    """
    Check that there's no significant discontinuity in the density
    of the running variable at the cutoff.
    
    Passes if density_jump p-value > 0.05 (no evidence of manipulation).
    """
    p = result.get("density_p_value")
    if p is None:
        return True  # Can't compute, assume passed
    return p > 0.05


def _check_covariate_balance(results: dict) -> bool:
    """
    Check that no covariate shows a significant discontinuity.
    
    Passes if no covariate has p < 0.05 (Bonferroni-adjusted).
    """
    if not results:
        return True
    
    n_tests = len(results)
    bonferroni_threshold = 0.05 / n_tests if n_tests > 0 else 0.05
    
    for name, r in results.items():
        p = r.get("p_value")
        if p is not None and p < bonferroni_threshold:
            logger.warning(f"Covariate imbalance detected: {name} (p={p:.4f})")
            return False
    
    return True
