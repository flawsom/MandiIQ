"""Pydantic schemas for request/response validation and OpenAPI docs."""

from pydantic import BaseModel, Field
from typing import List, Optional, Tuple
from datetime import datetime


# ── Predict Loss Risk ──

class LossRiskRequest(BaseModel):
    category: str = Field(..., example="Furniture", description="Product category")
    sub_category: str = Field(..., example="Chairs & Chairmats", description="Product sub-category")
    region: str = Field(..., example="Ontario", description="Region")
    segment: str = Field(..., example="Consumer", description="Customer segment")
    discount: float = Field(..., ge=0, le=1, example=0.15, description="Discount as decimal (0-1)")
    quantity: int = Field(..., ge=1, example=3, description="Order quantity")
    ship_mode: str = Field(..., example="Standard Class", description="Shipping mode")
    shipping_delay: int = Field(..., ge=0, example=4, description="Days between order and ship")


class LossRiskResponse(BaseModel):
    loss_probability: float = Field(..., ge=0, le=1, example=0.73, description="Predicted probability of order being unprofitable")
    prediction: int = Field(..., ge=0, le=1, example=1, description="Binary prediction: 0=profitable, 1=loss")
    top_3_shap: List[Tuple[str, float]] = Field(..., description="Top 3 SHAP features contributing to prediction")


# ── Max Discount ──

class MaxDiscountRequest(BaseModel):
    category: str = Field(..., example="Furniture")
    sub_category: str = Field(..., example="Chairs & Chairmats")
    region: str = Field(..., example="Ontario")
    segment: str = Field(..., example="Consumer")
    quantity: int = Field(default=3, ge=1, example=3)
    ship_mode: str = Field(default="Standard Class", example="Standard Class")
    shipping_delay: int = Field(default=4, ge=0, example=4)


class MaxDiscountResponse(BaseModel):
    safe_discount_pct: float = Field(..., example=5.0, description="Max safe discount percentage")
    current_loss_risk: float = Field(..., example=85.3, description="Loss risk at 15pp higher discount")
    safe_loss_risk: float = Field(..., example=45.1, description="Loss risk at recommended discount")
    discount_scan: List[dict] = Field(..., description="Full discount scan results")


# ── Forecast ──

class ForecastPoint(BaseModel):
    date: str = Field(..., example="2024-01-01")
    forecast: float = Field(..., example=120000.0)
    forecast_lower: float = Field(..., example=105000.0)
    forecast_upper: float = Field(..., example=135000.0)


class ForecastResponse(BaseModel):
    forecast: List[ForecastPoint]
    better_model: str = Field(..., example="Prophet")
    prophet_mape: float = Field(..., example=12.78)
    lstm_mape: float = Field(..., example=15.10)


# ── Crop Yield Risk ──

class CropRiskRequest(BaseModel):
    state: str = Field(..., example="Punjab")
    district: str = Field(..., example="Ludhiana")
    crop: str = Field(..., example="Rice")
    season: str = Field(..., example="Kharif")
    rainfall_deficit_pct: float = Field(default=0, example=15.0)


class CropRiskResponse(BaseModel):
    yield_collapse_probability: float = Field(..., ge=0, le=1)
    expected_yield: float = Field(..., example=3.5)
    risk_score: float = Field(..., ge=0, le=100)
    recommendation: str = Field(...)


# ── Health ──

class HealthResponse(BaseModel):
    status: str = Field("healthy")
    model_loaded: bool
    model_metrics: Optional[dict] = None
    forecast_available: bool
    crop_model_available: bool
