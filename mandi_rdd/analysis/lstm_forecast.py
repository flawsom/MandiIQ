"""
MandiRDD — LSTM forecast for honest comparison with Prophet.

Superstore's key forecasting finding: Prophet beats LSTM on small datasets.
This is the MandiRDD version — same comparison, repointed at modal_price.

Reports both MAPEs honestly. In an interview: "we let both models speak,
reported what they said, and chose the one that earned it on this data."
"""

import numpy as np
import pandas as pd
from typing import Optional
import warnings
import logging

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

try:
    from sklearn.preprocessing import MinMaxScaler
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    torch = None
    nn = None

# Guard class definition — only define if torch is available
if TORCH_AVAILABLE:
    class PriceLSTM(nn.Module):
        """Simple LSTM for price forecasting — 1 layer, 32 hidden units."""

        def __init__(self, input_size=1, hidden_size=32, num_layers=1):
            super().__init__()
            self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True)
            self.linear = nn.Linear(hidden_size, 1)

        def forward(self, x):
            lstm_out, _ = self.lstm(x)
            return self.linear(lstm_out[:, -1, :])
else:
    PriceLSTM = None  # Placeholder for graceful fallback


def create_sequences(data: np.ndarray, seq_length: int = 12):
    """Create (X, y) sequences for time series forecasting."""
    X, y = [], []
    for i in range(len(data) - seq_length):
        X.append(data[i:i + seq_length])
        y.append(data[i + seq_length])
    return np.array(X), np.array(y)


def train_lstm_forecast(
    monthly_prices: np.ndarray,
    seq_length: int = 12,
    epochs: int = 100,
    test_months: int = 3,
) -> dict:
    """
    Train LSTM forecast and compare with naive baseline.

    Args:
        monthly_prices: Array of monthly modal prices
        seq_length: Number of months to look back
        epochs: Training epochs
        test_months: Number of months to hold out for testing

    Returns:
        Dict with train_mae, test_mae, forecast, and comparison metrics
    """
    if not TORCH_AVAILABLE:
        return {"error": "PyTorch not installed", "mape": None}

    if not SKLEARN_AVAILABLE:
        return {"error": "sklearn not installed", "mape": None}

    if len(monthly_prices) < seq_length + test_months + 5:
        return {"error": f"Insufficient data: {len(monthly_prices)} months"}

    # Scale data
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(monthly_prices.reshape(-1, 1)).flatten()

    # Train/test split
    train_end = len(scaled) - test_months - seq_length
    train_data = scaled[:train_end + seq_length]
    test_data = scaled[train_end:]

    # Create sequences
    X_train, y_train = create_sequences(train_data, seq_length)
    X_test, y_test = create_sequences(test_data, seq_length)

    if len(X_train) < 5 or len(X_test) < 1:
        return {"error": "Insufficient sequences"}

    # Convert to tensors
    X_train_t = torch.FloatTensor(X_train).reshape(-1, seq_length, 1)
    y_train_t = torch.FloatTensor(y_train).reshape(-1, 1)
    X_test_t = torch.FloatTensor(X_test).reshape(-1, seq_length, 1)
    y_test_t = torch.FloatTensor(y_test).reshape(-1, 1)

    # Model
    model = PriceLSTM()
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

    # Train
    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        output = model(X_train_t)
        loss = criterion(output, y_train_t)
        loss.backward()
        optimizer.step()

    # Evaluate
    model.eval()
    with torch.no_grad():
        train_pred = model(X_train_t).numpy().flatten()
        test_pred = model(X_test_t).numpy().flatten()

    # Inverse scale
    train_pred_actual = scaler.inverse_transform(train_pred.reshape(-1, 1)).flatten()
    test_pred_actual = scaler.inverse_transform(test_pred.reshape(-1, 1)).flatten()

    train_actual = scaler.inverse_transform(y_train.reshape(-1, 1)).flatten()
    test_actual = scaler.inverse_transform(y_test.reshape(-1, 1)).flatten()

    # Metrics
    train_mae = np.mean(np.abs(train_pred_actual - train_actual))
    test_mae = np.mean(np.abs(test_pred_actual - test_actual))

    # MAPE
    train_mape = np.mean(np.abs((train_pred_actual - train_actual) / (train_actual + 1e-10))) * 100
    test_mape = np.mean(np.abs((test_pred_actual - test_actual) / (test_actual + 1e-10))) * 100
    test_rmse = np.sqrt(np.mean((test_pred_actual - test_actual) ** 2))

    # Naive baseline: forecast = last observed value
    naive_pred = np.full_like(test_actual, train_actual[-1])
    naive_mae = np.mean(np.abs(naive_pred - test_actual))
    naive_mape = np.mean(np.abs((naive_pred - test_actual) / (test_actual + 1e-10))) * 100

    # Generate forecast for next 6 months
    last_seq = scaled[-seq_length:].reshape(1, seq_length, 1)
    future_preds = []
    model.eval()
    with torch.no_grad():
        current_seq = torch.FloatTensor(last_seq)
        for _ in range(6):
            pred = model(current_seq).item()
            future_preds.append(pred)
            # Slide window
            current_seq = torch.cat([
                current_seq[:, 1:, :],
                torch.FloatTensor([[[pred]]])
            ], dim=1)

    future_preds_actual = scaler.inverse_transform(
        np.array(future_preds).reshape(-1, 1)
    ).flatten()

    return {
        "model": "LSTM",
        "train_mae": float(train_mae),
        "test_mae": float(test_mae),
        "test_rmse": float(test_rmse),
        "test_mape": float(test_mape),
        "naive_mae": float(naive_mae),
        "naive_mape": float(naive_mape),
        "forecast": future_preds_actual.tolist(),
        "train_actual": train_actual.tolist(),
        "test_actual": test_actual.tolist(),
        "test_predicted": test_pred_actual.tolist(),
        "n_train": len(X_train),
        "n_test": len(X_test),
    }


def compare_forecast_models(
    monthly_prices: np.ndarray,
    commodity: str,
) -> dict:
    """
    Run both Prophet and LSTM, return honest comparison.

    This is the same pattern Superstore used — report both MAPEs,
    pick the winner, explain why.
    """
    result = {
        "commodity": commodity,
        "prophet": {},
        "lstm": {},
        "better_model": None,
    }

    # LSTM
    lstm_result = train_lstm_forecast(monthly_prices)
    if "error" not in lstm_result and lstm_result.get("test_mape") is not None:
        result["lstm"] = {
            "test_mape": lstm_result["test_mape"],
            "test_mae": lstm_result["test_mae"],
            "beats_naive": lstm_result.get("test_mape", 999) < lstm_result.get("naive_mape", 999),
        }
    else:
        result["lstm"] = {"error": lstm_result.get("error", "Unknown error"), "test_mape": None}

    # Prophet (run separately via forecast.py)
    # Prophet's MAPE will be filled in by the caller

    return result
