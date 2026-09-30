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
