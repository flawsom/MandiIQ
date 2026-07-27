# MandiIQ — Handoff Document

## Project Overview

MandiIQ is an agricultural market intelligence dashboard. It ingests price data from India's Agmarknet (data.gov.in), rainfall data from IMD, NDVI imagery from Sentinel-2, and applies RDD (Regression Discontinuity Design) causal analysis to estimate the impact of rainfall deficits on commodity prices.

---

## Service Architecture

| Service | URL | Hosting | Purpose |
|---------|-----|---------|---------|
| **API** | `https://mandiiq-api-lnd7.onrender.com` | Render | FastAPI — data ingestion, RDD analysis, dashboard JSON serving, metrics push |
| **Dashboard** | `https://mandiiq.streamlit.app` | Streamlit Cloud | Streamlit UI — pages: Dashboard, RDD Explorer, Price Trends, Commodity Health, About |
| **GitHub Pages** | `https://flawsom.github.io/MandiIQ` | GitHub Pages | Static assets — heartbeat monitor, docs |
| **Custom Domain** | `https://mandiiq.unifies.codes` | Vercel rewrite → GitHub Pages | Vercel rewrite proxy (in `flawsom/mandiiq-redirect` repo) |
| **GitHub Repo** | `https://github.com/flawsom/MandiIQ` | GitHub | Source code + DuckDB data (LFS) |

---

## Deployment & CI/CD

### Render (API)
- Auto-deploys on push to `master`
- Pipeline runs on boot (ingests data, runs RDD analysis)
- Dashboard cache auto-warms on boot
- Grafana Cloud metrics push thread starts on boot

### Streamlit Cloud (Dashboard)
- Auto-deploys on push to `master` (detects changes in repo)
- Connected to `https://github.com/flawsom/MandiIQ`

### GitHub Actions
| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `daily-ingest.yml` | Schedule + push | Daily data download, LFS update, auto-commit |
| `dashboard-heartbeat.yml` | Schedule (hourly) | POSTs to refresh dashboard cache via webhook |
| `deploy-pages.yml` | Push to master | Deploys GitHub Pages (static assets) |
| `ci.yml` | Push + PR | Runs tests |

---

## Environment Variables

### Render (mandiiq-api-lnd7)

```yaml
DATA_GOV_IN_API_KEY: <set>
GH_TOKEN: <set>  # GitHub PAT for API proxy
GRAFANA_CLOUD_PROM_PASS: <set>
GRAFANA_CLOUD_PROM_URL: https://prometheus-prod-43-prod-ap-south-1.grafana.net
GRAFANA_CLOUD_PROM_USER: 3400476
MANDIIQ_DB_PATH: mandi_rdd/data/mandi_iq.duckdb
PORT: "8000"
PYTHON_VERSION: "3.11"
R2_ACCESS_KEY_ID: <set>
R2_ACCOUNT_ID: <set>
R2_BUCKET: mandiiq-data
R2_SECRET_ACCESS_KEY: <set>
RENDER_API_URL: https://mandiiq-api.onrender.com
SENTINEL_CLIENT_ID: <set>
STREAMLIT_APP_URL: https://mandiiq.streamlit.app
```

### Streamlit Cloud (mandiiq.streamlit.app)
```toml
MANDIQ_API_URL = "https://mandiiq-api-lnd7.onrender.com"
OPENROUTER_API_KEY = "<set>"
GEMINI_API_KEY = "<set>"
ALL_INDIA_RAINFALL_RESOURCE_ID = "<set>"
ALL_INDIA_RAINFALL_API_KEY = "<set>"
```

### GitHub Secrets (all 19 secrets set via `gh secret set`)

---

## Key Code Structure

```
mandi_rdd/
├── api/
│   ├── main.py              # FastAPI app — endpoints, pipeline, cache
│   └── metrics_push.py       # Grafana Cloud pushgateway thread
├── dashboard/
│   ├── app.py                # Streamlit app — pages, navigation, CSS, topbar, footer
│   └── pages/
│       ├── about.py          # About page — methodology, data sources
│       ├── rdd_explorer.py   # RDD analysis explorer
│       ├── price_trends.py   # Price trend charts
│       └── commodity_health.py # Per-commodity freshness
├── analysis/                 # RDD analysis, XGBoost models
├── data/                     # DuckDB database (LFS-tracked), CSV backfills
├── ingestion/                # Data ingestion pipelines
└── tests/                    # Test suite
docs/
├── heartbeat-dashboard.html  # GitHub Pages heartbeat monitor
.github/workflows/            # CI/CD workflows
```

