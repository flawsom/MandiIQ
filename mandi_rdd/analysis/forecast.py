"""
MandiRDD — Forecasting Layer.

Reuses Superstore's Prophet + LSTM comparison pattern, repointed at
modal_price time series per commodity/market.

Implementation is lightweight — just Prophet for the MVP, matching the
Superstore finding that classical models outperform deep learning on
smaller datasets.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
import logging
from typing import Optional
import warnings

warnings.filterwarnings("ignore")

logger = logging.getLogger(__name__)

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    logger.warning("Prophet not installed. Install with: pip install prophet")


def train_forecast(
    conn,
    commodity: str,
    state: Optional[str] = None,
    district: Optional[str] = None,
    periods: int = 6,
) -> dict:
    """
    Train a forecast model on a commodity's modal price time series.
    
    Uses Prophet if available, otherwise falls back to simple exponential
    smoothing (lightweight, no heavy dependencies).
    
    Args:
        conn: SQLite connection
        commodity: Commodity to forecast
        state: Optional state filter
        district: Optional district filter
        periods: Number of months to forecast
        
    Returns:
        Dict with forecast dataframe, model, and metrics
    """
    # Build query
    query = """
        SELECT arrival_date, AVG(modal_price) as modal_price
        FROM prices
        WHERE commodity = ?
    """
    params = [commodity]

    if state:
        query += " AND state = ?"
        params.append(state)
    if district:
        query += " AND district = ?"
        params.append(district)

    query += " GROUP BY arrival_date ORDER BY arrival_date"

    # DuckDB connection: use native execute + fetchdf (NOT pd.read_sql_query,
    # which expects a SQLAlchemy/SQLite connection and silently mishandles
    # DuckDB parameterized '?' queries -> empty frame -> no metrics).
    df = conn.execute(query, params).fetchdf()

    if len(df) < 20:
        return {"error": f"Insufficient data: {len(df)} days", "forecast": [], "metrics": {}}

    # Aggregate to monthly
    df["arrival_date"] = pd.to_datetime(df["arrival_date"])
    df["year_month"] = df["arrival_date"].dt.to_period("M").astype(str)
    
    monthly = df.groupby("year_month").agg(
        modal_price=("modal_price", "mean"),
        n_days=("modal_price", "count"),
    ).reset_index()
    
    monthly = monthly.sort_values("year_month")
    
    # Prepare Prophet data
    prophet_df = monthly.rename(columns={"year_month": "ds", "modal_price": "y"})
    prophet_df["ds"] = pd.to_datetime(prophet_df["ds"])
    
    if len(prophet_df) < 6:
        return {"error": f"Insufficient monthly data: {len(prophet_df)} months", "forecast": [], "metrics": {}}
    
    # Use Prophet if available, otherwise fallback to exponential smoothing
    if PROPHET_AVAILABLE:
        return _train_prophet(prophet_df, commodity, state, periods)
    else:
        return _train_exponential_smoothing(prophet_df, commodity, state, periods)


def _train_prophet(prophet_df, commodity, state, periods):
    """Train Prophet forecast (original implementation)."""
    # Train/test split: last 3 months for testing
    train = prophet_df[:-3] if len(prophet_df) > 6 else prophet_df
    test = prophet_df[-3:] if len(prophet_df) > 6 else prophet_df.iloc[-2:]
    
    # Train Prophet
    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
    )
    model.fit(train)
    
    # Forecast
    future = model.make_future_dataframe(periods=periods, freq="MS")
    forecast = model.predict(future)
    
    # Metrics on test set
    test_forecast = forecast[forecast["ds"].isin(test["ds"])]
    metrics = {}
    if len(test_forecast) > 0:
        merged = test.merge(test_forecast, on="ds", how="inner")
        if len(merged) > 0:
            mae = np.abs(merged["y"] - merged["yhat"]).mean()
            rmse = np.sqrt(((merged["y"] - merged["yhat"]) ** 2).mean())
            mape = (np.abs((merged["y"] - merged["yhat"]) / merged["y"]) * 100).mean()
            metrics = {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)}
    
    # Format forecast output
    forecast_out = []
    for _, row in forecast.iterrows():
        forecast_out.append({
            "date": str(row["ds"].date()),
            "forecast": float(row["yhat"]),
            "forecast_lower": float(row["yhat_lower"]),
            "forecast_upper": float(row["yhat_upper"]),
        })
    
    return {
        "commodity": commodity,
        "state": state or "All",
        "forecast": forecast_out,
        "metrics": metrics,
        "n_training_months": len(train),
        "n_test_months": len(test),
        "model": model,
    }


def _train_exponential_smoothing(prophet_df, commodity, state, periods):
    """Lightweight fallback: simple exponential smoothing with trend."""
    from scipy.optimize import minimize_scalar
    
    # Use log prices for stability
    y = prophet_df["y"].values
    n = len(y)
    
    # Train/test split
    train = y[:-3] if n > 6 else y
    test = y[-3:] if n > 6 else y[-2:]
    
    # Simple exponential smoothing with linear trend
    def forecast_with_alpha(alpha, beta=0.3):
        level = train[0]
        trend = (train[-1] - train[0]) / len(train) if len(train) > 1 else 0
        forecasts = []
        for t in range(len(train) + periods):
            if t < len(train):
                new_level = alpha * train[t] + (1 - alpha) * (level + trend)
                trend = beta * (new_level - level) + (1 - beta) * trend
                level = new_level
            else:
                forecasts.append(level + trend)
        return forecasts
    
    # Optimize alpha
    def mse(alpha):
        fc = forecast_with_alpha(alpha)[:-periods]
        if len(fc) != len(train):
            return 1e10
        return np.mean((np.array(fc) - train) ** 2)
    
    result = minimize_scalar(mse, bounds=(0.01, 0.99), method="bounded")
    alpha_opt = result.x
    
    # Generate forecast
    all_forecasts = forecast_with_alpha(alpha_opt)
    train_forecasts = all_forecasts[:-periods]
    future_forecasts = all_forecasts[-periods:]
    
    # Metrics on test set
    metrics = {}
    if len(train_forecasts) >= len(test):
        test_fc = train_forecasts[-len(test):]
        mae = np.abs(np.array(test) - np.array(test_fc)).mean()
        rmse = np.sqrt(((np.array(test) - np.array(test_fc)) ** 2).mean())
        mape = (np.abs((np.array(test) - np.array(test_fc)) / np.array(test)) * 100).mean()
        metrics = {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)}
    
    # Format forecast output
    last_date = prophet_df["ds"].max()
    forecast_out = []
    for i, fc_val in enumerate(future_forecasts):
        forecast_date = last_date + pd.DateOffset(months=i+1)
        # Simple confidence intervals
        std_err = np.std(np.array(train) - np.array(train_forecasts[-len(train):])) if len(train_forecasts) >= len(train) else np.std(train) * 0.1
        forecast_out.append({
            "date": str(forecast_date.date()),
            "forecast": float(fc_val),
            "forecast_lower": float(fc_val - 1.96 * std_err),
            "forecast_upper": float(fc_val + 1.96 * std_err),
        })
    
    return {
        "commodity": commodity,
        "state": state or "All",
        "forecast": forecast_out,
        "metrics": metrics,
        "n_training_months": len(train),
        "n_test_months": len(test),
        "model": None,  # No model object for fallback
        "method": "exponential_smoothing",
    }


def get_forecast_summary(conn, commodity: str) -> dict:
    """Get a forecast summary for the API response (no model object)."""
    result = train_forecast(conn, commodity=commodity)
    
    # Remove the model object (not JSON serializable)
    if "model" in result:
        del result["model"]
    
    return result


def compare_forecast_models(
    conn,
    commodity: str,
    state: Optional[str] = None,
    periods: int = 12,
) -> dict:
    """
    Run both Prophet and LSTM on the same data and return an honest comparison.
    
    Reports both MAPEs, picks the winner, and explains why — the same
    honest-comparison discipline Superstore pioneered.
    
    Args:
        conn: DuckDB connection
        commodity: Commodity to forecast
        state: Optional state filter
        periods: Forecast horizon in months
        
    Returns:
        Dict with prophet_metrics, lstm_metrics, better_model, explanation
    """
    # 1. Pull monthly price time series (same data for both models)
    df = conn.execute(
        """SELECT arrival_date, AVG(modal_price) as modal_price
           FROM prices
           WHERE commodity = ? AND modal_price IS NOT NULL
           GROUP BY arrival_date ORDER BY arrival_DATE""",
        [commodity],
    ).fetchdf()
    
    if len(df) < 30:
        return {
            "commodity": commodity,
            "error": f"Insufficient data for model comparison: {len(df)} daily records",
        }
    
    # Aggregate to monthly (same pattern as train_forecast)
    df["arrival_date"] = pd.to_datetime(df["arrival_date"])
    df["year_month"] = df["arrival_date"].dt.to_period("M").astype(str)
    monthly = df.groupby("year_month")["modal_price"].mean().reset_index()
    monthly = monthly.rename(columns={"modal_price": "price"})
    monthly_prices = monthly["price"].values
    
    if len(monthly_prices) < 12:
        return {
            "commodity": commodity,
            "error": f"Insufficient monthly data: {len(monthly_prices)} months",
        }
    
    # 2. Run Prophet
    prophet_result = train_forecast(conn, commodity=commodity, state=state, periods=periods)
    prophet_metrics = prophet_result.get("metrics", {})
    prophet_mape = prophet_metrics.get("mape")
    prophet_mae = prophet_metrics.get("mae")
    prophet_rmse = prophet_metrics.get("rmse")
    
    # 3. Run LSTM
    from mandi_rdd.analysis.lstm_forecast import train_lstm_forecast
    lstm_result = train_lstm_forecast(monthly_prices)
    
    lstm_mape = lstm_result.get("test_mape")
    lstm_mae = lstm_result.get("test_mae")
    lstm_rmse = lstm_result.get("test_rmse")
    lstm_error = lstm_result.get("error")
    
    # 4. Compare honestly
    comparison = {
        "commodity": commodity,
        "state": state or "All",
        "n_training_months": len(monthly_prices),
        "prophet": {
            "test_mape": prophet_mape,
            "test_mae": prophet_mae,
            "test_rmse": prophet_rmse,
            "available": prophet_mape is not None,
        },
        "lstm": {
            "test_mape": lstm_mape,
            "test_mae": lstm_mae,
            "test_rmse": lstm_rmse,
            "available": lstm_mape is not None and lstm_error is None,
            "error": lstm_error,
        },
        "better_model": None,
        "explanation": "",
    }
    
    # 5. Pick winner
    both_available = comparison["prophet"]["available"] and comparison["lstm"]["available"]
    
    if both_available and prophet_mape is not None and lstm_mape is not None:
        if prophet_mape < lstm_mape:
            comparison["better_model"] = "Prophet"
            comparison["explanation"] = (
                f"Prophet (MAPE: {prophet_mape:.1f}%) outperforms LSTM (MAPE: {lstm_mape:.1f}%) "
                f"on this dataset ({len(monthly_prices)} months). This matches the Superstore finding: "
                f"classical structural models generalize better than deep learning when training data "
                f"is limited. Prophet's seasonality decomposition captures the annual price cycle "
                f"more efficiently than LSTM's learned representations at this data scale."
            )
        elif lstm_mape < prophet_mape:
            comparison["better_model"] = "LSTM"
            comparison["explanation"] = (
                f"LSTM (MAPE: {lstm_mape:.1f}%) outperforms Prophet (MAPE: {prophet_mape:.1f}%) "
                f"on this dataset ({len(monthly_prices)} months). The LSTM captures non-linear "
                f"dependencies in the price series that Prophet's additive decomposition misses. "
                f"This is more likely with larger, more complex time series."
            )
        else:
            comparison["better_model"] = "Tie"
            comparison["explanation"] = (
                f"Prophet and LSTM produce nearly identical test error "
                f"(MAPE: {prophet_mape:.1f}% vs {lstm_mape:.1f}%). Either model is suitable "
                f"for this commodity, but Prophet is preferred for interpretability."
            )
    elif comparison["prophet"]["available"]:
        comparison["better_model"] = "Prophet"
        comparison["explanation"] = (
            f"Only Prophet produced a valid forecast (MAPE: {prophet_mape:.1f}%). "
            f"LSTM unavailable: {lstm_error or 'PyTorch not installed'}."
        )
    elif comparison["lstm"]["available"]:
        comparison["better_model"] = "LSTM"
        comparison["explanation"] = (
            f"Only LSTM produced a valid forecast (MAPE: {lstm_mape:.1f}%). "
            f"Prophet unavailable or insufficient data for its training/test split."
        )
    else:
        comparison["error"] = "Neither model produced a valid forecast"
    
    # 6. Include forecast chart data
    comparison["forecast"] = prophet_result.get("forecast", [])
    
    # 7. Include monthly time series for charting
    comparison["monthly_history"] = {
        "dates": monthly["arrival_date"].dt.strftime("%Y-%m-%d").tolist(),
        "prices": monthly_prices.tolist(),
    }
    
    # 8. Include LSTM future forecast if available
    if "forecast" in lstm_result and not isinstance(lstm_result.get("forecast"), list):
        pass  # LSTM forecast is already a list from train_lstm_forecast
    
    comparison["lstm"]["future_forecast"] = lstm_result.get("forecast", []) if lstm_error is None else []
    
    return comparison
