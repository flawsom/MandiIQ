"""Generate XML sitemaps for MandiIQ (claude-seo methodology).

Pure stdlib (xml.sax.saxutils for safe escaping). The function
`write_sitemaps()` is safe to call any time (deploy script, CI, or a
manual run); it never raises and always produces a valid index + files.
"""

from __future__ import annotations

import os
import datetime
from xml.sax.saxutils import escape

# Reuse the canonical route list / site url from the dashboard seo module.
try:
    from mandi_rdd.dashboard.seo import SITE_URL, PAGE_SEO, SEO_ASSET_BASE
except Exception:  # pragma: no cover - import safety
    SITE_URL = "https://mandiiq.unifies.codes"
    PAGE_SEO = {}
    SEO_ASSET_BASE = "https://flawsom.github.io/MandiIQ/seo"

# Where the sitemap files are *published* (GitHub Pages). The <loc> entries
# inside point at SITE_URL (the canonical app domain); the index itself points
# at the Pages-hosted file. Streamlit reserves /static/, so we cannot publish
# here.
SITEMAP_PUBLISH_BASE = SEO_ASSET_BASE

# Routes that should be indexable (exclude noindex ones).
_INDEXABLE = [
    k for k, v in PAGE_SEO.items()
    if v.get("robots", "index,follow").startswith("index") and k != ""
]
# Always include home.
if "" not in _INDEXABLE:
    _INDEXABLE = [""] + _INDEXABLE
