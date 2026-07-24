# Historical Price Data (Backfill)

The live `data.gov.in` mandi-prices API (`9ef84268-...`) is a **daily snapshot
feed** — it only ever returns the *current day's* records. That means the
nightly GitHub Action can append one day at a time but can **never create
months/years of history on its own**.

Two KPIs on the Executive Overview need real history to compute:

| KPI | Why it needs history |
|-----|----------------------|
| **RDD Effect** | The regression-discontinuity estimator needs 3+ dates with both deficient AND non-deficient rainfall to find a price jump at the threshold. |
| **Forecast MAPE** | Prophet needs >= 20 daily points for a train/test split (ideally months). |

`Avg Price` and `Districts` work from even a single day, so they populate
immediately. The other two stay `—` until history exists.

## How to backfill (one-time, then automatic)

1. **Get a historical CSV** from one of these verified sources:
   - **data.gov.in dataset page** for "APMC Mandi Prices / Agmarknet"
     (search "Agmarknet" on https://www.data.gov.in) → look for the
     **Downloads** section with monthly/yearly bulk CSV or ZIP files.
   - **agmarknet.gov.in** → Reports → Daily Prices → yearly ZIP archives
     (official, complete, multi-year).

   Expected columns (Agmarknet / data.gov.in bulk format):
   ```
   arrival_date, state, district, market, commodity, variety,
   grade, min_price, max_price, modal_price
   ```
   The loader auto-detects `dd/mm/yyyy`, `yyyy-mm-dd`, `mm-dd-yyyy`
   and tolerates alternate column names (e.g. `mandi`, `maxprice`).

   ### Hands-off option (no manual download)
   `mandi_rdd/ingestion/fetch_historical.py` can pull the bulk file
   **itself** during the nightly workflow, so you never touch a CSV:
   1. On the data.gov.in dataset **Downloads** page, copy the direct
      link to the bulk CSV/ZIP.
   2. In GitHub → **Settings → Secrets and variables → Actions**, add:
      - Name: `HISTORICAL_SOURCE_URL`
      - Value: that direct download URL
   3. The next `nightly-ingest` run fetches it into `data/historical/`,
      unpacks ZIPs, and the existing backfill ingests it automatically.
   Leave the secret unset to keep the manual-drop behaviour.

2. **Drop it in the historical folder:**
   ```
   mandi_rdd/data/historical/<any-name>.csv
   ```
   (the folder is tracked via `.gitkeep`; do NOT commit the CSV itself
   unless you intend to — see step 3).

3. **Push it** so the online pipeline ingests it:
   ```bash
   git add mandi_rdd/data/historical/<file>.csv
   git commit -m "data: backfill historical prices <range>"
   git push origin master
   ```
   The next `nightly-ingest` run (or a manual "Run workflow") will:
   - `upsert` every row into `prices` (deduped, no duplicates),
   - delete the consumed CSV,
   - then run RDD + forecast + classifier on the combined history,
   - commit the refreshed DB back → Streamlit redeploys automatically.

## Alternative: keep the CSV out of git

If you'd rather not commit a large CSV, you can SSH into the runner or use
the GitHub web UI to place the file before a manual workflow run. The
`ingest_historical_csv.py` script also supports a direct call:

```bash
python -m mandi_rdd.ingestion.ingest_historical_csv path/to/file.csv
# or auto-ingest everything currently in data/historical/:
python -m mandi_rdd.ingestion.ingest_historical_csv --auto
```

## Verifying it worked (online)

After the workflow run completes (Actions tab → Nightly Ingestion → green),
open the dashboard. Within ~1-2 min of Streamlit's redeploy:
- **Avg Price** / **Districts** → real numbers immediately.
- **RDD Effect** → populates once >= 3 qualifying rainfall dates exist.
- **Forecast MAPE** → populates once >= 20 daily points exist per commodity.

You can also check the DB directly:
```sql
SELECT commodity, MIN(arrival_date), MAX(arrival_date), COUNT(*)
FROM prices GROUP BY commodity;
```
A wide date range = successful backfill.
