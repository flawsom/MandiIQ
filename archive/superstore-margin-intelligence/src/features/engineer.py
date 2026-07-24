"""
Feature engineering for the Superstore Margin Intelligence System.

Adds derived columns used by models, dashboards, and analysis.
"""

import pandas as pd
import numpy as np


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add engineered features:
    - profit_margin: profit / sales (as percentage)
    - discount_tier: binned discount levels
    - order_month, order_year, order_quarter
    - is_loss: binary flag for unprofitable orders
    - log_sales: log-transformed sales
    """
    df = df.copy()

    # Profit margin (as percentage)
    df["profit_margin"] = (df["profit"] / df["sales"]) * 100

    # Discount tier buckets
    def discount_tier(d):
        if d == 0:
            return "0%"
        elif d <= 0.20:
            return "1-20%"
        elif d <= 0.40:
            return "21-40%"
        else:
            return "41%+"

    df["discount_tier"] = df["discount"].apply(discount_tier)

    # Time-based features
    df["order_month"] = df["order_date"].dt.month
    df["order_year"] = df["order_date"].dt.year
    df["order_quarter"] = df["order_date"].dt.quarter
    df["order_month_name"] = df["order_date"].dt.strftime("%b")
    df["order_year_month"] = df["order_date"].dt.to_period("M").astype(str)

    # Loss flag (target for classifier)
    df["is_loss"] = (df["profit"] < 0).astype(int)

    # Log sales for normalization
    df["log_sales"] = np.log1p(df["sales"])

    # Shipping delay category
    def delay_cat(days):
        if days <= 2:
            return "Fast"
        elif days <= 5:
            return "Normal"
        else:
            return "Slow"

    df["shipping_delay_cat"] = df["shipping_delay"].apply(delay_cat)

    return df


def get_feature_columns():
    """Return the list of feature columns used for modeling."""
    return [
        "category",
        "sub_category",
        "region",
        "segment",
        "discount",
        "quantity",
        "ship_mode",
        "shipping_delay",
    ]


def get_target_column():
    """Return the target column name."""
    return "is_loss"
