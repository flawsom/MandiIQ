"""SEO utilities for MandiIQ — implements the claude-seo methodology.

Design rules (Karpathy minimalism + defensive coding):
  * Pure, dependency-free, no network calls at import or render time.
  * Every public function is wrapped so it can NEVER raise — SEO must
    never be able to break page rendering.
  * No changes to existing page render() logic are required; the app
    injects these tags centrally via `inject_page_seo(url_path)`.

All tags are injected into the Streamlit body (the supported pattern for
this app) using st.markdown(unsafe_allow_html=True).
"""

from __future__ import annotations

import json
from typing import Dict, Optional

# ──────────────────────────────────────────────────────────────
# Site constants (single source of truth)
# ──────────────────────────────────────────────────────────────
# Option B: the app domain is the single canonical. GitHub Pages still hosts
# the crawler-visible landing DOCUMENT (LANDING_URL below) and the /seo assets,
# but every canonical/og:url/JSON-LD URL points at the app domain.
SITE_URL = "https://mandiiq.unifies.codes"
APP_URL = "https://mandiiq.unifies.codes"
SITE_NAME = "MandiIQ"
TWITTER_HANDLE = "@MandiIQ"

# Crawler-visible entry point (GitHub Pages). Streamlit apps serve a JS shell,
# so the SEO-facing landing page (raw HTML) carries the OG/Twitter/JSON-LD
# tags and links to the live app. SEO assets are served from /seo/ on Pages.
SEO_ASSET_BASE = "https://flawsom.github.io/MandiIQ/seo"

DEFAULT_DESCRIPTION = (
    "MandiIQ: Indian mandi (APMC) price intelligence \u2014 regression-discontinuity "
    "causal analysis on IMD rainfall-deficit thresholds, ML price forecasting, and an "
    "AI procurement assistant over live Agmarknet data."
)

