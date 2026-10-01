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
# - MANDIIQ_REFRESH_INTERVAL_MINUTES=30        (optional; min 5)
# - MANDIIQ_REFRESH_INITIAL_DELAY_S=90         (optional; min 5)
# - MANDIIQ_PRICE_SOURCES=<mirror|resource_id,..>  (optional; extra price hosts)
#     api.data.gov.in is always tried first, so this only adds fallbacks - it
#     never displaces the documented API. Any host must serve the same
#     data.gov.in response shape ({"records": [...], "total": n}).
# - MANDIIQ_CEDA_API_KEY=<token>                   (optional; see below)
#     Token for the CEDA (Ashoka University) Agmarknet archive. It is the one
#     Agmarknet host that answers a cloud network, but it is an ARCHIVE - daily
#     coverage ends around 2025-10 - so it backfills history and cannot make the
#     newest date current. Arm it to fill gaps, not to restore freshness.
# - MANDIIQ_CEDA_LOOKBACK_DAYS=7                   (optional)
# - MANDIIQ_CEDA_MAX_CALLS=150                     (optional)
# - MANDIIQ_DUCKDB_MEMORY_LIMIT=192MB              (optional; rebuild ceiling)
# - MANDIIQ_REFRESH_SCOPE=light                     (optional; "full" runs the
#     analysis recompute too - see the self-refresh section for why the default
#     is light and what it leaves out)
# - MANDIIQ_ALLOW_AUTO_REBUILD=1                    (optional; lets the workflow
#     run the prices rebuild unattended. Off unless an operator sets it, because
#     the rebuild is what restarts a small container)

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
# Two instances, and which one the site is reading
# ─────────────────────────────────────────────────────────────────────────────
# Two services run the same image behind two hostnames:
#
#   primary  https://p01--mandiiq--x4n8x4gkmzht.code.run
#   mirror   https://p01--mandiiq--zbvjrztgjqgw.code.run
#
# On 2026-10-01 the primary answered `503 no healthy upstream` on /health, /
# and /docs while the mirror answered /health normally - the edge had no
# healthy container behind it. Every docs page and the landing page pointed at
# the primary alone, so the whole public surface went dark with it.
# docs/assets/site.js now probes /health on each host in order, remembers the
# first that answers (sessionStorage), re-points the host-qualified nav and
# footer links at the winner, and labels the nav LED with the instance - the
# landing pill and the live console both read it through
# window.MandiiqShell.resolveApi(). The mirror is a failover, not an equal: it
# has been observed running an older build than the primary, so when the pages
# fall back they say so instead of quietly mixing two builds' numbers.
#
# Check both by hand:
#   for h in x4n8x4gkmzht zbvjrztgjqgw; do
#     curl -s -o /dev/null -w "$h %{http_code}\n" --max-time 15 \
#       "https://p01--mandiiq--$h.code.run/health"
#   done
# A 503 here (rather than a JSON body) is the platform edge reporting no
# healthy container: it is a service-level restart/deploy problem, not a bug in
# the pipeline, and no client-side change can fix it.
#
# https://mandiiq.unifies.codes/status.html is that same check as a public page.
# It probes both instances directly, reports each one's build, staleness and
# /health fields side by side, marks the field where two live instances really
# disagree, and lists the measured reasons the newest arrival is not from today.
# It takes its host list from docs/assets/site.js, so it cannot drift from the
# rest of the site.

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
# .github/workflows/refresh-live-data.yml picks whichever instance answers
# /health, then triggers POST /refresh on it every 15
# minutes and then verifies /health and /data-quality, so a frozen warehouse
# fails a workflow within the quarter hour instead of going unnoticed for
# weeks. A self-throttle step skips a run whose predecessor is younger than
# MIN_REFRESH_GAP_MINUTES (20), and `force: true` on a manual dispatch bypasses
# it - so a burst of workflows cannot stack up pipeline runs on a free tier.

