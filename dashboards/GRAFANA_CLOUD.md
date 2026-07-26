# Grafana Cloud Free Tier — Setup Guide

Use **Grafana Cloud free tier** instead of the self-hosted Grafana sidecar
(which required Render's $7/mo `starter` plan).

## Free Tier Limits
| Feature | Limit |
|---------|-------|
| Data series | 10,000 |
| Retention | 14 days |
| Dashboards | Unlimited |
| Users | Up to 3 |
| Metrics ingestion | 100 MB / mo |

✅ Plenty for MandiIQ's ~20 metrics (pipeline duration, price count, API latency).

---

## Step 1: Create Free Account

1. Go to https://grafana.com → **Get Started Free**
2. Sign up with your GitHub/Google/email
3. Select **Grafana Cloud** plan (free)
4. Once created, your stack URL is: `https://<your-stack>.grafana.net`

## Step 2: Create a Cloud API Key

1. In your Grafana Cloud portal, go to **Security → API Keys**
2. Click **Add API Key**
3. Role: **MetricsPublisher** (for pushing metrics)
4. Copy the key — it starts with `glc_`

## Step 3: Configure Prometheus Agent (on Render)

Add the **Grafana Agent** as a sidecar or use **Prometheus remote write**.

**Option A: Grafana Agent (fluvbit)**

The Grafana Agent is a lightweight (~50 MB) binary that scrapes `/metrics` and
pushes to Cloud. Add this to your Render `startCommand`:

```bash
# In render.yaml startCommand:
# grafana-agent --config.file=grafana-agent.yaml & uvicorn ...
```

Create `grafana-agent.yaml` in the repo root:

```yaml
metrics:
  wal_directory: /tmp/agent/wal
  global:
    scrape_interval: 60s
    remote_write:
      - url: https://prometheus-{region}.grafana.net/api/prom/push
        basic_auth:
          username: {instance-id}
          password: {cloud-api-key}
  configs:
    - name: default
      scrape_configs:
        - job_name: mandiiq-api
          static_configs:
            - targets: ["localhost:8000"]
          metrics_path: /metrics
```

**Option B: Prometheus Remote Write (lighter — preferred)**

Render's free tier can't run multiple processes (no Docker). The simpler approach
is to use a **remote write proxy** inside the API itself:

1. Add `prometheus-client` to `requirements/api.txt`
2. Instrument the API to push metrics to Grafana Cloud on an interval

**Option C: No Agent — Use HTTP Endpoint (simplest)**

Grafana Cloud can scrape your `/metrics` endpoint directly if it's publicly
accessible. In your Grafana Cloud dashboard:

1. Go to **Connections → Data Sources → Add Prometheus**
2. Set URL to: `https://mandiiq-api.onrender.com`
3. Set Scrape interval: `60s`
4. Click **Save & Test**

✅ **This is the simplest option and costs $0.** No agent, no config changes.

## Step 4: Import MandiIQ Dashboard

1. In Grafana Cloud, go to **Dashboards → New → Import**
2. Fetch the dashboard JSON:

   ```bash
   curl -s https://mandiiq-api.onrender.com/grafana-dashboard > dashboard.json
   ```

3. Upload `dashboard.json` in the Import UI (or paste the URL)
4. When prompted, select `grafanacloud-{stack}-prom` as the datasource
5. Click **Import**

## Step 5: Verify

You should see metrics within 60 seconds:
- `mandiiq_uptime_seconds` — API uptime
- `mandiiq_llm_fallback_total` — LLM failures
- `mandiiq_pipeline_duration_seconds` — Pipeline run time
- `mandiiq_prices_total` — Total prices in DB
- `mandiiq_commodities_total` — Commodity count

---

## Upgrading Later

When your project grows, you can always add the Grafana Agent on Render
(requires Render's Docker plan) or use Grafana Cloud's paid tier for
longer retention and more series.
