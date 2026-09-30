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

# Health check:
# - Path: /health
# - Port: 8080
# - Interval: 30s
# - Timeout: 10s

# Resources (free tier):
# - 512 MB RAM
# - 1 vCPU
# - 1 replica