# ─────────────────────────────────────────────────────────────────────────────
# Self-refresh (the reason the data was frozen before 2026-09-30)
# ─────────────────────────────────────────────────────────────────────────────
# The container refreshes its own warehouse. On boot it waits 90 s and then
# runs the full pipeline, repeating every 30 minutes. Nothing external has to
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
#   MANDIIQ_REFRESH_INTERVAL_MINUTES=30     how often it runs (min 5)
#   MANDIIQ_REFRESH_INITIAL_DELAY_S=90      delay before the first run (min 5)
#
# Boot used to be the exception to all of that. If `prices` looked empty at
# startup, `lifespan` started the FULL pipeline in its own thread immediately -
# i.e. the heaviest work this service does began before the readiness probe
# could pass, on the same 512 MB the probe had to fit in. An OOM there is a
# crash loop, and the platform edge reports a crash loop as
# `503 no healthy upstream` on every route, which is unrecoverable from here.
#
# That thread is gone. Boot now only logs what it found, and the work goes to
# whatever runs while the container is already serving: the scheduler tick
# below (after MANDIIQ_REFRESH_INITIAL_DELAY_S), POST /refresh, or the admin
# recovery endpoints. MANDIIQ_SELF_REFRESH=0 now means no pipeline runs in the
# container at all, boot included - so an operator who sets it to stop the
# in-container rebuilds is no longer surprised by one at startup.
#
# Each tick runs at a declared SCOPE, and the scope is what makes it
# survivable on a small tier:
#
#   MANDIIQ_REFRESH_SCOPE=light   (the default)
#     read-only integrity check, date repair, prices, rainfall, state backfill.
#     No satellite fetch, no index rebuild, no RDD/forecast/classifier
#     recompute, no narratives.
#   MANDIIQ_REFRESH_SCOPE=full
#     the whole pipeline, for an instance with the memory for it.
#
# The analysis half is a recompute over data the price and rainfall steps have
# already stored, so a light run costs the freshness of nothing but the
# analysis - and a run that does not fit the box is not a run. What a run left
# out is recorded, never implied: /health.refresh_skipped_steps, and the
# "scope"/"steps_skipped" keys in last_ingest_status.json. Ask for the heavy
# work explicitly when you want it, on an instance that can afford it:
#
#   curl -sS -X POST "https://.../refresh?scope=full"
#
# Why light is the default: on 2026-10-01 the primary - a 2.4.0 build whose
# self-refresh tick finally ran the pipeline that the DuckDB probe bug had been
# suppressing since July - restarted repeatedly while it worked, and the edge
# answered 503 on every route between restarts.
#
# Two other protections landed with it:
#
#   * /health never waits on the pipeline. The warehouse counts and the date
#     scan come from a snapshot while a run is in flight, and refresh when it
#     is over (or sooner, if the warehouse file changed underneath them - a
#     restore or a rebuild swaps it). counts_age_s reports how old the numbers
#     are. A liveness probe that queues behind the work it is checking is read
#     by the platform as a dead container, and a dead container is restarted.
#   * a run writes data/refresh_state.json on the volume while it is in flight,
#     naming the step it is inside. A marker still there at boot means the
#     previous process was killed mid-run; /health then reports
#     unclean_refresh_runs and last_unclean_refresh, and the next boot waits
#     initial_delay * 2^deaths (capped by MANDIIQ_REFRESH_BACKOFF_MAX_S,
#     default 1800) before repeating the work that killed it. One fatal run
#     cannot become a loop that fires every 90 seconds.
#
# The prices rebuild - the other thing that can kill a small container - is an
# operator decision now, not an automatic one:
#
#   MANDIIQ_ALLOW_AUTO_REBUILD=1   arm the workflow's rebuild for this service
#
# Without it, refresh-live-data.yml notices index_fault_pending, says why it is
# not acting, and leaves the warehouse stale and serving. With it (on a service
# with the memory for the rebuild) the automated recovery resumes; the version
# gate still applies on top of the flag.
#
# Tunables added by all of this (all optional):
#   MANDIIQ_REFRESH_SCOPE=light|full          what an in-container tick may do
#   MANDIIQ_REFRESH_BACKOFF_MAX_S=1800        ceiling on the wait after a death
#   MANDIIQ_HEALTH_COUNT_TTL_S=60             how long a counts snapshot is fresh
#   MANDIIQ_ALLOW_AUTO_REBUILD=1              allow the workflow to rebuild

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
# Both are gated on the build that is actually running, because a flag can be
# wrong and a version cannot:
#
#   * /health.safe_recovery is derived from app.version (>= 2.4.0), not the
#     literal `True` it used to be. Before that, a build could advertise a
#     capability it did not have.
#   * refresh-live-data.yml independently refuses to run recovery unless the
#     deployed `version` is >= 2.4.0. A flag on an already-deployed build cannot
#     be corrected by editing this repo, and on 2026-10-01 a 2.3.0 container
#     with a recorded index fault was sent /admin/rebuild-prices, OOM-killed
#     during the rebuild, and answered `503 no healthy upstream` on every route
#     afterwards. Refusing leaves the warehouse stale and serving, which is
#     recoverable; a crash loop is not.
#
# So: deploy 2.4.0 and the next scheduled run clears index_fault_pending by
# itself - unless the instance has not been cleared to rebuild (2.4.1's
# MANDIIQ_ALLOW_AUTO_REBUILD), in which case the workflow skips the repair and
# says why, and a light tick reports the fault rather than starting it.
#
# That deploy landed on 2026-10-01. The primary answers /health 200 with
# version 2.4.0 and safe_recovery true, and every 2.4.0 route answers 200 -
# /fdr and /spec-curve/Onion included. /analytics, /conformal, /drift,
# /tail-risk, /dml, /nowcast, /forecast and /risk-score are commodity paths:
# they answer at /analytics/Onion and 404 at /analytics, so a probe that omits
# the commodity reports a 404 that is not a missing route.
#
# 2.4.1 is the build that made the tick itself survivable: scope, the backoff
# after a run is killed mid-flight, and a /health that answers from a snapshot
# while the pipeline writes. `version` is how you tell it from the 2.4.0 build
# that crash-looped on 2026-10-01.
#
# The refresh-live-data.yml workflow runs both of the recovery operations above
# when /health says index_fault_pending, or when n_prices is 0 - but the
# rebuild is now behind MANDIIQ_ALLOW_AUTO_REBUILD (see the self-refresh
# section), so with the flag unset the workflow reports the pending fault and
# leaves the warehouse stale-but-serving instead of restarting a small
# container to clear it.
# If the upstream feed (api.data.gov.in) is unreachable, /health reports a
# degraded run and the warehouse keeps serving what it has - the pipeline
# skips the price fetch and still refreshes rainfall, RDD and the forecast.
#
# ─────────────────────────────────────────────────────────────────────────────
# Price sources and the resumable backfill
# ─────────────────────────────────────────────────────────────────────────────
# A single hard-coded host made the whole feed depend on one DNS name. The
# fetch now walks a chain of hosts - api.data.gov.in first, then any
# MANDIIQ_PRICE_SOURCES mirrors. A 4xx other than 429 stops the walk
# immediately (a bad key fails the same everywhere); a refused connection,
# timeout or 5xx is retried on the current host and then falls through to the
# next, so one host being down degrades to "slower" rather than "no prices".
#
# agmarknet.gov.in is not in that chain on purpose: it is a portal, not a
# data.gov.in resource endpoint, so agmarknet.gov.in/resource/<id> answers 403
# for every caller and could never serve a row.
#
# The walk is also resumable. Each run reads its start offset from the
# `ingest_cursors` table, and when the MANDIIQ_PRICE_FETCH_MAX_SECONDS budget
# cuts a pass short it writes the offset it reached back. Before this, every
# run restarted at offset 0 and only re-read the newest pages, so the older
# tail of the archive was never reached however many runs passed. Re-reading a
# resumed page is safe because upserts are idempotent.
#
# Inspect or reset the cursor directly:
#   SELECT source, cursor_offset, total_records, updated_at FROM ingest_cursors;
#   DELETE FROM ingest_cursors WHERE source = 'prices';   -- restart from the top

