# Northflank deployment configuration for MandiIQ
# Connect your GitHub repo at https://app.northflank.com and create a new service
# Select "Docker" build, point to this repo

# Build settings (in Northflank UI):
# - Build context: .
# - Dockerfile: Dockerfile.northflank
# - Port: 8080

# Environment variables (set in Northflank UI):
# SECURITY: never commit real values here. Any credential that has ever been
# committed to this repo must be considered compromised and rotated.
# - PYTHONPATH=/app
# - PORT=8080
# - MANDIIQ_DB_PATH=/data/mandi_iq.duckdb
# - DATA_GOV_IN_API_KEY=<your data.gov.in key - set in Northflank, never in git>
# - GEMINI_API_KEY (optional)
# - NVIDIA_API_KEY (optional)
# - OPENROUTER_API_KEY (optional)
# - GRAFANA_CLOUD_PROM_URL (optional)
# - GRAFANA_CLOUD_PROM_USER (optional)
# - GRAFANA_CLOUD_PROM_PASSWORD (optional)
# - R2_ACCOUNT_ID=<your Cloudflare account ID>
# - R2_ACCESS_KEY_ID=<your R2 access key>
# - R2_SECRET_ACCESS_KEY=<your R2 secret key>
# - R2_BUCKET=mandiiq-data
# - MANDIIQ_SELF_REFRESH=1                     (optional; disable with 0)
# - MANDIIQ_REFRESH_INTERVAL_MINUTES=60        (optional; min 5)
# - MANDIIQ_REFRESH_INITIAL_DELAY_S=90         (optional; min 5)

# Persistent Volume:
# - Name: mandiiq-data
# - Mount path: /data
# - Size: 1 GB (free tier includes 1GB)
#
# The volume is the live warehouse. /health, /data-quality and /freshness all
# read it, so a redeploy never loses data as long as the volume stays mounted.

# Health check:
# - Path: /health
# - Port: 8080
# - Interval: 30s
# - Timeout: 10s

# Resources (free tier):
# - 512 MB RAM
# - 1 vCPU
# - 1 replica

# ─────────────────────────────────────────────────────────────────────────────
# Deploying a new build
# ─────────────────────────────────────────────────────────────────────────────
# Pushing to master does NOT redeploy Northflank. After merging a fix, either
#   * press "Build & deploy" on the service in the Northflank dashboard, or
#   * enable Auto-deploy for the service (Triggers -> Auto-deploy on push).
#
# Then confirm which build is live:
#   curl -s https://p01--mandiiq--x4n8x4gkmzht.code.run/health \
#     | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['version'], d['data_max_date'], d.get('days_behind'))"
# `version` comes from the FastAPI app, so an old value means an old build is
# still serving. `/data-quality` only exists on builds from 2026-09-30 onward.

# ─────────────────────────────────────────────────────────────────────────────
# Keeping the service awake
# ─────────────────────────────────────────────────────────────────────────────
# Free-tier hosts suspend idle services, which is why the first visitor used to
# wait for a cold start. Two independent keep-alives cover it:
#
#   1. .github/workflows/keepalive.yml - every 10 minutes, pinging until the
#      next run is due, so coverage is continuous. Pings /health on both
#      instances plus the landing page and the Streamlit app.
#   2. worker/worker.js + worker/wrangler.toml - a Cloudflare cron trigger
#      (*/1 minute). Deploy once with `wrangler deploy` (export
#      CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID first) and Cloudflare's
#      edge keeps everything warm even if GitHub cron is throttled or disabled.
#
# A third, external option is UptimeRobot pointed at /health every 5 minutes.
#
# .github/workflows/refresh-live-data.yml triggers POST /refresh hourly and then
# verifies /health and /data-quality, so a frozen warehouse fails a workflow
# within the hour instead of going unnoticed for weeks.

