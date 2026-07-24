# Contributing to MandiIQ

Thanks for your interest in MandiIQ. This document covers the things a first-time
contributor needs to know — especially the repo conventions that aren't obvious
from the code alone.

## Canonical remote

The canonical remote for this repository is **`origin`** and it must point at:

```
https://github.com/flawsom/MandiIQ.git
```

Verify before pushing:

```bash
git remote -v
# origin  https://github.com/flawsom/MandiIQ.git (fetch/push)
```

If `origin` points anywhere else (e.g. a `Margin-Intelligence-System` repo), fix it:

```bash
git remote set-url origin https://github.com/flawsom/MandiIQ.git
```

Any other repository (a separate project, a fork you maintain) should use a
**distinct remote name** such as `margin-intelligence` or `fork` — never leave an
unrelated repo as `origin` in a MandiIQ working copy.

## Data storage decision (PRD Phase 6 — external store)

The ingestion pipeline writes its DuckDB database to `mandi_rdd/data/mandi_iq.duckdb`
by default. The path is configurable via the `MANDIIQ_DB_PATH` environment variable
so the database can instead live in an external object store (R2/S3/GCS) or mounted
persistent storage.

**Decision:** we prefer keeping the database **out of git** (PRD Phase 6, option A).
The daily ingestion workflow uploads the refreshed DB to an external store (R2) when
`R2_BUCKET` + credentials are configured as repository secrets. Until those secrets
exist, the workflow falls back to committing the DB to `master` (with a log warning)
so the project still works for a single-owner portfolio deployment.

This is a deliberate tradeoff: a binary committed daily grows history, but it keeps
the demo self-contained without an external account. If you add R2 credentials to
the repo secrets, the git-commit fallback is no longer used.

## Ingestion automation & branch protection (PRD Phase 8)

The nightly ingestion runs as a GitHub Actions schedule (`0 6 * * *`). Its commits
**do not** use `[skip ci]`, so the normal quality gates (test suite + no-mock-data
check) still run against them.

**Branch protection decision (PRD Phase 8, option A):** when `master` is put behind
branch protection for external PRs, the ingestion bot's `GITHUB_TOKEN` actor should
be **exempted** from the protection rules (GitHub supports this). The real gate is the
data-integrity / no-mock-data check that runs on every bot commit — not human review
of automated data commits.

> Turn this on *after* the exemption is configured, not before — enabling protection
> first will silently break the scheduled workflow.

## Local setup

```bash
git clone https://github.com/flawsom/MandiIQ.git
cd MandiIQ
python -m venv .venv && source .venv/bin/activate
pip install -r mandi_rdd/requirements.txt
cp .env.example .env   # set DATA_GOV_IN_API_KEY for live price pulls
```

Run the dashboard:

```bash
streamlit run mandi_rdd/dashboard/app.py
```

Run ingestion manually (same as the CI job):

```bash
python -m mandi_rdd.run_nightly --max-records 5000
```

## API keys & secrets

- `DATA_GOV_IN_API_KEY` — **required** for live price ingestion. A missing or invalid
  key fails the job loudly; there is **no fallback/default key** in the code.
- `GEMINI_API_KEY` / `OPENROUTER_API_KEY` — for the LLM narrative + Ask MandiIQ.
- `OPENMETEO_API_KEY` — optional weather source.

Never commit secrets or a fallback API key.

## Tests

```bash
python -m pytest mandi_rdd/tests/ -q
```

The suite includes `test_no_mock_data.py` (fails the build if any mock/fabricated
data appears) and `test_scheduler_integrity.py` (missing-key-fail, idempotency,
no-`[skip ci]` on the ingest commit).

## Authorship & tooling note

All commits in this repository are authored by **Siba Prasad Panda** (sibaprasadpanda56@gmail.com)
and the nightly ingestion bot (mandiiq-bot). No third-party AI coding assistants are
listed as commit authors or co-authors. Historical references to any such tooling have
been removed from the commit history.
