# MandiIQ Grafana Dashboard

Real-time monitoring dashboard for the MandiIQ data ingestion pipeline.
Visualizes pipeline health, step durations, API call latency, and data
throughput from the `/metrics` Prometheus endpoint.

## Quick Start

### Option 1: Auto-Provision via `/grafana-dashboard` (Recommended)

The API exposes a `GET /grafana-dashboard` endpoint that serves the dashboard
JSON directly. Grafana can fetch this URL for zero-touch provisioning.

**Step 1: Validate the endpoint**

```bash
curl -s https://mandiiq-api.onrender.com/grafana-dashboard | head -c 200
```

Expected output (truncated):
```json
{"__inputs":[{"name":"DS_PROMETHEUS",...
```

If you see `{"detail":"Dashboard template not found"}`, the dashboard file
isn't deployed — file an issue at github.com/flawsom/MandiIQ.

**Step 2: Configure Grafana's dashboard provisioning**

Create `/etc/grafana/provisioning/dashboards/mandiiq.yaml`:

```yaml
apiVersion: 1

providers:
  - name: MandiIQ
    type: file
    disableDeletion: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

Then create a **JSON provisioning file** at
`/etc/grafana/provisioning/dashboards/mandiiq-from-api.json`:

```json
{
  "apiVersion": 1,
  "providers": [
    {
      "name": "MandiIQ",
      "type": "url",
      "options": {
        "url": "https://mandiiq-api.onrender.com/grafana-dashboard"
      }
    }
  ]
}
```

> **Note:** The `type: url` provider is available in Grafana 9+ and fetches the
> dashboard JSON on every provisioning cycle. If your Grafana version doesn't
> support URL providers, use Option 2 or 3 instead.

**Step 3: Add the Prometheus datasource**

Create `/etc/grafana/provisioning/datasources/mandiiq.yml`:

```yaml
apiVersion: 1

datasources:
  - name: MandiIQ Prometheus
    type: prometheus
    access: proxy
    url: https://mandiiq-api.onrender.com
    isDefault: false
    editable: false
```

**Step 4: Restart Grafana**

```bash
sudo systemctl restart grafana-server
```

When importing, Grafana prompts for the `${DS_PROMETHEUS}` datasource —
select the "MandiIQ Prometheus" datasource you just created.

### Option 2: Manual Import

1. Fetch the dashboard JSON:

   ```bash
   curl -s https://mandiiq-api.onrender.com/grafana-dashboard > mandiiq-pipeline.json
   ```

2. In Grafana: **+** > **Import** > Upload `mandiiq-pipeline.json`
3. Select your Prometheus data source for `${DS_PROMETHEUS}`

### Option 3: Self-Hosted (File-Based)

```bash
# 1. Add the Prometheus datasource
cp dashboards/grafana-datasource.yml /etc/grafana/provisioning/datasources/mandiiq.yml

# 2. Add the dashboard JSON (fetched from the API)
curl -s https://mandiiq-api.onrender.com/grafana-dashboard > /etc/grafana/provisioning/dashboards/mandiiq-pipeline.json

# 3. Add a dashboard provider
cat > /etc/grafana/provisioning/dashboards/mandiiq-provider.yaml << 'EOF'
apiVersion: 1
providers:
  - name: MandiIQ
    type: file
    options:
      path: /etc/grafana/provisioning/dashboards
EOF

# 4. Restart Grafana
sudo systemctl restart grafana-server
```

### Option 5: Full Observability Stack (Docker Compose)

Stand up the entire stack — MandiIQ API + Prometheus + Grafana — in one command.
Prometheus scrapes the API's `/metrics` endpoint, and Grafana auto-provisions the
dashboard from the `/grafana-dashboard` endpoint.

**`docker-compose.yml`:**

```yaml
services:
  # ── MandiIQ API (optional: use if running locally instead of Render) ──
  mandiiq-api:
    build:
      context: .
      dockerfile: mandi_rdd/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - PORT=8000
      - MANDIIQ_DB_PATH=/data/mandi_iq.duckdb
      - DATA_GOV_IN_API_KEY=${DATA_GOV_IN_API_KEY:-}
    volumes:
      - ./mandi_rdd/data:/data
    healthcheck:
      test: ["CMD-SHELL", "python -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")'"]
      interval: 30s
      timeout: 5s
      retries: 3

  # ── Prometheus (scrapes /metrics) ──
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./dashboards/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--web.console.libraries=/etc/prometheus/console_libraries"
      - "--web.console.templates=/etc/prometheus/consoles"
      - "--web.enable-lifecycle"

  # ── Grafana (dashboard auto-provisioned from API) ──
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - ./dashboards/grafana-datasource.yml:/etc/grafana/provisioning/datasources/mandiiq.yml:ro
      - ./dashboards/grafana-provider.yml:/etc/grafana/provisioning/dashboards/mandiiq-provider.yml:ro
      - grafana_data:/var/lib/grafana
    environment:
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
    depends_on:
      prometheus:
        condition: service_started

