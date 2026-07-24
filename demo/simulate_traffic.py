"""
Generate synthetic prediction traffic for the monitoring demo.

This script simulates real API usage so the monitoring view has data to display.
All traffic is synthetic — clearly labeled as such in the dashboard.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import random
import time
import warnings
warnings.filterwarnings("ignore")

from src.monitoring.request_logger import log_prediction, ensure_log_dir


def simulate_traffic(n_requests: int = 500, api_url: str = "http://localhost:8000"):
    """
    Generate synthetic prediction requests and log them.
    
    If the API is running, also sends real HTTP requests.
    Otherwise, just logs synthetic entries locally.
    """
    print(f"Simulating {n_requests} prediction requests...")
    ensure_log_dir()
    
    categories = ["Furniture", "Office Supplies", "Technology"]
    sub_categories = {
        "Furniture": ["Chairs & Chairmats", "Tables", "Bookcases", "Office Furnishings"],
        "Office Supplies": ["Binders", "Paper", "Storage", "Appliances"],
        "Technology": ["Phones", "Copiers", "Computers", "Accessories"],
    }
    regions = ["East", "West", "Central", "South", "Ontario", "Quebec", "Nunavut"]
    segments = ["Consumer", "Corporate", "Home Office", "Small Business"]
    ship_modes = ["Standard Class", "First Class", "Second Class", "Same Day"]
    
    import urllib.request
    import json
    
    for i in range(n_requests):
        cat = random.choice(categories)
        sub = random.choice(sub_categories[cat])
        
        features = {
            "category": cat,
            "sub_category": sub,
            "region": random.choice(regions),
            "segment": random.choice(segments),
            "discount": round(random.uniform(0, 0.5), 2),
            "quantity": random.randint(1, 15),
            "ship_mode": random.choice(ship_modes),
            "shipping_delay": random.randint(0, 10),
        }
        
        start = time.time()
        
        # Try to call the real API
        try:
            req_data = json.dumps(features).encode()
            req = urllib.request.Request(
                f"{api_url}/predict/loss-risk",
                data=req_data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as f:
                response = json.loads(f.read())
            latency = (time.time() - start) * 1000
        except Exception:
            # API not running — generate synthetic response
            latency = random.uniform(50, 500)
            response = {
                "loss_probability": round(random.uniform(0, 1), 4),
                "prediction": random.randint(0, 1),
                "top_3_shap": [("discount", 0.5), ("category", 0.3), ("region", 0.2)],
            }
        
        log_prediction(
            endpoint="/predict/loss-risk",
            input_features=features,
            output=response,
            latency_ms=round(latency, 2),
        )
        
        if (i + 1) % 100 == 0:
            print(f"  ... {i + 1}/{n_requests} requests simulated")
    
    print(f"Done! {n_requests} requests logged to monitoring_logs/predictions.csv")
    print("Run the monitoring dashboard to view results.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Simulate API traffic for monitoring demo")
    parser.add_argument("--n", type=int, default=500, help="Number of requests")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000")
    args = parser.parse_args()
    
    simulate_traffic(n_requests=args.n, api_url=args.api_url)
