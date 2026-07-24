---
name: MandiIQ design system build state
description: Current state of the 7-phase MandiIQ design system build — phases done, bugs fixed, open items, data gap, deploy pending
type: project
---

# MandiIQ Design System Build — State as of 2026-07-18

## Context
Target: `mandi_rdd/dashboard/` (Streamlit app, port 8501) + `mandi_rdd/api/main.py` (FastAPI, port 8000).
Working dir: `C:\Users\sibap\Downloads\Margin Intelligence System`. Shell: PowerShell 7.
Git remote `mis` → `https://github.com/flawsom/MIS.git` (already configured).

## TL;DR
The 7-phase plan was ~95% implemented BEFORE this work. I ran a bug-fix + verification pass. Found and fixed 6 real bugs. 2 open items remain. Git NOT committed/pushed. Deploy NOT started.

## Phases — ALL 7 DONE (verified)
1. Tokens/config/pin: `.streamlit/config.toml` (turmeric palette), `streamlit==1.59.2` pinned, `styles/design.css` reconciled.
2. Shared theme: `dashboard/theme.py` (inject_theme, inject_atmosphere); `app.py` imports it.
3. Flip-board: `frontend/` (FlipBoard.tsx, main.tsx, package.json, vite.config.ts, index.html), `flip_board.py` wrapper, `dist/` committed.
4. Exec overview: uses flip_board + themed Plotly.
5. Other 4 pages: all import inject_theme/make_themed_figure/render_ledger/commodity_color.
6. README + `.env.example` (4 vars: DATA_GOV_IN_API_KEY, OPENROUTER_API_KEY, MANDIIQ_API_URL, PORT).
7. Deploy: `render.yaml`, `Dockerfile`, `deploy.sh` — at REPO ROOT (not mandi_rdd/).

## Bugs I FIXED (6)
1. `app.py` `from dashboard.theme` → `from mandi_rdd.dashboard.theme` (ModuleNotFoundError on startup; no top-level `dashboard` package).
2. `executive_overview.py` `flip_board(kpis)` dict call → 8 keyword args (value + raw per KPI) + st.metric fallback.
3. `flip_board.py` `float("nan")` → `NaN` literal breaks React JSON.parse. Added sanitization: non-finite raw → None before sending.
4. `risk_forecast.py:126` teal `#2FA787` → turmeric `#E8B14D`.
5. `executive_overview.py` Plotly `color="var(--color-mist)"` (Plotly can't resolve CSS vars; token undefined) → `#5B6572`. Also `#0B0F14`→`#0B0F1E` in causal_explorer.
6. Palette sweep: off-palette red `#E2572B` → documented rust `#D9663B` across causal_explorer, risk_forecast, procurement_advisor, executive_overview. FlipBoard.tsx TS `raw: number | null`.

## OPEN items (2)
1. `frontend/dist/` not rebuilt (edited FlipBoard.tsx). Optional — committed dist works at runtime. Rebuild: `cd mandi_rdd/dashboard/frontend && npm install && npm run build`.
2. Git: ~7 files edited, NONE committed/pushed. Need commit + push to `https://github.com/flawsom/MIS.git`.

## Critical DATA gap (NOT a code bug)
`mandi_rdd/data/mandi_iq.duckdb` (1MB) has tables (prices, rainfall, rdd_results, classification_results, narratives, district_map) but `prices` = 0 ROWS. Second `mandi_rdd/data/mandi_rdd.db` (53KB SQLite) same schema, also empty.
Symptom: app renders but shows "₹nan" / "Run the pipeline to see RDD results". Fix: run ingestion with DATA_GOV_IN_API_KEY (https://api.data.gov.in/manage).
Minor display polish NOT done: "₹nan" should show "—" when effect is None.

## Environment
- Python deps all installed: streamlit 1.59.2, plotly 6.9.0, duckdb, fastapi, pandas, requests.
- Node v24 + npm 11 installed.
- App reads DB via `mandi_rdd/storage/duckdb_store.py` → `DB_PATH = .../data/mandi_iq.duckdb`.
- A streamlit server may still be running in background (PID 22876, localhost:8501). Kill: `Get-Process python | Stop-Process` (kills ALL python — careful).

## Deploy options (not started)
render.yaml at repo root (3 services, pip-only build, dist committed). Docker (Dockerfile copies mandi_rdd/ incl frontend/dist/). Streamlit Cloud. Env vars needed: DATA_GOV_IN_API_KEY, OPENROUTER_API_KEY, MANDIIQ_API_URL (default localhost:8000), PORT (default 8000).

## Palette reference (documented, from design.css)
Turmeric #E8B14D, Ink #0B0F1E, Slate #2E3A55, Cream #F2EFE6, Rust #D9663B (alert/risk), neutral faint #5B6572. Commodity colors: onion #C25B3F, tomato #D9663B, wheat #E8B14D, potato #B89A6A.
