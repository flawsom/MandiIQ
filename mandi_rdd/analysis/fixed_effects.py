"""
MandiRDD — Fixed-effects regression cross-check.

Reuses Superstore's causal analysis pattern (fixed-effects with
category/region dummies) but applied to the mandi price + rainfall data.

Secondary cross-check for the RDD: if RDD and fixed-effects roughly agree,
that's a much stronger claim than either alone.

Model: modal_price ~ rainfall_departure + district_dummies + month_dummies
"""
import numpy as np
import pandas as pd
from scipy import stats
import logging

logger = logging.getLogger(__name__)

try:
    from sklearn.linear_model import LinearRegression
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not installed")


def fixed_effects_regression(
    df: pd.DataFrame,
    price_col: str = "avg_modal_price",
    running_col: str = "departure_pct",
    entity_col: str = "district",
    time_col: str = "month",
    cutoff: float = -19.0,
) -> dict:
    """
    Fixed-effects regression of price on rainfall departure, controlling for
    district and month fixed effects.
    
    This is the same technique Superstore used for its own causal layer
    (fixed-effects of margin on discount tier + category + region), but
    here it's a validation check rather than the primary method.
    
    If the fixed-effects coefficient on `below_cutoff` is similar in sign
    and magnitude to the RDD effect, the two methods corroborate each other.
    
    Args:
        df: DataFrame with price, departure, district, month columns
        price_col: Column name for outcome (price)
        running_col: Column name for running variable (rainfall departure)
        entity_col: Column name for entity fixed effects (district)
        time_col: Column name for time fixed effects (month)
        cutoff: The rainfall deficiency cutoff (-19% by IMD classification)
    
    Returns:
        Dict with coefficient, p_value, std_error, r_squared
    """
    if not SKLEARN_AVAILABLE:
        return {"error": "scikit-learn not installed", "coefficient": None}

    df = df.copy()
    df = df.dropna(subset=[price_col, running_col, entity_col])
    
    if len(df) < 30:
        return {"error": f"Insufficient observations: {len(df)}", "coefficient": None}

    # Create treatment indicator: below_cutoff = 1 if departure < cutoff
    df["below_cutoff"] = (df[running_col] < cutoff).astype(int)
    
    # Also include the running variable itself as a control
    X_cols = ["below_cutoff", running_col]
    
    # Entity (district) fixed effects
    entity_dummies = pd.get_dummies(df[entity_col], prefix="entity", drop_first=True)
    
    # Time (month) fixed effects
    if time_col in df.columns:
        time_dummies = pd.get_dummies(df[time_col].astype(str), prefix="month", drop_first=True)
    else:
        time_dummies = pd.DataFrame(index=df.index)
    
    # Combine features — ensure all numeric
    X_parts = [df[X_cols].copy()]
    for dummies in [entity_dummies, time_dummies]:
        if len(dummies.columns) > 0:
            X_parts.append(dummies)
    
    X = pd.concat(X_parts, axis=1)
    # Ensure all numeric
    X = X.apply(pd.to_numeric, errors='coerce').fillna(0)
    y = df[price_col].values
    
    # Drop any remaining NaN
    mask = ~np.isnan(y)
    X = X[mask]
    y = y[mask]
    
    if len(X) < 30:
        return {"error": f"Insufficient after dropping NaN: {len(X)}", "coefficient": None}
    
    # Fit model
    model = LinearRegression()
    model.fit(X, y)
    
    # Coefficient on below_cutoff
    coef_idx = list(X.columns).index("below_cutoff")
    coefficient = model.coef_[coef_idx]
    
    # Standard error
    y_pred = model.predict(X)
    residuals = y - y_pred
    n, p = X.shape
    dof = max(n - p, 1)
    
    mse = np.sum(residuals ** 2) / dof
    X_np = X.values.astype(np.float64)
    try:
        XtX_inv = np.linalg.inv(X_np.T @ X_np + np.eye(p) * 1e-6)
        se = np.sqrt(mse * XtX_inv[coef_idx, coef_idx])
    except np.linalg.LinAlgError:
        se = 0.0
    
    # t-stat and p-value
    t_stat = coefficient / se if se > 0 else 0
    p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=dof))
    
    # R-squared
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r_squared = 1 - (ss_res / (ss_tot + 1e-10))
    
    return {
        "coefficient": float(coefficient),
        "std_error": float(se),
        "p_value": float(p_value),
        "t_stat": float(t_stat),
        "r_squared": float(r_squared),
        "n_observations": int(n),
        "n_districts": int(entity_dummies.shape[1] + 1),
        "interpretation": (
            f"Fixed-effects estimate: crossing the {cutoff}% threshold is associated "
            f"with a ₹{coefficient:.2f} change in modal price (p={p_value:.4f}). "
            f"R² = {r_squared:.3f}."
        ),
    }


def run_fe_crosscheck(
    conn,
    commodity: str,
    cutoff: float = -19.0,
) -> dict:
    """
    Run the fixed-effects cross-check for a commodity.
    Loads price + rainfall joined data, runs FE regression, returns result.
    """
    from mandi_rdd.ingestion.fetch_rainfall import load_district_subdivision_map
    
    # Get monthly average prices
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
        ORDER BY year, month
    """, [commodity]).fetchdf()
    
    if len(price_df) < 30:
        return {"error": f"Insufficient price data: {len(price_df)} monthly obs"}
    
    # Map districts to sub-divisions
    district_map = load_district_subdivision_map()
    price_df["sub_division"] = price_df.apply(
        lambda r: district_map.get((r["state"], r["district"]), None), axis=1
    )
    price_df = price_df.dropna(subset=["sub_division"])
    
    if len(price_df) < 30:
        return {"error": f"Insufficient after district mapping: {len(price_df)}"}
    
    # Join with rainfall
    rainfall_df = conn.execute("SELECT * FROM rainfall").fetchdf()
    merged = price_df.merge(
        rainfall_df, on=["sub_division", "year", "month"], how="inner"
    )
    merged = merged.dropna(subset=["departure_pct", "avg_modal_price"])
    
    if len(merged) < 30:
        return {"error": f"Insufficient after rainfall join: {len(merged)}"}
    
    # Run FE regression
    fe_result = fixed_effects_regression(
        merged,
        price_col="avg_modal_price",
        running_col="departure_pct",
        entity_col="district",
        time_col="month",
        cutoff=cutoff,
    )
    
    fe_result["commodity"] = commodity
    fe_result["n_matched_observations"] = len(merged)
    
    return fe_result
