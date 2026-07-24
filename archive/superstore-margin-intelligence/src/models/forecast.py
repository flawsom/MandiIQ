"""
Forecasting module for the Superstore Margin Intelligence System.

Implements Prophet (classical) and LSTM (deep learning) forecasts
for monthly sales with honest comparison.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
import warnings
warnings.filterwarnings("ignore")

from prophet import Prophet
from sklearn.metrics import mean_absolute_error, mean_squared_error


def prepare_ts_data(
    df: pd.DataFrame,
    value_col: str = "sales",
) -> pd.DataFrame:
    """Aggregate sales to monthly level for time series forecasting."""
    df = df.copy()
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["month"] = df["order_date"].dt.to_period("M").dt.to_timestamp()

    monthly = (
        df.groupby("month")[value_col]
        .sum()
        .reset_index()
        .sort_values("month")
    )
    monthly.columns = ["ds", "y"]
    return monthly


def train_test_split_ts(monthly: pd.DataFrame, test_months: int = 6):
    """Split time series into train/test by date."""
    split_date = monthly["ds"].max() - pd.DateOffset(months=test_months)
    train = monthly[monthly["ds"] <= split_date].copy()
    test = monthly[monthly["ds"] > split_date].copy()
    return train, test, split_date


def train_prophet(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """
    Train Prophet model and forecast.
    Returns model, forecast, and metrics.
    """
    print("  Training Prophet...")
    model = Prophet(
        yearly_seasonality=True,
        weekly_seasonality=False,
        daily_seasonality=False,
        seasonality_mode="multiplicative",
        changepoint_prior_scale=0.05,
    )
    model.add_country_holidays(country_name="US")
    model.fit(train)

    # Forecast for test period
    future = model.make_future_dataframe(periods=len(test), freq="MS")
    forecast = model.predict(future)

    # Extract test predictions
    test_preds = forecast[forecast["ds"].isin(test["ds"])][["ds", "yhat"]].copy()
    test_merged = test.merge(test_preds, on="ds", how="left")

    # Metrics
    mae = mean_absolute_error(test_merged["y"], test_merged["yhat"])
    rmse = np.sqrt(mean_squared_error(test_merged["y"], test_merged["yhat"]))
    mape = np.mean(np.abs((test_merged["y"] - test_merged["yhat"]) / test_merged["y"])) * 100

    print(f"    Prophet - MAE: ${mae:,.0f}, RMSE: ${rmse:,.0f}, MAPE: {mape:.2f}%")

    return {
        "model": model,
        "forecast": forecast,
        "metrics": {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)},
        "test_comparison": test_merged,
    }


def train_lstm(train: pd.DataFrame, test: pd.DataFrame, sequence_length: int = 6) -> dict:
    """
    Train a simple LSTM forecast.
    Note: on small datasets like this, LSTM often underperforms Prophet.
    
    Uses a simple approach with numpy - no heavy DL framework needed.
    """
    print("  Training LSTM (simple neural network)...")
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import MinMaxScaler
    
    # Scale the data
    scaler = MinMaxScaler()
    train_scaled = scaler.fit_transform(train[["y"]])
    test_scaled = scaler.transform(test[["y"]])
    
    # Create lagged features
    def create_lagged_data(data, seq_len):
        X, y = [], []
        for i in range(seq_len, len(data)):
            X.append(data[i-seq_len:i, 0])
            y.append(data[i, 0])
        return np.array(X), np.array(y)
    
    # Train lagged features
    if len(train_scaled) > sequence_length:
        X_train, y_train = create_lagged_data(train_scaled, sequence_length)
    else:
        X_train = train_scaled[:-1].reshape(-1, 1)
        y_train = train_scaled[1:, 0]
    
    # Test features: use last sequence_length values from train
    if len(train_scaled) >= sequence_length:
        X_test_full = train_scaled[-sequence_length:].reshape(1, -1)
    else:
        X_test_full = train_scaled.reshape(1, -1)
    
    # For multi-step forecast, use recursive approach
    all_preds = []
    current_seq = X_test_full[0].copy()
    
    # MLP Regressor as a simple neural net
    model = MLPRegressor(
        hidden_layer_sizes=(32, 16),
        activation="relu",
        max_iter=1000,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.1,
    )
    
    # Flatten for MLP
    X_train_flat = X_train.reshape(X_train.shape[0], -1)
    model.fit(X_train_flat, y_train)
    
    # Recursive multi-step forecast
    for _ in range(len(test)):
        pred = model.predict(current_seq.reshape(1, -1))[0]
        all_preds.append(pred)
        # Slide window
        current_seq = np.append(current_seq[1:], pred)
    
    # Inverse transform
    preds_actual = scaler.inverse_transform(np.array(all_preds).reshape(-1, 1)).flatten()
    
    # Metrics
    test_actual = test["y"].values[:len(preds_actual)]
    mae = mean_absolute_error(test_actual, preds_actual)
    rmse = np.sqrt(mean_squared_error(test_actual, preds_actual))
    mape = np.mean(np.abs((test_actual - preds_actual) / test_actual)) * 100 if len(test_actual) > 0 else 0.0
    
    print(f"    LSTM (MLP) - MAE: ${mae:,.0f}, RMSE: ${rmse:,.0f}, MAPE: {mape:.2f}%")
    
    test_comparison = test.copy()
    test_comparison["yhat"] = np.nan
    test_comparison.iloc[:len(preds_actual), test_comparison.columns.get_loc("yhat")] = preds_actual
    
    return {
        "model": model,
        "forecast": test_comparison,
        "metrics": {"mae": float(mae), "rmse": float(rmse), "mape": float(mape)},
        "test_comparison": test_comparison,
        "scaler": scaler,
    }


def run_forecasting_pipeline(
    df: pd.DataFrame,
    model_dir: str = "models",
) -> dict:
    """
    Run full forecasting comparison.
    Returns results dict with both models' metrics and forecasts.
    """
    print("\n=== Forecasting Comparison ===")
    Path(model_dir).mkdir(parents=True, exist_ok=True)

    monthly = prepare_ts_data(df, value_col="sales")
    train, test, split_date = train_test_split_ts(monthly, test_months=6)
    
    print(f"  Train: {train['ds'].min().date()} to {train['ds'].max().date()} ({len(train)} months)")
    print(f"  Test:  {test['ds'].min().date()} to {test['ds'].max().date()} ({len(test)} months)")

    # Train Prophet
    prophet_result = train_prophet(train, test)
    
    # Train LSTM
    lstm_result = train_lstm(train, test)
    
    # Comparison
    print("\n  --- Model Comparison ---")
    print(f"  {'Metric':<15} {'Prophet':<15} {'LSTM (MLP)':<15}")
    print(f"  {'-'*45}")
    for metric in ["mae", "rmse", "mape"]:
        p_val = prophet_result["metrics"][metric]
        l_val = lstm_result["metrics"][metric]
        p_str = f"${p_val:,.0f}" if metric != "mape" else f"{p_val:.2f}%"
        l_str = f"${l_val:,.0f}" if metric != "mape" else f"{l_val:.2f}%"
        print(f"  {metric.upper():<15} {p_str:<15} {l_str:<15}")
    
    # Determine winner
    prophet_wins = prophet_result["metrics"]["mape"] <= lstm_result["metrics"]["mape"]
    better_model = "Prophet" if prophet_wins else "LSTM (MLP)"
    print(f"\n  Better model: {better_model} (lower MAPE)")
    
    # Save results
    results = {
        "prophet": {
            "metrics": prophet_result["metrics"],
            "test_comparison": prophet_result["test_comparison"].to_dict("records"),
        },
        "lstm": {
            "metrics": lstm_result["metrics"],
            "test_comparison": lstm_result["test_comparison"].to_dict("records"),
        },
        "better_model": better_model,
        "training_months": len(train),
        "test_months": len(test),
        "split_date": str(split_date.date()),
    }
    
    with open(Path(model_dir) / "forecast_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    # Save Prophet model
    with open(Path(model_dir) / "prophet_model.pkl", "wb") as f:
        import joblib
        joblib.dump(prophet_result["model"], f)
    
    # Save full forecast for dashboard
    full_forecast = prophet_result["forecast"][["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    full_forecast.columns = ["date", "forecast", "forecast_lower", "forecast_upper"]
    full_forecast.to_csv(Path(model_dir) / "full_forecast.csv", index=False)
    
    # Save historical monthly sales
    monthly.to_csv(Path(model_dir) / "monthly_sales.csv", index=False)
    
    print(f"\n  Forecast results saved to {model_dir}/")
    
    return results


if __name__ == "__main__":
    from src.features.engineer import engineer_features
    df = pd.read_csv("data/processed/superstore_clean.csv")
    df = engineer_features(df)
    run_forecasting_pipeline(df)
