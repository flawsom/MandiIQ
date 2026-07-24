"""
Request logging and monitoring for the Margin Intelligence API.

Logs every prediction request for drift detection and performance monitoring.
Designed to be used by the FastAPI middleware.
"""

import json
import csv
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


LOG_DIR = Path("monitoring_logs")
REQUEST_LOG = LOG_DIR / "predictions.csv"
DRIFT_REPORT = LOG_DIR / "drift_report.json"
TRAINING_STATS_PATH = Path("models") / "training_feature_stats.json"


def ensure_log_dir():
    """Create monitoring log directory."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_prediction(
    endpoint: str,
    input_features: Dict[str, Any],
    output: Dict[str, Any],
    latency_ms: float,
    model_version: str = "v1",
):
    """Log a single prediction request to CSV."""
    ensure_log_dir()
    
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "endpoint": endpoint,
        "latency_ms": round(latency_ms, 2),
        "model_version": model_version,
        **{f"input_{k}": str(v) for k, v in input_features.items()},
        **{f"output_{k}": str(v) for k, v in output.items()},
    }
    
    file_exists = REQUEST_LOG.exists()
    with open(REQUEST_LOG, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def compute_training_statistics(df: pd.DataFrame, feature_cols: list):
    """Compute feature statistics from training data for drift comparison."""
    stats = {}
    for col in feature_cols:
        if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
            stats[col] = {
                "mean": float(df[col].mean()),
                "std": float(df[col].std()),
                "min": float(df[col].min()),
                "max": float(df[col].max()),
                "p5": float(df[col].quantile(0.05)),
                "p25": float(df[col].quantile(0.25)),
                "p50": float(df[col].quantile(0.5)),
                "p75": float(df[col].quantile(0.75)),
                "p95": float(df[col].quantile(0.95)),
            }
    with open(TRAINING_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)
    return stats


def check_drift(
    recent_predictions: pd.DataFrame,
    threshold_std: float = 3.0,
) -> Dict[str, Any]:
    """
    Basic drift detection: check if recent prediction input features
    deviate significantly from training distribution.
    
    Uses z-score-based outlier detection per feature.
    """
    if not TRAINING_STATS_PATH.exists():
        return {"drift_detected": False, "message": "No training stats available"}
    
    with open(TRAINING_STATS_PATH) as f:
        training_stats = json.load(f)
    
    drift_flags = {}
    overall_drift = False
    
    for col, stats in training_stats.items():
        if col not in recent_predictions.columns:
            continue
        
        values = pd.to_numeric(recent_predictions[col], errors="coerce").dropna()
        if len(values) == 0:
            continue
        
        mean = stats["mean"]
        std = stats["std"]
        if std == 0:
            continue
        
        z_scores = (values - mean) / std
        outlier_pct = (abs(z_scores) > threshold_std).mean() * 100
        
        if outlier_pct > 10:
            drift_flags[col] = {
                "outlier_pct": round(outlier_pct, 1),
                "current_mean": round(float(values.mean()), 4),
                "training_mean": mean,
                "drift_detected": True,
            }
            overall_drift = True
    
    report = {
        "drift_detected": overall_drift,
        "checked_at": datetime.utcnow().isoformat(),
        "n_requests_checked": len(recent_predictions),
        "drifted_features": drift_flags,
        "recommendation": (
            "Retrain recommended: feature distribution has shifted significantly"
            if overall_drift
            else "No significant drift detected"
        ),
    }
    
    with open(DRIFT_REPORT, "w") as f:
        json.dump(report, f, indent=2)
    
    return report


def get_monitoring_summary() -> Dict[str, Any]:
    """Get summary of monitoring data for the dashboard."""
    ensure_log_dir()
    
    if not REQUEST_LOG.exists():
        return {
            "total_requests": 0,
            "avg_latency_ms": 0,
            "drift_detected": False,
            "predictions_by_endpoint": {},
            "recent_requests": [],
        }
    
    df = pd.read_csv(REQUEST_LOG)
    
    if len(df) == 0:
        return {"total_requests": 0}
    
    summary = {
        "total_requests": len(df),
        "avg_latency_ms": round(df["latency_ms"].mean(), 2) if "latency_ms" in df else 0,
        "drift_detected": False,
    }
    
    if DRIFT_REPORT.exists():
        with open(DRIFT_REPORT) as f:
            drift = json.load(f)
            summary["drift_detected"] = drift.get("drift_detected", False)
    
    if "endpoint" in df.columns:
        summary["predictions_by_endpoint"] = df["endpoint"].value_counts().to_dict()
    
    recent = df.tail(20).to_dict(orient="records") if len(df) > 0 else []
    summary["recent_requests"] = recent
    
    return summary


if __name__ == "__main__":
    # Demo: generate synthetic traffic for monitoring view
    print("Generating synthetic request traffic for monitoring demo...")
    import random
    
    ensure_log_dir()
    
    categories = ["Furniture", "Office Supplies", "Technology"]
    regions = ["East", "West", "Central", "South"]
    segments = ["Consumer", "Corporate", "Home Office"]
    
    for _ in range(100):
        log_prediction(
            endpoint="/predict/loss-risk",
            input_features={
                "category": random.choice(categories),
                "region": random.choice(regions),
                "segment": random.choice(segments),
                "discount": round(random.uniform(0, 0.5), 2),
                "quantity": random.randint(1, 20),
            },
            output={
                "loss_probability": round(random.uniform(0, 1), 4),
                "prediction": random.randint(0, 1),
            },
            latency_ms=round(random.uniform(50, 500), 2),
        )
    
    summary = get_monitoring_summary()
    print(f"  Logged {summary['total_requests']} requests")
    print(f"  Avg latency: {summary['avg_latency_ms']}ms")
    print(f"  Drift detected: {summary['drift_detected']}")
