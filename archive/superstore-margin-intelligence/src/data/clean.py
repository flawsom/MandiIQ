"""
Data cleaning pipeline for the Superstore Margin Intelligence System.

Ingests raw CSV, validates, deduplicates, and persists cleaned data.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime


def load_raw(path: str = "data/raw/Sample_-_Superstore.csv") -> pd.DataFrame:
    """Load raw Superstore CSV."""
    df = pd.read_csv(path, parse_dates=["Order Date", "Ship Date"])
    print(f"  Loaded {len(df):,} rows, {len(df.columns)} columns from {path}")
    return df


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean the Superstore dataset:
    - Rename columns to snake_case
    - Deduplicate on (Order ID, Product Name)
    - Parse dates and compute shipping delay
    - Drop rows with null profits
    - Validate date ranges
    """
    print(f"  Input shape: {df.shape}")

    # Standardize column names
    col_map = {
        "Row ID": "row_id",
        "Order ID": "order_id",
        "Order Date": "order_date",
        "Order Priority": "order_priority",
        "Order Quantity": "quantity",
        "Sales": "sales",
        "Discount": "discount",
        "Ship Mode": "ship_mode",
        "Profit": "profit",
        "Unit Price": "unit_price",
        "Shipping Cost": "shipping_cost",
        "Customer Name": "customer_name",
        "Province": "province",
        "Region": "region",
        "Customer Segment": "segment",
        "Product Category": "category",
        "Product Sub-Category": "sub_category",
        "Product Name": "product_name",
        "Product Container": "container",
        "Product Base Margin": "base_margin",
        "Ship Date": "ship_date",
    }
    df = df.rename(columns=col_map)

    # Convert order_id to string for consistent dedup
    df["order_id"] = df["order_id"].astype(str)

    # Deduplicate on order_id + product_name
    before_dedup = len(df)
    df = df.drop_duplicates(subset=["order_id", "product_name"])
    print(f"  Dedup: {before_dedup - len(df)} duplicate rows removed")

    # Ensure datetime
    df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
    df["ship_date"] = pd.to_datetime(df["ship_date"], errors="coerce")

    # Compute shipping delay
    df["shipping_delay"] = (df["ship_date"] - df["order_date"]).dt.days

    # Remove orders where ship precedes order (data errors)
    bad_dates = df["shipping_delay"] < 0
    if bad_dates.any():
        print(f"  Removing {bad_dates.sum()} rows with ship_date < order_date")
        df = df[~bad_dates]

    # Drop rows with null profit (cannot compute margin)
    null_profit = df["profit"].isna()
    if null_profit.any():
        print(f"  Dropping {null_profit.sum()} rows with null profit")
        df = df[~null_profit]

    # Ensure positive sales
    bad_sales = df["sales"] <= 0
    if bad_sales.any():
        print(f"  Dropping {bad_sales.sum()} rows with non-positive sales")
        df = df[~bad_sales]

    print(f"  Clean shape: {df.shape}")
    return df.reset_index(drop=True)


def run_pipeline(
    raw_path: str = "data/raw/Sample_-_Superstore.csv",
    clean_path: str = "data/processed/superstore_clean.csv",
) -> pd.DataFrame:
    """
    Run the full cleaning pipeline end-to-end.
    Returns the cleaned DataFrame and saves to disk.
    """
    print("=== Superstore Data Cleaning Pipeline ===")
    df = load_raw(raw_path)
    df = clean_data(df)

    # Save cleaned CSV
    Path(clean_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(clean_path, index=False)
    print(f"  Saved clean data to {clean_path}")

    return df


if __name__ == "__main__":
    run_pipeline()
