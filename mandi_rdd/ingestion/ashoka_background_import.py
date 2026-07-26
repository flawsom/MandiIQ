"""
Resumable background Ashoka CEDA historical import.
Call via API endpoint; runs in a background thread on the Render web server
(no GitHub Actions timeout). Saves checkpoints so it survives restarts.

    GET  /api/historical-import-status  -> progress JSON
    POST /api/trigger-ashoka-import     -> starts/resumes the import
"""
from __future__ import annotations

import csv
import json
import logging
import os
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error

log = logging.getLogger("mandi_rdd.ashoka_bg")

BASE = "https://agmarknet.ceda.ashoka.edu.in"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
           "Accept": "application/json"}

START = "2010-01-01"
END = datetime.now().strftime("%Y-%m-%d")
FIELDNAMES = ["arrival_date", "state", "district", "market", "commodity",
              "variety", "grade", "min_price", "max_price", "modal_price"]

# Where checkpoints + output live (relative to project root)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "historical"
CHECKPOINT_FILE = DATA_DIR / "ashoka_checkpoint.json"
OUTPUT_CSV = DATA_DIR / "agmarknet_ashoka.csv"

_write_lock = threading.Lock()
_import_thread: Optional[threading.Thread] = None
_status = {"state": "idle", "progress": "", "cells_done": 0, "cells_total": 0,
           "rows_written": 0, "error": "", "started_at": "", "elapsed_sec": 0}


# ── API helpers (same as fetch_historical_ashoka.py) ──

def _get(path):
    with urllib.request.urlopen(
        urllib.request.Request(BASE + path, headers=HEADERS),
        timeout=25, context=CTX,
    ) as f:
        return json.loads(f.read()).get("data", [])


def _post(body):
    req = urllib.request.Request(
        BASE + "/api/prices",
        data=json.dumps(body).encode(), headers=HEADERS, method="POST",
    )
    with urllib.request.urlopen(req, timeout=45, context=CTX) as f:
        return json.loads(f.read()).get("data", [])


def fetch_cell(state_id: int, comm_id: int, dist_id: str) -> list[dict]:
    body = {
        "state_id": state_id,
        "commodity_id": comm_id,
        "district_id": str(dist_id),
        "calculation_type": "m",
        "start_date": START,
        "end_date": END,
    }
    try:
        rows = _post(body)
    except Exception:
        return []
    out = []
    for r in rows:
        t = r.get("t") or r.get("arrival_date")
        if not t:
            continue
        try:
            arrival = datetime.strptime(str(t)[:7], "%Y-%m").strftime("%Y-%m-%d")
        except Exception:
            arrival = str(t)
        cmdty = r.get("cmdty") or r.get("commodity") or ""
        dist = r.get("district") or r.get("district_name") or ""
        out.append({
            "arrival_date": arrival,
            "state": r.get("state") or r.get("state_name") or "",
            "district": dist,
            "market": dist,
            "commodity": cmdty,
            "variety": cmdty,
            "grade": cmdty,
            "min_price": r.get("p_min"),
            "max_price": r.get("p_max"),
            "modal_price": r.get("p_modal"),
        })
    return out


# ── Checkpoint helpers ──

def _load_checkpoint() -> set:
    """Return set of (state_id, comm_id, dist_id) cells already fetched."""
    if CHECKPOINT_FILE.exists():
        try:
            raw = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
            return {tuple(c) for c in raw.get("done_cells", [])}
        except Exception:
            pass
    return set()


def _save_checkpoint(done_cells: set[tuple]):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    serializable = [list(c) for c in done_cells]
    CHECKPOINT_FILE.write_text(
        json.dumps({"done_cells": serializable, "updated": datetime.utcnow().isoformat()}),
        encoding="utf-8",
    )


# ── Cell enumeration ──

def enumerate_all_cells() -> list[tuple[int, int, str]]:
    """List ALL (state_id, commodity_id, district_id) cells across all states.

    Returns ~state_count * district_avg * 453 commodities.
    """
    states = _get("/api/states")
    commodities = _get("/api/commodities")
    log.info(f"Enumerating cells: {len(states)} states, {len(commodities)} commodities")

    cells: list[tuple[int, int, str]] = []
    for s in states:
        sid = s["census_state_id"]
        try:
            dists = _get(f"/api/districts?state_id={sid}")
        except Exception:
            dists = []
        for d in dists:
            did = d.get("census_district_id")
            if not did:
                continue
            for c in commodities:
                cells.append((sid, c["commodity_id"], str(did)))
    return cells


# ── Background runner ──

