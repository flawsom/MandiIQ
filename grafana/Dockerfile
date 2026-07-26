# Grafana with MandiIQ provisioning
# Self-hosted observability sidecar for Render Blueprints.
# Scrapes /metrics from mandiiq-api and hosts pipeline dashboard.
FROM grafana/grafana:11.4.0

# Copy provisioning configs (datasource + dashboard provider)
# NOTE: COPY paths are relative to Docker build context (repo root),
# NOT the Dockerfile's directory. Hence the grafana/ prefix.
COPY grafana/provisioning/datasources/ /etc/grafana/provisioning/datasources/
COPY grafana/provisioning/dashboards/ /etc/grafana/provisioning/dashboards/

# Copy the dashboard JSON (dashboards/ is at repo root)
COPY dashboards/mandiiq-pipeline.json /var/lib/grafana/dashboards/mandiiq-pipeline.json

EXPOSE 3000