volumes:
  prometheus_data:
  grafana_data:
```

**`dashboards/prometheus.yml`** (scrape config):

```yaml
# Prometheus scrape configuration for MandiIQ.
# Place at dashboards/prometheus.yml
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: "mandiiq-api"
    metrics_path: /metrics
    static_configs:
      - targets: ["mandiiq-api:8000"]
        labels:
          service: "mandiiq"
          environment: "docker"

  # Uncomment to scrape the Render-hosted API instead of local:
  # - job_name: "mandiiq-api-render"
  #   metrics_path: /metrics
  #   scheme: https
  #   static_configs:
  #     - targets: ["mandiiq-api.onrender.com"]
  #       labels:
  #         service: "mandiiq"
  #         environment: "production"
```

**`dashboards/grafana-provider.yml`** (dashboard provisioning):

```yaml
apiVersion: 1

providers:
  - name: MandiIQ
    type: url
    disableDeletion: true
    options:
      # Grafana fetches the dashboard JSON from the API on every cycle
      url: "http://mandiiq-api:8000/grafana-dashboard?datasource=MandiIQ+Prometheus"
```

**Start the stack:**

```bash
docker compose up -d
```

Then open:
- **Grafana**: http://localhost:3000 (admin/admin, anonymous auto-login)
- **Prometheus**: http://localhost:9090
- **MandiIQ API**: http://localhost:8000/docs

> **Note:** The Grafana datasource provisioning YAML (`grafana-datasource.yml`)
> must point at the Prometheus container. If running Render's hosted API
> instead of the local container, update the URL to
> `https://mandiiq-api.onrender.com` in both the datasource and the compose
> scrape target.

> **Pro tip:** Use `?datasource=MandiIQ+Prometheus` on the dashboard URL to
> pre-bind the datasource name so Grafana skips the import prompt entirely.

The `dashboards/grafana-provider.yml` file is included in this repo and ready to use.

## Datasource Variable

All dashboard panels reference the template variable **`${DS_PROMETHEUS}`**.

- When importing manually, Grafana prompts you to select a Prometheus datasource
  to bind to this variable.
- When using provisioning YAML, the datasource UID from your YAML config is
  automatically resolved — no manual prompt.
- The variable name `DS_PROMETHEUS` is Grafana's standard convention for
  Prometheus datasource template variables.

## Validating the Setup
## Metrics Scraping with Grafana Alloy / Grafana Agent

### Grafana Alloy (River syntax)

