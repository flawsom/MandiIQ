"""
MandiRDD — Prescriptive Procurement Risk Advisor.

Combines the RDD effect size (how much prices jump), the classifier's
risk score (how likely a jump is next month), and the Prophet forecast
(expected price path) into one actionable recommendation.

Mirrors the role Superstore's discount optimizer played, adapted to
commodity procurement.

Output example:
  "MODERATE RISK (32%): Rainfall deficiency-driven price jump of ₹120–180
   expected in Nashik district next month. Current price ₹1,200 vs forecast
   ₹1,350. Consider locking procurement now rather than waiting."
"""

import pandas as pd
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def compute_recommendation(
    conn,
    commodity: str = "Onion",
    district: str = None,
    state: str = None,
) -> dict:
    """
    Compute a procurement recommendation for a commodity/district.
    
    Combines:
    - RDD effect size (causal layer)
    - Classifier risk score (predictive layer)
    - Prophet forecast (forecasting layer)
    
    Returns a prescriptive recommendation string.
    """
    from mandi_rdd.analysis.rdd_engine import run_rdd
    from mandi_rdd.analysis.fixed_effects import run_fe_crosscheck
    from mandi_rdd.analysis.forecast import get_forecast_summary
    from mandi_rdd.analysis.classifier import predict_spike_risk

    recommendation = {
        "commodity": commodity,
        "district": district or "All",
        "state": state or "All",
        "rdd_effect": None,
        "fe_effect": None,
        "risk_score": None,
        "forecast_trend": None,
        "recommendation": "Insufficient data to generate recommendation.",
        "confidence": "low",
    }

    # 1. Get RDD effect
    rdd_result = run_rdd(conn, commodity=commodity, state=state)
    if rdd_result and rdd_result.get("effect") is not None:
        recommendation["rdd_effect"] = round(rdd_result["effect"], 2)
        recommendation["rdd_p_value"] = rdd_result.get("p_value")

    # 2. Get fixed-effects cross-check
    fe_result = run_fe_crosscheck(conn, commodity=commodity)
    if fe_result and fe_result.get("coefficient") is not None:
        recommendation["fe_effect"] = round(fe_result["coefficient"], 2)
        recommendation["fe_p_value"] = fe_result.get("p_value")

    # 3. Get risk score
    risk = predict_spike_risk(conn, commodity=commodity, district=district, state=state)
    if "error" not in risk:
        recommendation["risk_score"] = round(risk.get("overall_risk", 50), 1)
        recommendation["max_risk_score"] = round(risk.get("max_risk", 50), 1)
        recommendation["top_risks"] = risk.get("top_5_risk_districts", [])

    # 4. Get forecast trend
    forecast = get_forecast_summary(conn, commodity=commodity)
    if "error" not in forecast and forecast.get("forecast"):
        predictions = forecast["forecast"]
        if len(predictions) >= 2:
            current = predictions[0]["forecast"]
            future = predictions[-1]["forecast"]
            trend_pct = ((future - current) / current) * 100 if current > 0 else 0
            recommendation["current_price"] = round(current, 2)
            recommendation["forecast_price"] = round(future, 2)
            recommendation["forecast_trend_pct"] = round(trend_pct, 1)

    # 5. Generate recommendation text
    rec = _generate_recommendation_text(recommendation)
    recommendation["recommendation"] = rec["text"]
    recommendation["confidence"] = rec["confidence"]
    recommendation["action"] = rec["action"]

    return recommendation


def _generate_recommendation_text(data: dict) -> dict:
    """
    Generate human-readable recommendation from available data.
    
    The recommendation combines:
    - Causal evidence (RDD + FE)
    - Risk score (classifier)
    - Price trend (forecast)
    
    Returns dict with text, confidence, and action.
    """
    parts = []
    confidence = "low"
    evidence_count = 0

    # Check what data is available
    rdd_ok = data.get("rdd_effect") is not None
    fe_ok = data.get("fe_effect") is not None
    risk_ok = data.get("risk_score") is not None
    forecast_ok = data.get("forecast_trend_pct") is not None

    if rdd_ok:
        evidence_count += 1
    if fe_ok:
        evidence_count += 1
    if risk_ok:
        evidence_count += 1
    if forecast_ok:
        evidence_count += 1

    if evidence_count >= 3:
        confidence = "high"
    elif evidence_count >= 2:
        confidence = "moderate"
    else:
        confidence = "low"

    # Build recommendation text
    commodity = data.get("commodity", "commodity")
    district = data.get("district", "the region")

    # Risk level
    risk = data.get("risk_score") or 50
    if risk >= 70:
        risk_level = "HIGH"
    elif risk >= 40:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    # RDD direction
    rdd_direction = ""
    if rdd_ok:
        effect = data["rdd_effect"]
        if effect > 0:
            rdd_direction = f"prices historically jump by ₹{abs(effect):.0f} when crossing the -19% rainfall deficiency threshold"
        else:
            rdd_direction = f"prices historically drop by ₹{abs(effect):.0f} at the deficiency threshold"

    # FE corroboration
    fe_note = ""
    if fe_ok and rdd_ok:
        fe_effect = data["fe_effect"]
        if (fe_effect > 0 and data["rdd_effect"] > 0) or (fe_effect < 0 and data["rdd_effect"] < 0):
            fe_note = " (causal estimate corroborated by fixed-effects cross-check)"
        else:
            fe_note = " (note: fixed-effects cross-check shows a different direction — interpret with caution)"

    # Forecast direction
    forecast_note = ""
    if forecast_ok:
        trend = data["forecast_trend_pct"]
        current = data.get("current_price", 0)
        future = data.get("forecast_price", 0)
        if trend > 5:
            forecast_note = f"Price forecast shows an upward trend (₹{current:.0f} → ₹{future:.0f}, +{trend:.0f}%)"
        elif trend < -5:
            forecast_note = f"Price forecast shows a downward trend (₹{current:.0f} → ₹{future:.0f}, {trend:.0f}%)"
        else:
            forecast_note = f"Price forecast is stable (₹{current:.0f}, ±{abs(trend):.0f}%)"

    # Action
    if risk_level == "HIGH":
        if rdd_direction and "jump" in rdd_direction:
            action = "LOCK_PROCUREMENT"
            action_text = "Recommend locking procurement now to avoid expected price increase."
        else:
            action = "MONITOR"
            action_text = "High uncertainty — monitor weekly price data."
    elif risk_level == "MODERATE":
        if rdd_direction and "jump" in rdd_direction:
            action = "CONSIDER_EARLY_PROCUREMENT"
            action_text = "Consider partial advance procurement to hedge against potential price increase."
        else:
            action = "WATCH"
            action_text = "Conditions are evolving — review again next week."
    else:
        action = "NO_ACTION_NEEDED"
        action_text = "No urgent procurement action needed at this time."

    # Compile text
    parts.append(f"{risk_level} RISK ({risk:.0f}%)")
    parts.append(f"for {commodity} in {district}.")
    
    if rdd_direction:
        parts.append(rdd_direction.capitalize() + fe_note + ".")
    
    if forecast_note:
        parts.append(forecast_note + ".")
    
    parts.append(action_text)

    return {
        "text": " ".join(parts),
        "confidence": confidence,
        "action": action,
    }
