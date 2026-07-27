"""Sync local DuckDB from the live Northflank API, then pull RDD results.

Pulls /prices for the rain-sensitive RDD commodities, inserts them into
the local prices table, pulls RDD results from the API (which has rainfall
data), and saves them locally so the Streamlit dashboard shows real causal
estimates instead of the "Run the pipeline" placeholder.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests
import pandas as pd

API = "https://p01--mandiiq--x4n8x4gkmzht.code.run"
COMMODITIES = ["Onion", "Tomato", "Potato", "Cabbage", "Cauliflower"]

from mandi_rdd.storage.duckdb_store import get_connection, save_rdd_result, init_schema


def main():
    conn = get_connection()
    # Initialize schema if tables don't exist
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(prices)").fetchall()]
    except Exception:
        print("Initializing database schema...")
        init_schema(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(prices)").fetchall()]
    print("prices columns:", cols)
    insert_cols = [c2 for c2 in cols if c2 != "id"]

    frames = []
    # 1) Deep history for the RDD commodities
    for c in COMMODITIES:
        resp = requests.get(f"{API}/prices", params={"commodity": c, "limit": 5000}, timeout=60)
        resp.raise_for_status()
        rows = resp.json()
        print(f"{c}: {len(rows)} rows from API")
        if rows:
            frames.append(pd.DataFrame(rows))

    # 2) Broad unfiltered pull for commodity/district coverage
    resp = requests.get(f"{API}/prices", params={"limit": 5000}, timeout=60)
    resp.raise_for_status()
    broad = resp.json()
    print(f"broad pull: {len(broad)} rows, {len({r['commodity'] for r in broad})} commodities")
    if broad:
        frames.append(pd.DataFrame(broad))

    df = pd.concat(frames, ignore_index=True)
    for col in insert_cols:
        if col not in df.columns:
            df[col] = None
    df = df.drop_duplicates(subset=["state", "district", "market", "commodity", "arrival_date"])
    df = df[df["state"].notna() & (df["state"] != "")]

    conn.execute("DELETE FROM prices")
    conn.execute(
        f"INSERT INTO prices ({', '.join(insert_cols)}) SELECT {', '.join(insert_cols)} FROM df"
    )
    total = len(df)

    n, nc, nd = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT commodity), COUNT(DISTINCT district) FROM prices"
    ).fetchone()
    print(f"local prices: {n} rows | {nc} commodities | {nd} districts (inserted {total})")

    # 3) Pull RDD results from the API (prod has rainfall data)
    conn.execute("DELETE FROM rdd_results")
    rdd_count = 0
    for c in COMMODITIES:
        try:
            resp = requests.get(f"{API}/rdd-result/{c}", timeout=30)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("effect") is not None:
                    # Convert API response to the format expected by save_rdd_result
                    rdd_row = {
                        "commodity": c,
                        "effect": result["effect"],
                        "p_value": result["p_value"],
                        "std_error": result["std_error"],
                        "n_left": result["n_left"],
                        "n_right": result["n_right"],
                        "bandwidth": 20,  # default bandwidth
                        "cutoff": -19,  # default cutoff
                        "interpretation": result.get("interpretation", ""),
                    }
                    save_rdd_result(conn, rdd_row)
                    rdd_count += 1
                    print(f"RDD {c}: effect={result['effect']:.2f} p={result.get('p_value')}")
                else:
                    print(f"RDD {c}: no significant result")
            else:
                print(f"RDD {c}: not found on API (status {resp.status_code})")
        except Exception as e:
            print(f"RDD {c}: ERROR {e}")

    n_rdd = conn.execute("SELECT COUNT(*) FROM rdd_results").fetchone()[0]
    print(f"local rdd_results total: {n_rdd}")
    conn.close()


if __name__ == "__main__":
    main()
