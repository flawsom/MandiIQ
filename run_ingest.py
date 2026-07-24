"""
MandiIQ — Daily NDVI + Data Ingestion Pipeline

Runs automatically every day at 03:00 UTC via GitHub Actions.
- Fetches fresh mandi prices, rainfall, and NDVI from Sentinel Hub
- Exports NDVI results to ndvi_latest.json (git-tracked)
- Commits all data changes back to the repo

Streamlit Cloud and Render pick up the latest data on their next deploy.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from mandi_rdd.ingestion.scheduler import run_ingestion

summary = run_ingestion()
print(f"\n{'='*60}")
print(f"Pipeline complete — {summary.get('status', 'unknown')}")
print(f"{'='*60}")
