---
name: prd-v3-gap-analysis
description: Complete gap analysis of MandiIQ codebase against PRD-1 v3 specification (July 2026). Covers 14 sitemap routes, Global Shell (4 sections), Component Library (10 categories), Page Content (6 main + error pages), Motion Catalog (9 animations), Responsive Grid (3 breakpoints), and Build Directives (8 priorities). Key finding: architecture must refactor from st.tabs() to st.navigation().
type: reference
---

# MandiIQ PRD v3 — Gap Analysis

## Architecture Status
- Current: 5-tab layout via `st.tabs()` in `app.py`
- Required: 14-route `st.navigation()`-based architecture
- **CRITICAL**: All new PRD pages exist as files but are NOT connected to navigation

## Summary Status
| Area | Done | Partial | Missing |
|------|------|---------|---------|
| Sitemap Routes | 0 | 14 | 0 (files exist, none routed via st.navigation) |
| Global Shell (Part B) | 0 | 0 | 4 (sidebar, topbar, footer, breadcrumb ALL missing) |
| Component Lib (Part C) | 0 | 0 | 10 (all categories need implementation) |
| Page Content (Part D) | 8 | 4 | 2 (pages largely complete — risk_map, satellite, etc. well-implemented) |
| Motion (Part E) | 1 | 1 | 8 (atmosphere blobs done) |
| Responsive Grid (Part F) | 0 | 0 | 1 |
| Build Directives (Part G) | 1 | 2 | 5 (RDD robustness done) |

## Critical Path
1. Refactor app.py → st.navigation()
2. Implement Global Shell (Sidebar/TopBar/Footer/Breadcrumb)
3. Implement Component Library (all 10 categories with all states)
4. Wire up all page zones per Part D spec
5. Animation + WCAG polish
6. No-mock audit + README + env updates

## Key Files
- ANALYSIS.md at repo root (full 443-line gap analysis)
- .env.example (updated with all required variables)
- README.md (rewritten for both MAANG recruiters and users)
