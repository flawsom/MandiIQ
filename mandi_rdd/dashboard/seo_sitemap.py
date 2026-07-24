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


def _url_block(loc: str, lastmod: str) -> str:
    return (
        "  <url>\n"
        f"    <loc>{escape(loc)}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        "    <changefreq>daily</changefreq>\n"
        "    <priority>0.8</priority>\n"
        "  </url>\n"
    )


def write_sitemaps(out_dir: str = "static") -> bool:
    """Write sitemap-index.xml + sitemap-0001.xml into out_dir.

    Returns True on success, False on any error (never raises).
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        today = datetime.date.today().isoformat()
        locs = [
            (SITE_URL + "/" if k == "" else SITE_URL + "/" + k.strip("/") + "/")
            for k in _INDEXABLE
        ]
        urls_xml = "".join(_url_block(loc, today) for loc in locs)
        sitemap_body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls_xml}"
            "</urlset>\n"
        )
        sm_path = os.path.join(out_dir, "sitemap-0001.xml")
        with open(sm_path, "w", encoding="utf-8") as f:
            f.write(sitemap_body)

        index_body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            "  <sitemap>\n"
            f"    <loc>{escape(SITEMAP_PUBLISH_BASE + '/sitemap-0001.xml')}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            "  </sitemap>\n"
            "</sitemapindex>\n"
        )
        idx_path = os.path.join(out_dir, "sitemap-index.xml")
        with open(idx_path, "w", encoding="utf-8") as f:
            f.write(index_body)
        return True
    except Exception:
        return False