# Per-route metadata. Keys match st.Page url_path values used in app.py.
# `robots` lets error/onboarding pages be excluded from indexing.
PAGE_SEO: Dict[str, Dict[str, str]] = {
    "": {
        "title": "MandiIQ \u2014 Indian Mandi Price Intelligence System",
        "description": DEFAULT_DESCRIPTION,
        "og_type": "website",
        "robots": "index,follow",
    },
    "discontinuity": {
        "title": "Discontinuity Explorer \u2014 Rainfall-Deficit Price Jumps | MandiIQ",
        "description": (
            "Regression-discontinuity causal estimates of how Indian mandi prices jump "
            "when IMD rainfall crosses the deficiency threshold. Explore the effect size, "
            "placebo tests, and bandwidth sensitivity."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "forecast": {
        "title": "Price Forecast Explorer | MandiIQ",
        "description": (
            "ML price forecasts for Indian mandi commodities with uncertainty bands, "
            "backtest MAPE, and training-window controls."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "risk-map": {
        "title": "Procurement Risk Map | MandiIQ",
        "description": (
            "District-level procurement risk map combining price volatility, rainfall "
            "deficiency, and supply discontinuity signals."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "satellite": {
        "title": "Satellite View \u2014 NDVI & Crop Health | MandiIQ",
        "description": (
            "Sentinel-2 NDVI vegetation-index overlays for mandi catchment districts to "
            "anticipate supply shocks before they hit prices."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "discount-simulator": {
        "title": "Discount Simulator | MandiIQ",
        "description": (
            "Simulate procured-lot discount rates against fair modal price and forecast "
            "bands to protect margin on mandi purchases."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "ask": {
        "title": "Ask MandiIQ \u2014 AI Procurement Assistant",
        "description": (
            "Ask the MandiIQ procurement assistant about prices, forecasts, and causal "
            "effects across Indian mandi commodities."
        ),
        "og_type": "website",
        "robots": "index,follow",
    },
    "about": {
        "title": "Methodology & About | MandiIQ",
        "description": (
            "How MandiIQ computes regression-discontinuity price effects, ML forecasts, "
            "and procurement risk over Agmarknet, IMD, and Sentinel-2 data."
        ),
        "og_type": "article",
        "robots": "index,follow",
    },
    "settings": {
        "title": "Settings | MandiIQ",
        "description": "Configure MandiIQ display, commodity, and model preferences.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
    "onboarding": {
        "title": "Get Started | MandiIQ",
        "description": "Quick start guide for the MandiIQ price intelligence system.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
    "loading": {
        "title": "Loading | MandiIQ",
        "description": "Loading MandiIQ.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
    "404": {
        "title": "Page Not Found | MandiIQ",
        "description": "The requested MandiIQ page was not found.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
    "error/model-unavailable": {
        "title": "Model Unavailable | MandiIQ",
        "description": "The MandiIQ model backend is temporarily unavailable.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
    "error/no-data": {
        "title": "No Data | MandiIQ",
        "description": "MandiIQ has no data for the selected filters yet.",
        "og_type": "website",
        "robots": "noindex,follow",
    },
}


def _safe_get(url_path: str) -> Dict[str, str]:
    """Return SEO dict for a route, falling back to the home route."""
    if url_path in PAGE_SEO:
        return PAGE_SEO[url_path]
    return PAGE_SEO[""]


def canonical_url(url_path: str) -> str:
    if not url_path:
        return SITE_URL + "/"
    return SITE_URL + "/" + url_path.strip("/") + "/"


def json_ld() -> str:
    """Return a JSON-LD <script> block: Organization + WebSite + Dataset.

    Defensive: returns empty string on any failure.
    """
    try:
        org = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": SITE_NAME,
            "url": SITE_URL,
            "logo": PROXY_OG_IMAGE,
            "sameAs": [
                "https://twitter.com/MandiIQ",
            ],
        }
        website = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": SITE_URL,
            "potentialAction": {
                "@type": "SearchAction",
                "target": SITE_URL + "/ask?q={search_term_string}",
                "query-input": "required name=search_term_string",
            },
        }
        dataset = {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "MandiIQ Indian Mandi Price & Rainfall Discontinuity Dataset",
            "description": DEFAULT_DESCRIPTION,
            "url": SITE_URL,
            "creator": {"@type": "Organization", "name": SITE_NAME},
            "distribution": [
                {
                    "@type": "DataDownload",
                    "encodingFormat": "application/duckdb",
                    "contentUrl": SITE_URL + "/about",
                }
            ],
            "variableMeasured": [
                "modal_price",
                "arrival_volume",
                "rainfall_departure_pct",
                "rdd_effect",
            ],
            "spatialCoverage": "India",
        }
        payload = json.dumps([org, website, dataset], ensure_ascii=False)
        return f'<script type="application/ld+json">{payload}</script>'
    except Exception:
        return ""


def page_meta_tags(url_path: str = "") -> str:
    """Return the full SEO <head>-equivalent tag block for a route.

    Safe: returns at least a robots noindex fallback on any error.
    """
    try:
        meta = _safe_get(url_path)
        canon = canonical_url(url_path)
        title = meta.get("title", SITE_SEO_HOME_TITLE)
        desc = meta.get("description", DEFAULT_DESCRIPTION)
        og_type = meta.get("og_type", "website")
        robots = meta.get("robots", "index,follow")
        og_image = SEO_ASSET_BASE + "/og-image.png"

        tags = []
        # Canonical + robots
        tags.append(f'<link rel="canonical" href="{canon}" />')
        tags.append(f'<meta name="robots" content="{robots}" />')
        # Basic
        tags.append(f'<meta name="description" content="{desc}" />')
        # Open Graph
        tags.append(f'<meta property="og:type" content="{og_type}" />')
        tags.append(f'<meta property="og:site_name" content="{SITE_NAME}" />')
        tags.append(f'<meta property="og:title" content="{title}" />')
        tags.append(f'<meta property="og:description" content="{desc}" />')
        tags.append(f'<meta property="og:url" content="{canon}" />')
        tags.append(f'<meta property="og:image" content="{og_image}" />')
        # Twitter
        tags.append('<meta name="twitter:card" content="summary_large_image" />')
        tags.append(f'<meta name="twitter:site" content="{TWITTER_HANDLE}" />')
        tags.append(f'<meta name="twitter:title" content="{title}" />')
        tags.append(f'<meta name="twitter:description" content="{desc}" />')
        tags.append(f'<meta name="twitter:image" content="{og_image}" />')
        # Structured data
        tags.append(json_ld())
        return "\n".join(tags)
    except Exception:
        return '<meta name="robots" content="noindex,follow" />'


# Title used as a fallback constant (kept out of the f-string above for clarity)
SITE_SEO_HOME_TITLE = PAGE_SEO[""]["title"]


def inject_page_seo(url_path: str = "") -> str:
    """Convenience: return the tag block (caller does st.markdown(..., unsafe_allow_html=True))."""
    return page_meta_tags(url_path)


def route_seo_js() -> str:
    """Return a JS object literal mirroring PAGE_SEO for head injection.

    Used by build_index_seo.py to make route-aware meta tags appear in the
    raw served <head> (visible to non-JS crawlers / social scrapers).
    """
    try:
        data = {}
        for path, meta in PAGE_SEO.items():
            data[path or "/"] = {
                "title": meta.get("title", SITE_SEO_HOME_TITLE),
                "description": meta.get("description", DEFAULT_DESCRIPTION),
                "robots": meta.get("robots", "index,follow"),
            }
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


# The live app URL. Crawler hits to this host should resolve to rich OG tags
# (Streamlit's own shell is JS-rendered and carries none), so the edge proxy
# serves the landing HTML below for text/html requests.
APP_URL = "https://mandiiq.unifies.codes"
PROXY_LANDING_URL = "https://flawsom.github.io/MandiIQ/"
PROXY_OG_IMAGE = "https://flawsom.github.io/MandiIQ/seo/og-image.png"


def proxy_landing_html() -> str:
    """Return a standalone, crawler-visible HTML doc for the edge proxy.

    This is the single source of truth for what non-JS crawlers / social
    scrapers see at the app URL. Canonical and og:url point to APP_URL (the
    app itself is the canonical resource); the og:image lives on Pages
    because Streamlit cannot serve user files at /static/.
    """
    try:
        org = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": SITE_NAME,
            "url": APP_URL,
            "logo": PROXY_OG_IMAGE,
            "sameAs": ["https://twitter.com/MandiIQ"],
        }
        website = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": SITE_NAME,
            "url": APP_URL,
            "potentialAction": {
                "@type": "SearchAction",
                "target": APP_URL + "/ask?q={search_term_string}",
                "query-input": "required name=search_term_string",
            },
        }
        dataset = {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": "MandiIQ Indian Mandi Price & Rainfall Discontinuity Dataset",
            "description": DEFAULT_DESCRIPTION,
            "url": APP_URL,
            "creator": {"@type": "Organization", "name": SITE_NAME},
            "spatialCoverage": "India",
        }
        ld = json.dumps([org, website, dataset], ensure_ascii=False)
        desc = DEFAULT_DESCRIPTION
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MandiIQ — Indian Mandi Price Intelligence System</title>
<meta name="description" content="{desc}" />
<meta name="robots" content="index,follow" />
<link rel="canonical" href="{APP_URL}/" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="{SITE_NAME}" />
<meta property="og:title" content="MandiIQ — Indian Mandi Price Intelligence System" />
<meta property="og:description" content="{desc}" />
<meta property="og:url" content="{APP_URL}/" />
<meta property="og:image" content="{PROXY_OG_IMAGE}" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:site" content="@MandiIQ" />
<meta name="twitter:title" content="MandiIQ — Indian Mandi Price Intelligence System" />
<meta name="twitter:description" content="{desc}" />
<meta name="twitter:image" content="{PROXY_OG_IMAGE}" />
<script type="application/ld+json">{ld}</script>
</head>
<body>
<a href="{APP_URL}/">Open the live MandiIQ dashboard</a>
</body>
</html>"""
    except Exception:
        return (
            '<!DOCTYPE html><html><head><title>MandiIQ</title>'
            f'<link rel="canonical" href="{APP_URL}/" /></head><body>'
            f'<a href="{APP_URL}/">MandiIQ</a></body></html>'
        )

