"""
MandiRDD — Price-spike risk classifier.

XGBoost + SHAP predicting whether a district-month will cross into
a price-spike regime *next* month, using lagged rainfall trend,
seasonal features, and prior price volatility.

This is the layer that turns "we found an effect" into
"we can anticipate it."
"""

import numpy as np
import pandas as pd
from typing import Optional
import warnings
import logging
import json
from pathlib import Path

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    import shap
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import roc_auc_score, classification_report
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"


def engineer_spike_features(
    conn,
    commodity: str,
    state: str = None,
    lookback_months: int = 3,
) -> pd.DataFrame:
    """
    Engineer features for price-spike classification.
    
    Target: 1 if district-month has avg_modal_price in top quartile
    (price spike), 0 otherwise.
    
    Features:
    - lag_1m_departure, lag_2m_departure, lag_3m_departure (rainfall)
    - rolling_3m_avg_price
    - price_volatility (std/mean over lookback)
    - month (seasonal)
    - district (via encoding)
    """
    # Get monthly prices joined with rainfall
    from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
    district_map = load_district_subdivision_map()

    price_df = conn.execute("""
        SELECT
            state, district,
            EXTRACT(YEAR FROM arrival_date) AS year,
            EXTRACT(MONTH FROM arrival_date) AS month,
            AVG(modal_price) AS avg_modal_price,
            COUNT(*) AS n_observations
        FROM prices
        WHERE commodity = ? AND modal_price IS NOT NULL
        GROUP BY state, district, year, month
        HAVING COUNT(*) >= 3
        ORDER BY state, district, year, month
    """, [commodity]).fetchdf()

    if len(price_df) < 30:
        return pd.DataFrame()

    # Map district → sub-division for rainfall join
    price_df["sub_division"] = price_df.apply(
        lambda r: district_map.get((r["state"], r["district"]), None),
        axis=1
    )
    price_df = price_df.dropna(subset=["sub_division"])

    # Join with rainfall
    rainfall_df = conn.execute("SELECT * FROM rainfall").fetchdf()
    merged = price_df.merge(
        rainfall_df, on=["sub_division", "year", "month"], how="inner"
    )
    merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])

    if len(merged) < 30:
        return pd.DataFrame()

    # Sort by district + time for lag features
    merged = merged.sort_values(["state", "district", "year", "month"])

    # Create target: price spike = top quartile of avg_modal_price
    spike_threshold = merged["avg_modal_price"].quantile(0.75)
    merged["price_spike"] = (merged["avg_modal_price"] >= spike_threshold).astype(int)

    # Lag features for rainfall departure
    for lag in range(1, lookback_months + 1):
        merged[f"lag_{lag}m_departure"] = merged.groupby(["state", "district"])[
            "departure_pct"
        ].shift(lag)

    # Rolling price features
    merged["rolling_3m_avg_price"] = merged.groupby(["state", "district"])[
        "avg_modal_price"
    ].transform(lambda x: x.rolling(3, min_periods=1).mean())

    merged["price_volatility"] = merged.groupby(["state", "district"])[
        "avg_modal_price"
    ].transform(lambda x: x.rolling(3, min_periods=1).std() / x.rolling(3, min_periods=1).mean().replace(0, np.nan))

    # Month as feature
    merged["month_sin"] = np.sin(2 * np.pi * merged["month"] / 12)
    merged["month_cos"] = np.cos(2 * np.pi * merged["month"] / 12)

    # Drop NaN rows from lag creation
    merged = merged.dropna(subset=[f"lag_{lag}m_departure" for lag in range(1, lookback_months + 1)])

    return merged