# ─────────────────────────────────────────────────────────────────────────────
# Self-refresh (the reason the data was frozen before 2026-09-30)
# ─────────────────────────────────────────────────────────────────────────────
# The container refreshes its own warehouse. On boot it waits 90 s and then
# runs the full pipeline, repeating every 60 minutes. Nothing external has to
# be alive for the numbers to be current; the GitHub cron is a second belt,
# not the mechanism.
#
# That loop always existed - it was throwing a DuckDB
# `Can't open a connection to same database file with a different configuration`
# exception on every single tick since July, logging it, and sleeping again.
# The bug is fixed (mandi_rdd/storage/duckdb_store.py runs its integrity probe
# once per process), and every attempt is now recorded and exposed on /health:
#
#   refresh_runs, refresh_failures, refresh_interval_s,
#   last_refresh_attempt_utc, last_refresh_success_utc, last_refresh_error
#
# so "quiet" can be told apart from "silently broken":
#
#   curl -s .../health | python3 -m json.tool | \
#     grep -E 'status|days_behind|refresh_|last_outcome'
#
# `status` now describes the DATA (healthy / stale / degraded / empty / unknown)
# rather than being the literal string "healthy". Data more than 3 days behind
# or any impossible future date makes it stop claiming health. `last_outcome`
# still describes the most recent pipeline run.
#
# Tunables (all optional):
#   MANDIIQ_SELF_REFRESH=0                  disable the in-process scheduler
#   MANDIIQ_REFRESH_INTERVAL_MINUTES=60     how often it runs (min 5)
#   MANDIIQ_REFRESH_INITIAL_DELAY_S=90      delay before the first run (min 5)
#
# Each tick is a full pipeline run (prices + rainfall + NDVI + RDD + forecast).
# On the 512 MB free tier that is the heaviest thing this service does; if the
# container starts OOM-restarting, raise MANDIIQ_REFRESH_INTERVAL_MINUTES to
# 360 rather than disabling the scheduler entirely - the GitHub workflow will
# still nudge it hourly from outside.

# ─────────────────────────────────────────────────────────────────────────────
# Data integrity operations
# ─────────────────────────────────────────────────────────────────────────────
# Arrival dates are parsed as DD/MM/YYYY (day-first) at ingest. A date that
# cannot be true (in the future) is rejected there and then, and any rows that
# predate that guard are repaired by the pipeline's `date_integrity` step or on
# demand:
#
#   curl -sS -X POST "https://p01--mandiiq--x4n8x4gkmzht.code.run/admin/repair-dates?dry_run=true"
#   curl -sS -X POST "https://p01--mandiiq--x4n8x4gkmzht.code.run/admin/repair-dates?dry_run=false"
#
# The repair swaps month and day for stored future dates (the inverse of the
# month-first mis-parse: 2026-12-09 -> 2026-09-12) and drops rows that stay
# impossible. /health reports `n_future_dates` so the result is verifiable.
#
# ─────────────────────────────────────────────────────────────────────────────
# Recovering the warehouse
# ─────────────────────────────────────────────────────────────────────────────
# The rebuild that repairs an inconsistent prices index is atomic: it copies
# `prices` into a staging table in one transaction, refuses to publish a copy
# smaller than 90% of the original, and swaps only after a write probe proves
# the new index. An interrupted rebuild rolls back to the original table.
# Before 2026-09-30 it did not: the copy was committed and `prices` was dropped
# as its own statement, so a copy killed by memory pressure was published as an
# empty warehouse - which is how 1.6M rows were lost.
#
# Two recovery paths, both plain HTTP so a workflow can run them:
#
#   curl -sS -X POST "https://p01--mandiiq--x4n8x4gkmzht.code.run/admin/rebuild-prices"
#     For an inconsistent index. Forces the rebuild, probes it, and only then
#     clears the recorded fault, so /health.index_fault_pending goes false.
#
#   curl -sS -X POST "https://p01--mandiiq--x4n8x4gkmzht.code.run/admin/restore-from-r2"
#     For an empty or ruined warehouse. Streams the gzipped R2 backup to disk
#     (8 MB at a time - the old code held the compressed file and then the
#     whole database in RAM, which is what got the first attempt OOM-killed),
#     refuses to swap in a backup with zero price rows, and keeps the previous
#     file as mandi_iq.duckdb.before-restore.
#
# The hourly refresh-live-data.yml workflow now runs both of these
# automatically when /health says index_fault_pending, or when n_prices is 0.
# If the upstream feed (api.data.gov.in) is unreachable, /health reports a
# degraded run and the warehouse keeps serving what it has - the pipeline
# skips the price fetch and still refreshes rainfall, RDD and the forecast.
