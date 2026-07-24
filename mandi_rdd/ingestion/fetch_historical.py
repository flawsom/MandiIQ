"""Fetch historical Agmarknet / APMC mandi prices for backfill.

WHY THIS EXISTS
================
The live data.gov.in mandi-prices resource (9ef84268-...) is a DAILY
SNAPSHOT -- it only ever returns the current day. That means the nightly
scheduler can append one day at a time but can NEVER build the multi-month
history that the RDD Effect and Forecast MAPE KPIs require.

The historical archive lives on the data.gov.in DATASET page as a bulk
CSV/ZIP download (or as a separate datastore resource). This script pulls
that bulk file into `data/historical/` so the existing
`ingest_historical_csv.py` (run by the nightly workflow) can ingest it
automatically.

USAGE
=====
    # 1. From a direct bulk-download URL (copy the "Downloads" link on the
    #    data.gov.in dataset page):
    python -m mandi_rdd.ingestion.fetch_historical \\
        --url "https://data.gov.in/sites/default/files/.../agmarknet_prices.zip" \\
        --out data/historical

    # 2. From a datastore resource id (bulk CSV export):
    python -m mandi_rdd.ingestion.fetch_historical \\
        --resource-id <historical-resource-uuid> --out data/historical

    # 3. From a dataset id (lists its downloadable resources, pulls the
    #    first CSV/ZIP):
    python -m mandi_rdd.ingestion.fetch_historical \\
        --dataset-id <dataset-uuid> --out data/historical

The API key is read from DATA_GOV_IN_API_KEY (or DATA_GOV_API_KEY).
In the nightly GitHub Action these are provided as secrets, so the
workflow can bootstrap history with zero manual steps.

After download, the nightly scheduler's backfill step ingests every
*.csv in data/historical/ and deletes it, so history is loaded once
and then kept fresh by the daily snapshot ingestion.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import logging
import urllib.request
import urllib.parse
import ssl
import zipfile

logger = logging.getLogger("mandi_rdd.fetch_historical")

API_BASE = "https://api.data.gov.in"
CHUNK = 1 << 16  # 64 KB


def _api_key() -> str:
    key = os.environ.get("DATA_GOV_IN_API_KEY") or os.environ.get("DATA_GOV_API_KEY")
    if not key:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY (or DATA_GOV_API_KEY) not set. "
            "Add it as a GitHub secret / local env var."
        )
    return key


def _get(url: str, binary: bool = False, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as f:
        data = f.read() if binary else f.read().decode("utf-8", "replace")
    return data


def _download_file(url: str, dest_path: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as f:
        with open(dest_path, "wb") as out:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
    logger.info(f"Downloaded {os.path.getsize(dest_path)} bytes -> {dest_path}")


def _extract_zip(zip_bytes: bytes, out_dir: str):
    """Extract CSVs from an in-memory zip; returns list of extracted paths."""
    extracted = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".csv"):
                target = os.path.join(out_dir, os.path.basename(name))
                with open(target, "wb") as fh:
                    fh.write(z.read(name))
                extracted.append(target)
                logger.info(f"Extracted {target}")
    return extracted


def fetch_url(url: str, out_dir: str) -> list:
    os.makedirs(out_dir, exist_ok=True)
    if url.lower().endswith(".zip"):
        data = _get(url, binary=True)
        return _extract_zip(data, out_dir)
    # direct csv
    fname = os.path.basename(urllib.parse.urlparse(url).path) or "historical.csv"
    dest = os.path.join(out_dir, fname)
    _download_file(url, dest)
    return [dest]


def fetch_resource(resource_id: str, out_dir: str, limit: int = 100000) -> list:
    """Bulk CSV export of a datastore resource via the datastore_search API."""
    os.makedirs(out_dir, exist_ok=True)
    key = _api_key()
    url = (
        f"{API_BASE}/resource/{resource_id}"
        f"?api-key={key}&format=csv&limit={limit}&offset=0"
    )
    data = _get(url, binary=True)
    dest = os.path.join(out_dir, f"{resource_id}.csv")
    with open(dest, "wb") as fh:
        fh.write(data)
    logger.info(f"Resource {resource_id} -> {dest}")
    return [dest]


def fetch_dataset(dataset_id: str, out_dir: str) -> list:
    """List a dataset's resources and pull the first CSV/ZIP download."""
    os.makedirs(out_dir, exist_ok=True)
    key = _api_key()
    url = f"{API_BASE}/dataset/{dataset_id}?api-key={key}"
    try:
        html = _get(url)
    except Exception as e:
        logger.warning(f"Could not fetch dataset page {dataset_id}: {e}")
        return []
    # Best-effort: pull any .csv/.zip href and download the first
    import re
    links = re.findall(r'href="([^"]+\.(?:csv|zip))"', html)
    if not links:
        logger.warning("No CSV/ZIP links found on dataset page.")
        return []
    out = []
    for lnk in links[:5]:
        full = lnk if lnk.startswith("http") else f"https://data.gov.in{lnk}"
        try:
            out += fetch_url(full, out_dir)
            break
        except Exception as e:
            logger.warning(f"Failed {full}: {e}")
    return out


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Fetch historical Agmarknet bulk prices.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="Direct bulk CSV/ZIP download URL.")
    src.add_argument("--resource-id", help="data.gov.in datastore resource UUID (bulk CSV).")
    src.add_argument("--dataset-id", help="data.gov.in dataset UUID (pulls first CSV/ZIP).")
    p.add_argument("--out", default="data/historical", help="Output folder.")
    p.add_argument("--limit", type=int, default=100000, help="Rows for resource export.")
    args = p.parse_args(argv)

    if args.url:
        files = fetch_url(args.url, args.out)
    elif args.resource_id:
        files = fetch_resource(args.resource_id, args.out, limit=args.limit)
    else:
        files = fetch_dataset(args.dataset_id, args.out)

    if files:
        print(f"Fetched {len(files)} file(s) into {args.out}:")
        for f in files:
            print(f"  - {f}")
        print("The nightly scheduler will ingest these automatically on next run.")
        return 0
    print("No files fetched.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