def train_spike_classifier(
    conn,
    commodity: str,
    state: str = None,
    test_size: float = 0.2,
    random_state: int = 42,
) -> dict:
    """
    Train XGBoost classifier for price-spike prediction.
    
    Returns model artifacts, metrics, and SHAP values.
    """
    if not XGB_AVAILABLE:
        return {"error": "XGBoost not installed"}

    # Engineer features
    df = engineer_spike_features(conn, commodity=commodity, state=state)

    if len(df) < 50:
        return {"error": f"Insufficient feature rows: {len(df)}"}

    # Feature columns
    feature_cols = [
        "lag_1m_departure", "lag_2m_departure", "lag_3m_departure",
        "rolling_3m_avg_price", "price_volatility", "month_sin", "month_cos",
    ]
    
    # Add district as encoded feature
    district_encoded = pd.get_dummies(df["district"], prefix="district")
    # Keep only top 20 districts by frequency to avoid dimensionality issues
    top_districts = df["district"].value_counts().head(20).index
    for d in top_districts:
        feature_cols.append(f"district_{d}")
        df[f"district_{d}"] = (df["district"] == d).astype(int)

    # Ensure all feature columns exist
    available_features = [c for c in feature_cols if c in df.columns]
    X = df[available_features].fillna(0)
    y = df["price_spike"].values

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )

    # Handle class imbalance
    neg_count = (y_train == 0).sum()
    pos_count = (y_train == 1).sum()
    scale_pos_weight = neg_count / max(pos_count, 1)

    # Train XGBoost
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.1,
        scale_pos_weight=scale_pos_weight,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        eval_metric="auc",
        use_label_encoder=False,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    # Evaluate
    y_pred_proba = model.predict_proba(X_test)[:, 1]
    y_pred = model.predict(X_test)
    roc_auc = roc_auc_score(y_test, y_pred_proba)

    # SHAP analysis
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)

    # Feature importance
    feature_importance = pd.DataFrame({
        "feature": available_features,
        "importance": np.abs(shap_values).mean(axis=0),
    }).sort_values("importance", ascending=False)

    top_features = feature_importance.head(5).to_dict("records")

    # Save model
    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    model_path = Path(MODEL_DIR) / f"spike_classifier_{commodity.lower()}.json"
    model.save_model(str(model_path))

    result = {
        "commodity": commodity,
        "roc_auc": float(roc_auc),
        "n_training_rows": int(len(X_train)),
        "n_test_rows": int(len(X_test)),
        "top_features": json.dumps(top_features),
        "class_balance": {"neg": int(neg_count), "pos": int(pos_count)},
        "scale_pos_weight": float(scale_pos_weight),
        "feature_names": available_features,
        "model_path": str(model_path),
    }

    logger.info(f"Spike classifier for {commodity}: ROC-AUC={roc_auc:.4f}")

    return result


def predict_spike_risk(
    conn,
    commodity: str,
    district: str = None,
    state: str = None,
) -> dict:
    """
    Predict price-spike risk for next month for a district.
    
    Returns risk score (0-100) and top contributing features.
    """
    if not XGB_AVAILABLE:
        return {"error": "XGBoost not installed", "risk_score": 50}

    model_path = Path(MODEL_DIR) / f"spike_classifier_{commodity.lower()}.json"
    if not model_path.exists():
        return {"error": f"No trained model for {commodity}", "risk_score": 50}

    # Load model
    model = xgb.XGBClassifier()
    model.load_model(str(model_path))

    # Get latest features for this district
    df = engineer_spike_features(conn, commodity=commodity, state=state)

    if len(df) == 0:
        return {"error": "No feature data available", "risk_score": 50}

    # Filter to latest month per district
    latest = df.loc[df.groupby("district")["year"].idxmax()].copy()
    if district:
        latest = latest[latest["district"] == district]

    if len(latest) == 0:
        return {"error": f"No data for district: {district}", "risk_score": 50}

    # Feature columns (must match training)
    feature_cols = model.get_booster().feature_names
    if not feature_cols:
        return {"error": "Model has no feature names", "risk_score": 50}

    # Build feature vector
    X_pred = pd.DataFrame(index=latest.index)
    for col in feature_cols:
        if col in latest.columns:
            X_pred[col] = latest[col].values
        else:
            X_pred[col] = 0  # Missing feature

    X_pred = X_pred.fillna(0)

    # Predict
    proba = model.predict_proba(X_pred)[:, 1]
    latest["risk_score"] = (proba * 100).round(1)

    # Top risks
    top_risks = latest.nlargest(5, "risk_score")[
        ["state", "district", "year", "month", "risk_score", "departure_pct"]
    ]

    return {
        "commodity": commodity,
        "overall_risk": float(latest["risk_score"].mean()),
        "max_risk": float(latest["risk_score"].max()),
        "min_risk": float(latest["risk_score"].min()),
        "n_districts_analyzed": int(len(latest)),
        "top_5_risk_districts": top_risks.to_dict("records"),
        "roc_auc": None,  # Would be loaded from stored result
    }
