# MandiIQ Deployment Guide
## Quick Start (4-Step Deploy)

### Step 1: Push code to GitHub ✅ DONE
```bash
git push origin HEAD
```

### Step 2: Deploy API to Render
**Blueprint reads `render.yaml`**

#### Reconnect Blueprint (REQUIRED after repo recreate):
1. Go to https://dashboard.render.com
2. Click **Blueprints** in left sidebar
3. Click **Connect Blueprint** → select `flawsom/MandiIQ`
4. It will auto-detect `render.yaml` and deploy all services
5. ⏳ Wait 2-5 minutes for build

#### Set these Environment Variables in Render Dashboard:
| Variable | Value |
|----------|-------|
| `DATA_GOV_IN_API_KEY` | (your key from data.gov.in) |
| `NVIDIA_API_KEY` | (your key from build.nvidia.com) |
| `GEMINI_API_KEY` | (your key from aistudio.google.com) |
| `OPENROUTER_API_KEY` | (your key from openrouter.ai) |
| `MANDIIQ_DB_PATH` | `mandi_rdd/data/mandi_iq.duckdb` |
| `SENTINEL_CLIENT_ID` | (your Sentinel Hub client ID) |
| `SENTINEL_CLIENT_SECRET` | (your Sentinel Hub secret) |
| `WEBHOOK_SECRET` | (webhook shared secret) |
| `R2_ACCOUNT_ID` | (Cloudflare R2 account ID) |
| `R2_ACCESS_KEY_ID` | (R2 access key) |
| `R2_SECRET_ACCESS_KEY` | (R2 secret key) |
| `R2_BUCKET` | `mandiiq-data` |
| `RENDER_DEPLOY_HOOK_URL` | (create in Render Dashboard → Settings → Deploy Hooks) |
| `ALL_INDIA_RAINFALL_API_KEY` | (same as DATA_GOV_IN_API_KEY) |
| `ALL_INDIA_RAINFALL_RESOURCE_ID` | (rainfall resource ID) |
| `GRAFANA_CLOUD_PROM_URL` | `https://prometheus-prod-43-prod-ap-south-1.grafana.net` |
| `GRAFANA_CLOUD_PROM_USER` | `3400476` |
| `GRAFANA_CLOUD_PROM_PASS` | Grafana Cloud API token (set as GitHub secret) |

### Step 3: Deploy Dashboard to Streamlit Cloud
1. Go to https://share.streamlit.io
2. Click **Deploy an app**
3. Set Repository: `flawsom/MandiIQ`
4. Branch: `master`
5. Main file: `mandi_rdd/dashboard/app.py`
6. Click **Deploy**

#### Set these Secrets in Streamlit Cloud Dashboard:
In Settings → Secrets:
```toml
MANDIQ_API_URL = "https://mandiiq-api.onrender.com"
OPENROUTER_API_KEY = "sk-or-v1-..."
GEMINI_API_KEY = "..."
ALL_INDIA_RAINFALL_API_KEY = "..."
ALL_INDIA_RAINFALL_RESOURCE_ID = "..."
```

### Step 4: Fix Custom Domain
**Option A — Point DNS to Render (easiest):**
Set `CNAME` record for `mandiiq.unifies.codes` → `mandiiq-api.onrender.com`

**Option B — Vercel:**
Add domain in Vercel project settings, deploy a wrapper/proxy

### Step 5: Verify all 4 URLs
- ✅ https://mandiiq-api.onrender.com/health
- ✅ https://mandiiq.streamlit.app
- ❌ https://mandiiq.unifies.codes (needs DNS/deploy)
- ✅ https://github.com/flawsom/MandiIQ

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| LFS pointer file in DuckDB | Add `git lfs pull` to Render build command |
| OOM on free tier | Never set `--workers` beyond 1 (512 MB limit) |
| GitHub 500 on push | Use SSH: `git remote set-url origin git@github.com:flawsom/MandiIQ.git` |
| Rainfall 403 | Find working data.gov.in resource ID |
| Render deploy hook fails | Set `RENDER_DEPLOY_HOOK_URL` in Render env vars |
| API returns 404 for forecast | Fixed in code — now returns 200 with `"status":"unavailable"` |
| Risk score = 50 always | Need XGBoost installed + pipeline run (`POST /refresh`) |

## GitHub Secrets (for Actions workflows)
Set in: https://github.com/flawsom/MandiIQ/settings/secrets/actions

| Secret | Status |
|--------|--------|
| `OPENROUTER_API_KEY` | ✅ Set (from local env) |
| `DATA_GOV_IN_API_KEY` | ❌ Needs value |
| `GEMINI_API_KEY` | ❌ Needs value |
| `R2_ACCOUNT_ID` | ❌ Needs value |
| `R2_ACCESS_KEY_ID` | ❌ Needs value |
| `R2_SECRET_ACCESS_KEY` | ❌ Needs value |
| `HISTORICAL_SOURCE_URL` | ❌ Needs value |
| `RAINFALL_RESOURCE_ID` | ❌ Needs value |

---

## Onboarding Checklist

### GitHub ✅
- [x] Push code to master
- [x] Verify remote matches local
- [ ] Set GitHub Secrets (Actions tab)

### Render
- [ ] Reconnect Blueprint
- [ ] Verify all env vars
- [ ] Verify API health endpoint

### Streamlit Cloud
- [ ] Deploy dashboard
- [ ] Set secrets
- [ ] Verify loads

### Custom Domain
- [ ] Fix DNS or Vercel deploy
- [ ] Verify HTTPS works

### CI/CD (Optional)
- [ ] Nightly Ingestion workflow
- [ ] Freshness check workflow
- [ ] Render Keepalive workflow

### R2 Backup (Optional)
- [ ] Set R2 env vars
- [ ] Verify backup runs

### NDVI (Optional)
- [ ] Set Sentinel Hub credentials

### Grafana (Optional)
- [ ] Upgrade to starter plan on Render
- [ ] Configure Grafana dashboard
