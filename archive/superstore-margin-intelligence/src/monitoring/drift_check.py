"""
Data drift detection for the Superstore Margin Intelligence System.

Compares incoming prediction request distributions against training distributions
using Population Stability Index (PSI) and KS-tests.

Note: This monitors *simulated* traffic since there are no real production users.
Run with --simulate to generate demo drift check output.
"""

import numpy as np
import pandas as pd
from pathlib import Path
import json
import warnings
warnings.filterwarnings("ignore")

from scipy import stats


def compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """
    Population Stability Index.
    
    PSI < 0.1: No significant change
    0.1 <= PSI < 0.2: Moderate change
    PSI >= 0.2: Significant shift (drift flagged)
    """
    expected = np.array(expected)
    actual = np.array(actual)
    
    # Remove NaN
    expected = expected[~np.isnan(expected)]
    actual = actual[~np.isnan(actual)]
    
    if len(expected) == 0 or len(actual) == 0:
        return 0.0
    
    # Create bins based on expected distribution
    if len(np.unique(expected)) < bins:
        # Discrete variable - use unique values
        breaks = np.sort(np.unique(expected))
    else:
        # Continuous variable - use percentiles
        breaks = np.percentile(expected, np.linspace(0, 100, bins + 1))
        breaks = np.unique(breaks)
    
    psi = 0.0
    for i in range(len(breaks) - 1):
        p_i = np.mean((expected >= breaks[i]) & (expected < breaks[i + 1]))
        q_i = np.mean((actual >= breaks[i]) & (actual < breaks[i + 1]))
        
        # Handle edge cases
        p_i = max(p_i, 0.001)
        q_i = max(q_i, 0.001)
        
        psi += (p_i - q_i) * np.log(p_i / q_i)
    
    # Last bin
    p_i = max(np.mean(expected >= breaks[-1]), 0.001)
    q_i = max(np.mean(actual >= breaks[-1]), 0.001)
    psi += (p_i - q_i) * np.log(p_i / q_i)
    
    return psi


def check_numerical_drift(
    train_values: np.ndarray,
    current_values: np.ndarray,
    feature_name: str,
    threshold: float = 0.05,
) -> dict:
    """
    Check for drift in a numerical feature using KS-test.
    """
    if len(train_values) < 5 or len(current_values) < 5:
        return {"feature": feature_name, "drift_detected": False, "note": "Insufficient data"}
    
    ks_stat, p_value = stats.ks_2samp(train_values, current_values)
    drift = p_value < threshold
    
    psi = compute_psi(train_values, current_values)
    
    return {
        "feature": feature_name,
        "drift_detected": drift,
        "ks_statistic": float(ks_stat),
        "ks_p_value": float(p_value),
        "psi": float(psi),
        "train_mean": float(train_values.mean()),
        "current_mean": float(current_values.mean()),
        "train_std": float(train_values.std()),
        "current_std": float(current_values.std()),
    }


def check_categorical_drift(
    train_values: np.ndarray,
    current_values: np.ndarray,
    feature_name: str,
    psi_threshold: float = 0.2,
) -> dict:
    """
    Check for drift in a categorical feature using PSI.
    """
    if len(train_values) < 5 or len(current_values) < 5:
        return {"feature": feature_name, "drift_detected": False, "note": "Insufficient data"}
    
    psi = compute_psi(
        pd.Series(train_values).astype("category").cat.codes.values,
        pd.Series(current_values).astype("category").cat.codes.values,
    )
    
    drift = psi > psi_threshold
    
    return {
        "feature": feature_name,
        "drift_detected": drift,
        "psi": float(psi),
        "train_unique_values": int(len(np.unique(train_values))),
        "current_unique_values": int(len(np.unique(current_values))),
    }


def run_drift_check(
    training_data_path: str = "data/processed/superstore_clean.csv",
    monitoring_data_path: str = None,
    simulate: bool = True,
) -> dict:
    """
    Run full drift check.
    
    If simulate=True, generates synthetic "current" data by perturbing training data.
    """
    print("=== Data Drift Check ===")
    
    # Load training data
    train_df = pd.read_csv(training_data_path)
    print(f"  Training data: {len(train_df):,} rows")
    
    # Use training data as "current" (no real production data)
    # For demo, perturb the data slightly to simulate drift
    if simulate or monitoring_data_path is None:
        print("  Simulating current data (no real production traffic)...")
        current_df = train_df.copy()
        
        # Introduce small perturbations
        rng = np.random.RandomState(42)
        if "discount" in current_df.columns:
            current_df["discount"] = current_df["discount"] + rng.normal(0, 0.02, len(current_df))
            current_df["discount"] = current_df["discount"].clip(0, 1)
        if "quantity" in current_df.columns:
            current_df["quantity"] = (current_df["quantity"] + rng.poisson(0.5, len(current_df))).clip(1)
        if "shipping_delay" in current_df.columns:
            current_df["shipping_delay"] = (current_df["shipping_delay"] + rng.choice([-1, 0, 1], len(current_df))).clip(0)
    else:
        current_df = pd.read_csv(monitoring_data_path)
    
    # Numerical features to check
    numerical_features = ["discount", "quantity", "shipping_delay", "sales", "profit"]
    numerical_results = []
    
    for feat in numerical_features:
        if feat in train_df.columns and feat in current_df.columns:
            result = check_numerical_drift(
                train_df[feat].values,
                current_df[feat].values,
                feat,
            )
            numerical_results.append(result)
            status = "⚠️ DRIFT" if result["drift_detected"] else "✅ OK"
            print(f"  {status} {feat:20s} | KS p={result['ks_p_value']:.4f} | PSI={result['psi']:.4f}")
    
    # Categorical features to check
    categorical_features = ["category", "region", "segment", "ship_mode"]
    categorical_results = []
    
    for feat in categorical_features:
        if feat in train_df.columns and feat in current_df.columns:
            result = check_categorical_drift(
                train_df[feat].values,
                current_df[feat].values,
                feat,
            )
            categorical_results.append(result)
            status = "⚠️ DRIFT" if result["drift_detected"] else "✅ OK"
            print(f"  {status} {feat:20s} | PSI={result['psi']:.4f}")
    
    # Summary
    drift_count = sum(1 for r in numerical_results + categorical_results if r["drift_detected"])
    total_checks = len(numerical_results) + len(categorical_results)
    
    summary = {
        "timestamp": str(pd.Timestamp.now()),
        "drift_checks_performed": total_checks,
        "drifts_detected": drift_count,
        "drift_rate": round(drift_count / total_checks * 100, 1) if total_checks > 0 else 0,
        "numerical_results": numerical_results,
        "categorical_results": categorical_results,
        "simulated": simulate,
    }
    
    print(f"\n  Drift checks: {total_checks} | Drifts detected: {drift_count} ({summary['drift_rate']}%)")
    
    # Save
    output_path = Path("models/monitoring/drift_check.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Saved to {output_path}")
    
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", default=True)
    parser.add_argument("--monitoring-data", type=str, default=None)
    args = parser.parse_args()
    
    run_drift_check(simulate=args.simulate, monitoring_data_path=args.monitoring_data)