# ─────────────────────────────────────────────────────────────────────────────
# Why the warehouse goes stale, and the only fix that works
# ─────────────────────────────────────────────────────────────────────────────
# Diagnosed 2026-10-01, from three independent networks (a GitHub Actions
# runner, a Daytona sandbox and the Northflank container):
#
#   * api.data.gov.in completes the TCP handshake and then drops every TLS
#     handshake ("unexpected eof"). No TLS version or cipher set gets in, and
#     a bare GET without a key behaves the same - it is the network path, not
#     the request.
#   * www.data.gov.in is fronted by Akamai and answers "503 Service Unavailable
#     - Fail to connect": the CDN cannot reach its own origin either.
#   * the public CORS relays answer 522 for the same URL, which rules out
#     Cloudflare Workers and similar side-channels: only an Indian network
#     reaches the host.
#
# So no retry, mirror-list or worker on a cloud network can restore the
# documented feed, and "days_behind" growing past 3 is expected until the host
# comes back or another source is armed. The pipeline is honest about it: the
# run is reported degraded, /health shows status "stale", and the warehouse
# keeps serving what it has instead of pretending.
#
# CEDA (Centre for Economic Data and Analysis, Ashoka University) is the one
# Agmarknet mirror that answers a cloud network. It is reachable, it works,
# and it is NOT a live feed. Measured on 2026-10-01 with a valid token:
#
#   POST https://api.ceda.ashoka.edu.in/v1/agmarknet/prices
#     {"commodity_id": 1, "state_id": 0, "from_date": ..., "to_date": ...}
#     -> {"output": {"type": "success", "message": "Data exists",
#                     "data": [{date, commodity_id, census_state_id,
#                               census_district_id, market_id,
#                               min_price, max_price, modal_price}]}}
#
#   * coverage: full daily rows up to about 2025-10. Every window after that
#     answers `"message": "No data exists"` with an empty data array
#     (2025-07: 31 rows; 2025-09: 31 rows; 2025-10: 28 rows; 2025-11 onward: 0).
#     The archive is roughly eleven months behind, so it can FILL HISTORY but
#     it can never make the newest date current.
#   * ids: 453 commodities, 36 states, 640 districts. Onion is 23, Tomato 78,
#     Wheat 1, Potato 24. Only real ids return rows - an unknown id and an
#     empty window are indistinguishable in the response.
#   * rate limit: a short burst of calls earns HTTP 429 with
#     `Retry-After: 1833`, i.e. a 30 minute lockout. That is why requests are
#     paced (MANDIIQ_CEDA_MIN_INTERVAL_S, default 1s) and why a long
#     Retry-After is raised as CedaRateLimited instead of being slept through.
#
# So arming CEDA buys a backfill, not freshness. Set MANDIIQ_CEDA_API_KEY (a
# token comes from https://api.ceda.ashoka.edu.in/documentation/) when you
# want to fill gaps in the historical record; it will not close the gap
# between the newest date in the warehouse and today. Do not describe it as
# the live fallback in any status page - the honest report is "archive only".
#
# Rows land in the same `prices` table with market = district name and
# variety/grade = "Agmarknet daily (CEDA)", so they never collide with the
# variety-level rows the primary feed writes. The walk is bounded by
# MANDIIQ_CEDA_MAX_CALLS and resumable through the same `ingest_cursors`
# table (`source = 'prices_ceda'`).
#
# eNAM (enam.gov.in) answers HTTP 200 from a cloud network and its Agmarknet
# dashboard is current - the page carries today's date at render time. The data
# behind it does not. Measured 2026-10-01 from an external network:
#
#   * the controller the dashboard's own JavaScript calls is `Agm_ctrl`;
#     `Ajax_ctrl` only serves the CSV export form. Both return HTTP 500 with an
#     empty body.
#   * the dashboard hands out a `ci_session` cookie, and the 500 comes back
#     with that cookie attached - so it is not a missing-session problem.
#   * POST and GET, with and without Referer/Origin/X-Requested-With/Accept, at
#     `/index.php/Agm_ctrl/...` and with a trailing slash: 500 with an empty
#     body every time, in about one second, from nginx + CodeIgniter.
#
# That is the application refusing the caller (a server-side or geo condition),
# not a malformed request, so eNAM is not a usable live source from here. It is
# still probed - /admin/source-probe posts the page's own price query with the
# session it just minted and reports `cookie_minted`, `rows` and a verdict - so
# if eNAM starts answering, the next probe says so and an adapter is worth
# writing. Until then the honest reading is: dashboard reachable, data 500.
#
# Verify every path from production without waiting for the next tick:
#
#   curl -sS "$API/admin/source-probe" | python3 -m json.tool
#
# It reports, per configured host, whether it answered (and the newest arrival
# date it serves), whether the CEDA mirror is armed and reachable, and a
# single verdict field `can_ingest_live_data`. /health also carries
# `last_price_source` and `mirror_configured`, so "stale because upstream is
# dark" and "stale because we are misconfigured" stop looking identical.
