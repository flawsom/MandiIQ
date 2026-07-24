"""
API client for the Streamlit dashboard.

Replaces direct model imports with HTTP calls to the deployed FastAPI service.
This decoupling is the key architectural change from v1 to v2.
"""

import os
import json
import time
from typing import Optional
import urllib.request
import urllib.error
import streamlit as st

# API URL: set via environment or default to localhost for development
API_URL = os.getenv("API_URL", "http://localhost:8000")


def api_available() -> bool:
    """Check if the API is reachable."""
    try:
        req = urllib.request.Request(f"{API_URL}/health", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as f:
            data = json.loads(f.read())
            return data.get("status") == "healthy"
    except Exception:
        return False


def predict_loss_risk(
    category: str,
    sub_category: str,
    region: str,
    segment: str,
    discount: float,
    quantity: int,
    ship_mode: str,
    shipping_delay: int = 4,
) -> dict:
    """Call the loss-risk prediction API."""
    payload = {
        "category": category,
        "sub_category": sub_category,
        "region": region,
        "segment": segment,
        "discount": discount,
        "quantity": quantity,
        "ship_mode": ship_mode,
        "shipping_delay": shipping_delay,
    }
    return _call_api("/predict/loss-risk", payload)


def predict_max_discount(
    category: str,
    sub_category: str,
    region: str,
    segment: str,
    quantity: int = 3,
    ship_mode: str = "Standard Class",
    shipping_delay: int = 4,
) -> dict:
    """Call the max-discount prediction API."""
    payload = {
        "category": category,
        "sub_category": sub_category,
        "region": region,
        "segment": segment,
        "quantity": quantity,
        "ship_mode": ship_mode,
        "shipping_delay": shipping_delay,
    }
    return _call_api("/predict/max-discount", payload)


def get_forecast() -> dict:
    """Call the forecast API."""
    try:
        req = urllib.request.Request(
            f"{API_URL}/forecast",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as f:
            return json.loads(f.read())
    except Exception as e:
        st.error(f"Forecast API error: {e}")
        return {"forecast": [], "better_model": "N/A", "prophet_mape": 0, "lstm_mape": 0}


def predict_crop_risk(
    state: str,
    district: str,
    crop: str,
    season: str,
    rainfall_deficit_pct: float = 0,
) -> dict:
    """Call the crop-risk prediction API."""
    payload = {
        "state": state,
        "district": district,
        "crop": crop,
        "season": season,
        "rainfall_deficit_pct": rainfall_deficit_pct,
    }
    return _call_api("/predict/crop-risk", payload)


def get_health() -> dict:
    """Call the health check API."""
    try:
        req = urllib.request.Request(
            f"{API_URL}/health",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as f:
            return json.loads(f.read())
    except Exception:
        return {"status": "unreachable", "model_loaded": False}


def _call_api(endpoint: str, payload: dict) -> dict:
    """Make an HTTP POST request to the API."""
    url = f"{API_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as f:
            result = json.loads(f.read())
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        st.error(f"API Error ({e.code}): {error_body}")
        return {"error": str(e), "detail": error_body}
    except urllib.error.URLError as e:
        st.error(f"Cannot reach API at {API_URL}. Is the server running?")
        return {"error": str(e)}
    except Exception as e:
        st.error(f"Unexpected error: {e}")
        return {"error": str(e)}
