---
name: lean-file-analyzer skill
description: A token-lean file/codebase analyzer skill built and installed to save tokens on per-token model APIs (workspace.default.si, Databricks). Done, not yet committed/pushed.
type: project
---

# lean-file-analyzer skill — DONE, installed, not pushed

## What & why
Built to counter the token-burn pattern on per-token model APIs (workspace.default.si, Databricks foundation-model endpoints, OpenRouter). Each time the agent uses its Read tool, the FULL file content enters the context window and is billed as input tokens every subsequent turn. This skill analyzes files OUTSIDE the context window and returns only a bounded digest.

## Location
- `.aionrs/skills/lean-file-analyzer/SKILL.md` — the token discipline (5 rules: tokens-first, digest-before-Read, data-via-schema-never-Read, search-via-count/grep, scope-edits-with-digest)
- `.aionrs/skills/lean-file-analyzer/scripts/lean_analyze.py` — the analyzer, zero non-stdlib hard deps (duckdb/pyarrow/openpyxl degrade gracefully)
- `.aionrs/skills/lean-file-analyzer/lean-file-analyzer.skill` — packaged/validated bundle

## 7 modes (all verified working on this codebase)
- `tokens <path>` — per-file token estimate + read/digest decision (FIRST STEP on any unfamiliar path). >4k = never Read fully.
- `overview <dir>` — file list + extension histogram + size tiers + largest files
- `digest <file>` — line/byte/token counts + top-level signatures + capped head(12)/tail(8)
- `signatures <file|dir>` — def/class names + line numbers, no bodies
- `schema <datafile>` — tables, row counts, cols, 3 sample rows for csv/json/parquet/duckdb/sqlite/xlsx. NEVER Read raw data rows.
- `count <path> -p REGEX` — match tally per file, no line dump
- `grep <path> -p REGEX` — first N matching lines (capped 25)

## Proven savings (this codebase)
- README.md full Read = 7,978 tokens vs digest ~150 tokens (98% saved)
- orchestrator.py = 4,386 tokens vs digest/signatures ~200
- package-lock.json = 19,586 tokens — never touched

## Notes
- Cross-platform; run with `python` on Windows, `python3` elsewhere. Set `$env:PYTHONUTF8="1"` on Windows if console encoding errors occur.
- Token estimate uses chars/4 heuristic (conservative); relative ranking is what matters for the read/digest decision.
- Auto-ignores node_modules .git __pycache__ .venv venv dist build .next .cache.
- NOT yet committed/pushed to https://github.com/flawsom/MIS.git (same uncommitted set as the MandiIQ bug fixes).