[Grafana Alloy](https://grafana.com/docs/alloy/latest/) is the recommended
OpenTelemetry-compatible collector. Create `alloy-config.river`:

```river
// Scrape the MandiIQ /metrics endpoint and forward to a Grafana Cloud
// Prometheus endpoint or self-hosted Prometheus-compatible receiver.

logging {
  level  = "info"
  format = "logfmt"
}

// ── Scrape the MandiIQ API (Render-hosted) ──
prometheus.scrape "mandiiq" {
  targets = [
    {
      __address__ = "mandiiq-api.onrender.com:443",
      scheme      = "https",
      metrics_path = "/metrics",
    },
  ]
  scrape_interval = "30s"
  scrape_timeout  = "10s"
  forward_to     = [prometheus.remote_write.default.receiver]
}

// ── Forward to a Prometheus-compatible backend (e.g. Grafana Cloud) ──
prometheus.remote_write "default" {
  endpoint {
    url = env("REMOTE_WRITE_URL")  // set this in your environment

    basic_auth {
      username = env("REMOTE_WRITE_USERNAME")
      password = env("REMOTE_WRITE_PASSWORD")
    }
  }
}
```

Run with:

```bash
docker run -d --name alloy   -v ./alloy-config.river:/etc/alloy/config.river:ro   -e REMOTE_WRITE_URL=https://prometheus-prod-XX.grafana.net/api/prom/push   -e REMOTE_WRITE_USERNAME=your-instance-id   -e REMOTE_WRITE_PASSWORD=your-token   grafana/alloy:latest   run --server.http.listen-addr=0.0.0.0:12345 /etc/alloy/config.river
```

### Grafana Agent Static Mode (legacy YAML)

For the older [Grafana Agent static
mode](https://grafana.com/docs/agent/latest/static/), create
`agent-config.yaml`:

```yaml
# agent-config.yaml — Grafana Agent static mode
server:
  log_level: info

metrics:
  wal_directory: /tmp/agent-wal
  global:
    scrape_interval: 30s
    remote_write:
      - url: ${REMOTE_WRITE_URL}
        basic_auth:
          username: ${REMOTE_WRITE_USERNAME}
          password: ${REMOTE_WRITE_PASSWORD}

  configs:
    - name: mandiiq
      scrape_configs:
        - job_name: "mandiiq-api"
          metrics_path: /metrics
          scheme: https
          static_configs:
            - targets: ["mandiiq-api.onrender.com"]
              labels:
                service: "mandiiq"
                environment: "production"
```

Run with:

```bash
docker run -d --name grafana-agent   -v ./agent-config.yaml:/etc/agent/config.yaml:ro   -e REMOTE_WRITE_URL=https://prometheus-prod-XX.grafana.net/api/prom/push   -e REMOTE_WRITE_USERNAME=your-instance-id   -e REMOTE_WRITE_PASSWORD=your-token   grafana/agent:latest   --config.file=/etc/agent/config.yaml
```

### Prometheus Standalone (without Docker Compose)

If you only want a standalone Prometheus instance scraping the API:

**`prometheus.yml`:**

```yaml
global:
  scrape_interval: 30s

scrape_configs:
  - job_name: "mandiiq-api"
    metrics_path: /metrics
    scheme: https
    static_configs:
      - targets: ["mandiiq-api.onrender.com"]
        labels:
          service: "mandiiq"
          environment: "production"
```

**Run:**

```bash
prometheus --config.file=prometheus.yml
```

Or with Docker:

```bash
docker run -d --name prometheus   -p 9090:9090   -v ./prometheus.yml:/etc/prometheus/prometheus.yml:ro   prom/prometheus
```


```bash
# Check the dashboard API endpoint
curl -s https://mandiiq-api.onrender.com/grafana-dashboard | python -c "import json,sys; d=json.load(sys.stdin); print(f'Dashboard: {d.get(\"dashboard\",d).get(\"title\",\"?\")} — {len(d.get(\"dashboard\",d).get(\"panels\",[]))} panels')"

# Check the /metrics endpoint
curl -s https://mandiiq-api.onrender.com/metrics | head -20

# Check the API is alive
curl -s https://mandiiq-api.onrender.com/health | python -c "import json,sys; d=json.load(sys.stdin); print(f'Status: {d[\"status\"]} — {d[\"n_prices\"]:,} prices, {d[\"n_commodities\"]} commodities')"
```

## Panel Reference

### Row 1: Pipeline Health (stats)
- **Pipeline Runs** — total completed runs
- **Successful** — successful runs count
- **Failures** — failed runs count
- **Last Run Duration** — latest run time in seconds (thresholds: >60s yellow, >120s red)
- **Time Since Last Run** — seconds since last run (thresholds: >10min yellow, >1h red)
- **Success Rate** — `success / total` as a percentage

### Row 2: Pipeline Run Status
- **Run Status Over Time** — step-before area chart of success/failure cumulative counts
- **Health Overview** — smoothed time series of last run duration and age

### Row 3: Step Durations
- **Duration by Stage** — horizontal bar gauge showing mean duration per pipeline step
- **Duration Timeseries** — smoothed time series per step name
- **Step Duration Summary** — table of mean, max, and count per step (sorted by mean desc)
- **Step Outcomes** — step-before chart of success/failure per step
- **Rows Ingested** — horizontal bar gauge of fetched vs new rows per step

### Row 4: API Calls
- **API Call Latency** — time series of mean and max (dashed) latency per endpoint
- **API Error Rate** — step-before area chart of success/failure per endpoint
- **API Call Volume** — total call count per endpoint as horizontal bar gauge
- **API Latency Buckets** — mean latency per endpoint as color-thresholded bar gauge

### Row 5: Pipeline Summary
- **Latest Pipeline Snapshot** — stat panel showing latest success and failure counts
- **API Summary** — stat rows per endpoint (success count, error count)
- **Row Summary** — stat rows per step (fetched count, new count)

## Metric Reference

The `/metrics` endpoint exposes the following Prometheus metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mandiiq_pipeline_runs_total` | counter | — | Total pipeline runs |
| `mandiiq_pipeline_success_total` | counter | — | Successful runs |
| `mandiiq_pipeline_failure_total` | counter | — | Failed runs |
| `mandiiq_step_duration_seconds` | gauge | `step`, `quantile` | Step execution time (mean/max/count) |
| `mandiiq_step_duration_histogram_seconds` | histogram | `step`, `le` | Step execution time histogram buckets |
| `mandiiq_step_outcome_total` | counter | `step`, `result` | Step success/failure count |
| `mandiiq_rows_total` | counter | `step`, `kind` | Rows fetched/new per step |
| `mandiiq_api_duration_seconds` | gauge | `endpoint`, `quantile` | API call duration (mean/max/count) |
| `mandiiq_api_calls_total` | counter | `endpoint`, `result` | API call success/failure count |
| `mandiiq_last_pipeline_duration_seconds` | gauge | — | Latest run duration |
| `mandiiq_last_pipeline_run_age_seconds` | gauge | — | Seconds since last run |
| `mandiiq_uptime_seconds` | gauge | — | Server uptime |
| `mandiiq_health_checks_total` | counter | — | Total health check requests |

> **Note:** Dots and hyphens in endpoint/step names are sanitized to
> underscores in Prometheus labels (e.g. `data.gov.in` → `data_gov_in`).

> **Histogram note:** The `mandiiq_step_duration_histogram_seconds` metric
> uses cumulative histogram buckets with boundaries `[0.01, 0.05, 0.1, 0.5,
> 1.0, 5.0, 10.0, 30.0, 60.0, +Inf]`. This enables **heatmap
> visualizations** in Grafana via the `_bucket`, `_sum`, and `_count`
> suffixes. The gauge quantiles (`mean`, `max`, `count`) remain available
> on the separate `mandiiq_step_duration_seconds` metric.


## Webhook: Auto-Refresh on Dashboard Save

The API exposes a webhook endpoint that Grafana (or any external process) can
POST to whenever the dashboard JSON changes. On receipt, the server clears its
LRU cache and re-reads the JSON file from disk — no server restart needed.

### Endpoint

```
POST /webhook/grafana-dashboard-update
Content-Type: application/json
```

Accepts any JSON payload. The `event` and `title` fields are logged for
auditability but are not required. The endpoint always returns 200 (or an
error message if the dashboard file is missing on disk).

**Example call:**
```bash
curl -X POST https://mandiiq-api.onrender.com/webhook/grafana-dashboard-update \
  -H "Content-Type: application/json" \
  -d '{"event":"dashboard_updated","title":"MandiIQ Pipeline"}'
```

**Expected response:**
```json
{
  "status": "ok",
  "message": "Dashboard cache cleared and JSON reloaded from disk.",
  "event": "dashboard_updated"
}
```

### Option A: GitOps Auto-Sync (Recommended)

The most reliable way to keep the API in sync is to commit dashboard JSON
changes to the repo and let GitHub Actions call the webhook automatically.

A workflow (`.github/workflows/dashboard-sync.yml`) is included in the repo:

```yaml
name: Dashboard Sync
on:
  push:
    branches: [master]
    paths:
      - "dashboards/mandiiq-pipeline.json"
jobs:
  notify-api:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger dashboard cache webhook
        env:
          API_BASE: "https://mandiiq-api.onrender.com"
        run: |
          curl -X POST "${API_BASE}/webhook/grafana-dashboard-update" \
            -H "Content-Type: application/json" \
            -d '{"event":"dashboard_updated","source":"github-actions"}'
```

**Workflow:**
1. Export your Grafana dashboard as JSON → save to `dashboards/mandiiq-pipeline.json`
2. Commit and push to `master`
3. GitHub Actions fires the webhook → API refreshes its cache
4. Grafana picks up the updated dashboard on its next provisioning cycle

### Option B: Grafana Contact Point Webhook

Grafana Alerting can send webhook notifications via Contact Points. While
this is typically used for alert notifications, you can create a dedicated
Contact Point that POSTs to the webhook endpoint whenever an alert fires.

**Setup:**
1. **Grafana** → **Alerting** → **Contact points** → **New contact point**
2. Name: `MandiIQ Dashboard Refresh`
3. Type: **Webhook**
4. URL: `https://mandiiq-api.onrender.com/webhook/grafana-dashboard-update`
5. HTTP Method: `POST`
6. Optional: Add a custom message with the dashboard name

> **Note:** This approach triggers on alerts, not on dashboard saves directly.
> For true dashboard-save detection, use the GitOps approach (Option A).

### Option C: Periodic Polling (Grafana API)

If you need near-real-time detection without Git, you can run a lightweight
cron job that polls the Grafana API for dashboard version changes:

```bash
# Check the current dashboard version from Grafana's API
GRAFANA_URL="https://your-grafana.example.com"
GRAFANA_API_KEY="glsa_..."
DASHBOARD_UID="mandiiq-pipeline"

CURRENT_VERSION=$(curl -s -H "Authorization: Bearer ${GRAFANA_API_KEY}" \
  "${GRAFANA_URL}/api/dashboards/uid/${DASHBOARD_UID}" \
  | python -c "import json,sys; print(json.load(sys.stdin).get('dashboard',{}).get('version',0))")

# Store and compare with the last known version
LAST_VERSION_FILE="/tmp/last_grafana_version"
LAST_VERSION=$(cat "$LAST_VERSION_FILE" 2>/dev/null || echo "0")

if [ "$CURRENT_VERSION" -gt "$LAST_VERSION" ]; then
  echo "$CURRENT_VERSION" > "$LAST_VERSION_FILE"
  curl -X POST https://mandiiq-api.onrender.com/webhook/grafana-dashboard-update \
    -H "Content-Type: application/json" \
    -d '{"event":"dashboard_updated","version":'$CURRENT_VERSION'}'
fi
```

Run this as a cron job (`*/5 * * * *`) for dashboard-change detection within
5 minutes of every save.


## Admin: Refreshing the Dashboard Cache

The dashboard JSON is loaded from disk once at server startup and cached in memory.
If you update the `mandiiq-pipeline.json` file on disk (e.g., after editing panels),
you can push the changes live without a full server restart:

```bash
curl -X POST https://mandiiq-api.onrender.com/admin/refresh-dashboard-cache
```

Expected response:

```json
{"status":"ok","message":"Dashboard cache cleared and JSON reloaded from disk."}
```

This endpoint:

1. **Clears the LRU cache** — the next request with a custom `?datasource=` triggers
   a fresh deepcopy + patch from the newly loaded JSON.
2. **Re-reads the JSON** from the configured `_dashboard_path` on disk and updates
   both the inner dashboard body and the full export (with `__inputs`/`__requires`).

If the JSON file is missing or unreadable, the endpoint returns an error status
and the server continues serving the previously loaded dashboard.

> **Tip:** Combine this with the `?datasource=` query parameter to test changes
> without affecting production Grafana instances: patch the JSON locally, scp it
> to the server, call the refresh endpoint, then verify with
> `curl .../grafana-dashboard?datasource=Staging`.


## Alerting

Grafana alert rules can be added for:

- **Pipeline failure**: `mandiiq_pipeline_failure_total > 0`
- **Run duration spike**: `mandiiq_last_pipeline_duration_seconds > 120`
- **Stale pipeline**: `mandiiq_last_pipeline_run_age_seconds > 3600` (1h)
- **API errors**: `mandiiq_api_calls_total{result="failure"} > 10`
