#!/usr/bin/env python3
"""
Superstore Margin Intelligence System — End-to-End Pipeline.

Run with: python run_pipeline.py
This executes: data cleaning -> feature engineering -> causal analysis -> model training -> forecasting -> optimization
"""

import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import warnings
warnings.filterwarnings("ignore")


def step_data_cleaning():
    """Layer 1: Data Engineering -- clean and validate."""
    print("\n" + "=" * 60)
    print("LAYER 1: DATA ENGINEERING")
    print("=" * 60)
    from src.data.clean import run_pipeline
    df = run_pipeline()
    from src.data.init_db import init_database
    init_database()
    return df


def step_feature_engineering(df):
    """Add engineered features for modeling."""
    print("\n" + "=" * 60)
    print("LAYER 1.5: FEATURE ENGINEERING")
    print("=" * 60)
    from src.features.engineer import engineer_features
    df = engineer_features(df)
    print(f"  Features added: discount_tier, profit_margin, order_month, order_year, is_loss")
    return df


def step_causal_analysis(df):
    """Layer 2: Causal Analysis -- fixed-effects regression + threshold analysis."""
    print("\n" + "=" * 60)
    print("LAYER 2: CAUSAL ANALYSIS")
    print("=" * 60)
    from src.models.causal import run_causal_pipeline
    results = run_causal_pipeline(df)
    return results


def step_classifier(df):
    """Layer 3: Predictive Modeling -- train loss classifier with SHAP."""
    print("\n" + "=" * 60)
    print("LAYER 3: PREDICTIVE MODELING")
    print("=" * 60)
    from src.models.classifier import train_classifier
    artifacts = train_classifier(df)
    return artifacts


def step_forecasting(df):
    """Layer 4: Forecasting -- Prophet vs LSTM comparison."""
    print("\n" + "=" * 60)
    print("LAYER 4: FORECASTING")
    print("=" * 60)
    from src.models.forecast import run_forecasting_pipeline
    results = run_forecasting_pipeline(df)
    return results


def step_optimization():
    """Layer 5: Prescriptive Optimization -- discount recommendations."""
    print("\n" + "=" * 60)
    print("LAYER 5: PRESCRIPTIVE OPTIMIZATION")
    print("=" * 60)
    from src.models.optimizer import batch_optimize
    recommendations = batch_optimize()
    print(f"  Generated {len(recommendations)} discount recommendations")
    return recommendations


def step_run_sql_validation():
    """Run SQL queries to validate database."""
    print("\n" + "=" * 60)
    print("VALIDATION: SQL QUERIES")
    print("=" * 60)
    from src.data.init_db import get_connection
    sql_dir = Path("sql")
    for sql_file in sorted(sql_dir.glob("*.sql")):
        print(f"  Running {sql_file.name}...")
        con = get_connection()
        try:
            result = con.execute(open(sql_file).read()).fetchdf()
            print(f"    -> {len(result)} rows returned")
        except Exception as e:
            print(f"    -> Error: {e}")
        con.close()


def run_all():
    """Execute the full pipeline end-to-end."""
    import time
    start = time.time()

    print("=" * 60)
    print("  SUPERSTORE MARGIN INTELLIGENCE SYSTEM")
    print("  End-to-End Pipeline")
    print("=" * 60)

    df = step_data_cleaning()
    df = step_feature_engineering(df)
    step_causal_analysis(df)
    step_classifier(df)
    step_forecasting(df)
    step_optimization()
    step_run_sql_validation()

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"  PIPELINE COMPLETE -- {elapsed:.1f} seconds")
    print("=" * 60)
    print("\n  Next steps:")
    print("    streamlit run dashboard/app.py")


if __name__ == "__main__":
    run_all()
