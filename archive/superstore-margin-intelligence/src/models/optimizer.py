"""
Prescriptive optimization layer for the Superstore Margin Intelligence System.

Computes the maximum "safe" discount for a given order configuration
such that expected profit remains non-negative.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
import joblib

from src.models.classifier import load_classifier, predict_loss


# Cache for historical data to avoid repeated disk I/O
_HIST_CACHE = {}

def _get_historical_data():
    """Load and cache historical data once."""
    global _HIST_CACHE
    if "df" not in _HIST_CACHE:
        try:
            from src.features.engineer import engineer_features
            df = pd.read_csv("data/processed/superstore_clean.csv",
                           parse_dates=["order_date", "ship_date"])
            df = engineer_features(df)
            _HIST_CACHE["df"] = df
        except FileNotFoundError:
            _HIST_CACHE["df"] = pd.DataFrame()
    return _HIST_CACHE["df"]


def get_segment_stats(category, sub_category, region, segment):
    """Get pre-computed segment-level stats for profit estimation."""
    df = _get_historical_data()
    if len(df) == 0:
        return {"avg_profit_when_profitable": 50, "avg_loss_when_loss": -50, "count": 0}
    
    similar = df[
        (df["category"] == category)
        & (df["sub_category"] == sub_category)
        & (df["region"] == region)
        & (df["segment"] == segment)
    ]
    
    if len(similar) < 5:
        # Fall back to broader category+region
        similar = df[
            (df["category"] == category)
            & (df["region"] == region)
        ]
    
    if len(similar) < 5:
        return {"avg_profit_when_profitable": 50, "avg_loss_when_loss": -50, "count": 0}
    
    profitable = similar[similar["profit"] >= 0]["profit"]
    loss = similar[similar["profit"] < 0]["profit"]
    
    return {
        "avg_profit_when_profitable": float(profitable.mean()) if len(profitable) > 0 else 50.0,
        "avg_loss_when_loss": float(loss.mean()) if len(loss) > 0 else -50.0,
        "count": len(similar),
    }


def compute_safe_discount(
    category: str,
    sub_category: str,
    region: str,
    segment: str,
    quantity: int = 3,
    ship_mode: str = "Standard Class",
    shipping_delay: int = 4,
    artifacts: dict = None,
    model_dir: str = "models",
    n_steps: int = 20,  # Reduced for speed
) -> dict:
    """
    Compute the maximum discount level at which expected profit
    remains non-negative for a given order configuration.
    
    Uses the classifier to assess loss risk at each discount level,
    and estimates expected margin based on segment-level historical data.
    """
    if artifacts is None:
        artifacts = load_classifier(model_dir)

    # Get segment stats for expected profit estimation
    stats = get_segment_stats(category, sub_category, region, segment)

    # Scan discount levels from 0 to 80%
    discount_levels = np.linspace(0, 0.80, n_steps)
    results = []

    for discount in discount_levels:
        features = {
            "category": category,
            "sub_category": sub_category,
            "region": region,
            "segment": segment,
            "discount": discount,
            "quantity": quantity,
            "ship_mode": ship_mode,
            "shipping_delay": shipping_delay,
        }

        prediction = predict_loss(features, artifacts)
        risk = prediction["loss_probability"]

        # Expected profit = P(profitable) * avg_profit + P(loss) * avg_loss
        expected_profit = (1 - risk) * stats["avg_profit_when_profitable"] + risk * stats["avg_loss_when_loss"]

        results.append({
            "discount": float(discount),
            "loss_risk": float(risk),
            "estimated_margin": float(expected_profit),
        })

    # Find the maximum discount where expected profit >= 0
    safe_discount = 0.0
    for r in results:
        if r["estimated_margin"] >= 0:
            safe_discount = r["discount"]
        else:
            break

    # Get prediction at recommended discount
    safe_features = {
        "category": category,
        "sub_category": sub_category,
        "region": region,
        "segment": segment,
        "discount": safe_discount,
        "quantity": quantity,
        "ship_mode": ship_mode,
        "shipping_delay": shipping_delay,
    }
    safe_prediction = predict_loss(safe_features, artifacts)

    # Get prediction at a typical higher discount for comparison
    current_discount = min(safe_discount + 0.15, 0.80)
    current_features = {**safe_features, "discount": current_discount}
    current_prediction = predict_loss(current_features, artifacts)

    return {
        "recommended_max_discount": round(safe_discount * 100, 1),
        "safe_discount_pct": round(safe_discount * 100, 1),
        "current_loss_risk": round(current_prediction["loss_probability"] * 100, 1),
        "safe_loss_risk": round(safe_prediction["loss_probability"] * 100, 1),
        "current_risk_shap": current_prediction["top_3_shap"],
        "safe_risk_shap": safe_prediction["top_3_shap"],
        "discount_scan": results,
        "metrics": {
            "segment_sample_count": stats["count"],
        },
    }


def batch_optimize(model_dir: str = "models") -> pd.DataFrame:
    """
    Compute safe discounts for all category/sub_category/region/segment combinations.
    Returns a DataFrame of recommendations.
    """
    artifacts = load_classifier(model_dir)
    df = pd.read_csv("data/processed/superstore_clean.csv")

    # Get unique combinations
    combos = df.groupby(["category", "sub_category", "region", "segment"]).size().reset_index()
    combos.columns = ["category", "sub_category", "region", "segment", "count"]
    combos = combos[combos["count"] >= 5]  # Require minimum orders

    print(f"Computing safe discounts for {len(combos)} configurations...")

    results = []
    for _, row in combos.iterrows():
        try:
            result = compute_safe_discount(
                category=row["category"],
                sub_category=row["sub_category"],
                region=row["region"],
                segment=row["segment"],
                artifacts=artifacts,
            )
            results.append({
                "category": row["category"],
                "sub_category": row["sub_category"],
                "region": row["region"],
                "segment": row["segment"],
                "order_count": row["count"],
                "safe_max_discount_pct": result["safe_discount_pct"],
            })
        except Exception as e:
            print(f"  Error for {row['category']}/{row['sub_category']}/{row['region']}/{row['segment']}: {e}")

    result_df = pd.DataFrame(results).sort_values("safe_max_discount_pct")
    result_df.to_csv(Path(model_dir) / "discount_recommendations.csv", index=False)
    print(f"Saved {len(result_df)} recommendations to {model_dir}/discount_recommendations.csv")

    return result_df


if __name__ == "__main__":
    batch_optimize()