---

## Current State (Fixed This Session)

### ✅ Fully Working
- **API** — Healthy, responds at `mandiiq-api-lnd7.onrender.com`
- **Dashboard cache** — Auto-warms on boot (`cache_size: 1`), heartbeat shows "Fresh"
- **GitHub proxy** — `GH_TOKEN` set, `/proxy/github/` returns data (no 403)
- **Grafana Cloud push** — `PROM_PASS` env var name fixed, thread starts on boot
- **Footer** — Uses `st.html()` with inline styles, no more raw HTML code
- **Topbar + CSS** — All HTML rendered via `st.html()` (no Markdown code-block issue)
- **Custom domain** — `mandiiq.unifies.codes` rewrites to GitHub Pages via Vercel
- **Daily ingest** — Runs on schedule, auto-commits DuckDB via LFS
- **Heartbeat monitor** — Runs hourly, shows live cache status
- **About page** — Citation code block removed, URL updated to `flawsom/MandiIQ`
- **Secrets** — All set on GitHub, Render, and Streamlit Cloud

### 🔄 Pipeline (Runs on boot, takes 5-15 min)
- Prices: loading from data.gov.in
- Commodities: loading from Agmarknet
- Rainfall: pre-loaded (CSV)
- RDD analysis: runs after data loads
- NDVI: runs after Sentinel-2 data loads

---

## Known Issues / TODOs

1. **Render redeploy resets pipeline** — Every push triggers a redeploy, resetting the in-memory data. The pipeline re-runs on boot but takes 5-15 minutes. Consider separating the API and pipeline into different services, or use persistent volume.

2. **DuckDB in LFS** — The 150MB DuckDB file is tracked by LFS. GitHub LFS has bandwidth limits (1GB/month free). The daily-ingest workflow re-creates the DB daily.

3. **Vercel SSL** — The `mandiiq.unifies.codes` domain had SSL issues during setup. Currently redirects via rewrite. If SSL cert is pending, it may need manual domain verification on Vercel.

4. **Streamlit rendering** — All HTML content converted from `st.markdown()` to `st.html()` to avoid Markdown code-block issues. Streamlit 1.35+ required for `st.html()`.

5. **Temp files in repo root** — Several `_fix_*.py`, `_check_*.py`, and other temp scripts were created during this session. Verify they're all deleted (`git clean -fd` to check).

6. **Grafana Cloud dashboard** — The Grafana dashboard JSON at `docs/mandi_dashboard.json` should be deployed to Grafana Cloud for live metrics visualization.

7. **Vercel redirect repo** — The `mandiiq-redirect` Vercel project was created in `flawsom/mandiiq-redirect` repo. The `vercel.json` with rewrite rules may have been pushed to `main` instead of `master` branch. Verify.

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Pipeline status, data freshness |
| `/refresh` | POST | Trigger data ingestion pipeline |
| `/admin/dashboard-status` | GET | Dashboard cache status |
| `/admin/refresh-dashboard-cache` | POST | Reload dashboard JSON from disk |
| `/grafana-dashboard` | GET | Patched Grafana dashboard JSON |
| `/proxy/github/{path}` | GET | GitHub API proxy (bypasses CORS) |
| `/api/prices/{commodity}` | GET | Price data for a commodity |
| `/api/commodities` | GET | List of available commodities |
| `/api/rdd/{commodity}` | GET | RDD analysis results |

---

## Quick Diagnostic Commands

```bash
# Check API health
curl -s https://mandiiq-api-lnd7.onrender.com/health

# Check dashboard cache
curl -s https://mandiiq-api-lnd7.onrender.com/admin/dashboard-status

# Trigger pipeline
curl -s -X POST https://mandiiq-api-lnd7.onrender.com/refresh

# Refresh dashboard cache
curl -s -X POST https://mandiiq-api-lnd7.onrender.com/admin/refresh-dashboard-cache

# Check GitHub proxy
curl -s https://mandiiq-api-lnd7.onrender.com/proxy/github/repos/flawsom/MandiIQ
```

---

*Handoff prepared 2026-07-27. Use with `repomix --style markdown` to generate a full repository context file.*