def _run_import_in_bg(workers: int = 2, limit_cells: int = 0,
                      all_commodities: bool = True,
                      checkpoint_every: int = 10):
    """Fetch ALL available cells with resume support.

    If all_commodities=False, limits to the default 40 commodities by name match.
    """
    global _status
    start_ts = time.time()
    _status["state"] = "running"
    _status["started_at"] = datetime.utcnow().isoformat()
    _status["error"] = ""
    _status["elapsed_sec"] = 0

    try:
        # Decide which commodities to process
        if all_commodities:
            commodities = _get("/api/commodities")  # all 453
        else:
            raw = ("onion,potato,tomato,wheat,paddy,rice,maize,gram,arhar,moong,urd,"
                   "soybean,groundnut,mustard,sugarcane,cotton,jute,rapeseed,bajra,jwar,barley,"
                   "pea,garlic,ginger,turmeric,chilli,coriander,banana,mango,apple,cauliflower,"
                   "cabbage,brinjal,carrot,beetroot,radish,bitter gourd,bottle gourd,ridge gourd,"
                   "spinach,lady finger,green pea")
            names = {x.strip().lower() for x in raw.split(",")}
            commodities = [c for c in _get("/api/commodities")
                           if (c.get("commodity_disp_name") or "").lower() in names]

        log.info(f"Processing {len(commodities)} commodities")

        # Enumerate cells
        states = _get("/api/states")
        all_cells: list[tuple[int, int, str]] = []
        for s in states:
            sid = s["census_state_id"]
            try:
                dists = _get(f"/api/districts?state_id={sid}")
            except Exception:
                dists = []
            for d in dists:
                did = d.get("census_district_id")
                if not did:
                    continue
                for c in commodities:
                    all_cells.append((sid, c["commodity_id"], str(did)))

        if limit_cells > 0:
            all_cells = all_cells[:limit_cells]

        done = _load_checkpoint()
        pending = [c for c in all_cells if c not in done]
        total = len(all_cells)
        _status["cells_total"] = total
        _status["cells_done"] = len(done)
        log.info(f"Total cells: {total}, already done: {len(done)}, pending: {len(pending)}")

        if not pending:
            _status["state"] = "complete"
            _status["progress"] = f"All {total} cells already fetched."
            return

        # Open CSV in append mode if checkpoint exists, else write header
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        mode = "a" if OUTPUT_CSV.exists() else "w"
        rows_written = 0

        with open(OUTPUT_CSV, mode, newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            if mode == "w":
                writer.writeheader()

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(fetch_cell, s, c, d): (s, c, d) for s, c, d in pending}
                for fut in as_completed(futs):
                    cell = futs[fut]
                    cell_rows = fut.result()
                    total_done = len(done) + 1
                    done.add(cell)
                    rows_written += len(cell_rows)        # Save checkpoint periodically

                    if total_done % checkpoint_every == 0:
                        _save_checkpoint(done)

                    if cell_rows:
                        with _write_lock:
                            # Tag each row with Ashoka source metadata so the
                            # downstream backfill step can record lineage.
                            for cr in cell_rows:
                                cr["_source"] = {
                                    "source_type": "ashoka",
                                    "source_name": "agmarknet.ceda.ashoka.edu.in",
                                    "resource_id": f"state={cell[0]}_commodity={cell[1]}_district={cell[2]}",
                                }
                            writer.writerows(cell_rows)

                    elapsed = time.time() - start_ts
                    _status["cells_done"] = total_done
                    _status["rows_written"] = rows_written
                    _status["elapsed_sec"] = round(elapsed, 0)
                    if total_done % 500 == 0:
                        pct = total_done / total * 100
                        rate = total_done / elapsed if elapsed > 0 else 0
                        eta = (total - total_done) / rate if rate > 0 else 0
                        _status["progress"] = (
                            f"{total_done}/{total} cells ({pct:.1f}%), "
                            f"{rows_written} rows, {rate:.1f} cells/s, "
                            f"ETA {eta/60:.0f}min"
                        )
                        log.info(_status["progress"])

        # Final checkpoint
        _save_checkpoint(done)
        elapsed = time.time() - start_ts
        _status["state"] = "complete"
        _status["progress"] = (
            f"Done: {total} cells, {rows_written} rows in {elapsed/60:.0f}min"
        )
        _status["elapsed_sec"] = round(elapsed, 0)
        log.info(_status["progress"])

        # Step 2: run historical backfill to consume the CSV into DuckDB
        log.info("Running historical CSV backfill into DuckDB...")
        try:
            from mandi_rdd.ingestion.ingest_historical_csv import run_auto
            n_backfill = run_auto(folder=str(DATA_DIR))
            _status["backfill_rows"] = n_backfill
            log.info(f"Backfill ingested {n_backfill} rows")
        except Exception as e:
            log.warning(f"Backfill failed (can run later): {e}")
            _status["backfill_error"] = str(e)

        # Record overall Ashoka import lineage
        try:
            from mandi_rdd.storage.duckdb_store import record_lineage_batch
            record_lineage_batch(
                _get_test_conn(),
                source_type="ashoka",
                source_name="ashoka_full_import",
                resource_id=OUTPUT_CSV.name,
                row_count=rows_written,
                n_new=-1,
                records=None,
                metadata={"cells_total": total, "cells_done": len(done), "duration_sec": elapsed},
            )
        except Exception:
            pass

    except Exception as e:
        _status["state"] = "error"
        _status["error"] = str(e)
        log.error(f"Import failed: {e}")
        _save_checkpoint(done)


# ── Public API ──

def get_status() -> dict:
    """Return current import status dict."""
    global _status
    if _status["state"] == "running":
        _status["elapsed_sec"] = round(time.time() - float(
            datetime.fromisoformat(_status["started_at"]).timestamp()
        if _status["started_at"] else time.time()), 0)
    return dict(_status)


def trigger(all_commodities: bool = True, workers: int = 2) -> dict:
    """Start or resume the background import. Returns status."""
    global _import_thread, _status
    if _status["state"] == "running":
        return {"error": "Import already running", "status": _status}

    _import_thread = threading.Thread(
        target=_run_import_in_bg,
        kwargs={"workers": workers, "all_commodities": all_commodities, "checkpoint_every": 10},
        daemon=True,
    )
    _import_thread.start()
    return {"status": "started", "message": f"Background import running (workers={workers}, all_commodities={all_commodities})"}


def trigger_backfill_only() -> dict:
    """Just run the historical backfill on any existing CSV (resume after restart)."""
    global _status
    try:
        from mandi_rdd.ingestion.ingest_historical_csv import run_auto
        n = run_auto(folder=str(DATA_DIR))
        _status["backfill_rows"] = n
        _status["state"] = "backfilled"
        return {"status": "ok", "backfill_rows": n}
    except Exception as e:
        return {"status": "error", "error": str(e)}
