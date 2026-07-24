# Karpathy/CLAUDE.md Optimization — MandiIQ Codebase

## Summary
Applied the Karpathy editorial skill patterns (from CLAUDE.md) to the MandiIQ Python/Streamlit codebase — 3 surgical simplifications that remove redundant imports with zero behavioral change.

## Principles Applied
- **Simplicity First** — Removed duplicate/redundant import statements. Every line not written is a line that can't break.
- **Surgical Changes** — Each change touched exactly one construct per file. No scope creep.
- **Goal-Driven Execution** — Each file verified with `py_compile` and `importlib` after change. All pass.

## Changes Made (2026-07-20)

### 1. `mandi_rdd/dashboard/pages/settings.py`
- **Before**: `get_connection` imported separately inside 3 different function try/except blocks
- **After**: Single top-level `from mandi_rdd.storage.duckdb_store import get_connection` on line 15
- **Net**: -2 lines of code

### 2. `mandi_rdd/dashboard/pages/risk_map.py`
- **Before**: `get_connection` imported at line 36 (combo with get_curated_commodities), then re-imported alone at line 68
- **After**: Single import at line 36 covers all usages
- **Net**: -1 line of code

### 3. `mandi_rdd/dashboard/pages/executive_overview.py`
- **Before**: `get_api_base` imported on a separate line (line 27) from `inject_theme, commodity_color` (line 17) — same module
- **After**: Merged into single import: `from mandi_rdd.dashboard.theme import inject_theme, commodity_color, get_api_base`
- **Net**: -1 line of code

## Verification
- All 3 files pass `python -m py_compile` (syntax validation)
- All 3 modules load via `importlib.util.spec_from_file_location`
- No behavioral changes — same functions, same imports, same execution path
- Temp scripts (`_opt_*.py`) cleaned up

## CLAUDE.md Source
Downloaded from `https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md` — Karpathy-style editorial principles.
