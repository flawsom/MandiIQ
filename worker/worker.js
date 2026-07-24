/**
 * MandiIQ edge proxy.
 *
 * Streamlit serves a JS shell, so social scrapers / non-JS crawlers see no
 * OpenGraph tags at the app URL. This Worker intercepts text/html GET/HEAD
 * requests for non-asset paths and returns a crawler-visible HTML document
 * carrying OG / Twitter / JSON-LD tags (fetched from the GitHub Pages landing
 * page, cached). All other requests proxy straight to the Streamlit origin.
 *
 * Deploy:  wrangler deploy   (needs CF_API_TOKEN + `UPSTREAM` var)
 */

// Upstream Streamlit origin (the real app behind this zone).
// Set via wrangler.toml `vars.UPSTREAM` or `wrangler secret put UPSTREAM`.
const UPSTREAM = (typeof UPSTREAM !== "undefined" && UPSTREAM) ||
  "https://mandiiq.streamlit.app";

// Where the canonical landing HTML lives (GitHub Pages). Single source of truth.
const LANDING_URL = "https://flawsom.github.io/MandiIQ/";

// Inline fallback if the landing page is unreachable.
const FALLBACK_HTML = `<!DOCTYPE html><html><head><title>MandiIQ</title>`
  + `<link rel="canonical" href="https://mandiiq.unifies.codes/" />`
  + `<meta property="og:title" content="MandiIQ" />`
  + `<meta property="og:image" content="https://flawsom.github.io/MandiIQ/seo/og-image.png" />`
  + `</head><body><a href="https://mandiiq.unifies.codes/">MandiIQ</a></body></html>`;

// Paths that look like static assets should always proxy (never get the shell).
function isAssetPath(pathname) {
  return /\.[a-z0-9]{1,8}$/i.test(pathname) || pathname.startsWith("/static/")
    || pathname.startsWith("/healthz") || pathname === "/robots.txt"
    || pathname === "/sitemap.xml" || pathname.startsWith("/seo/");
}

function wantsHtml(request) {
  const accept = request.headers.get("accept") || "";
  return /text\/html/.test(accept);
}

async function fetchLanding() {
  try {
    const res = await fetch(LANDING_URL, {
      method: "GET",
      headers: { "user-agent": "mandiiq-seo-proxy" },
      cf: { cacheTtl: 86400, cacheEverything: true },
    });
    if (res.ok) return new Response(await res.text(), {
      status: 200,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "public, max-age=3600",
      },
    });
  } catch (_) { /* fall through */ }
  return new Response(FALLBACK_HTML, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

async function proxyToUpstream(request, upstream) {
  const origin = upstream || UPSTREAM;
  const target = new URL(request.url);
  target.hostname = new URL(origin).hostname;
  target.protocol = new URL(origin).protocol;
  const init = {
    method: request.method,
    headers: request.headers,
    redirect: "follow",
    // Close the edge->origin connection quickly.
    cf: { timeout: 60 },
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
  }
  return fetch(target.toString(), init);
}

export default {
  async fetch(request, env, ctx) {
    const upstream = env.UPSTREAM || UPSTREAM;
    const url = new URL(request.url);

    // Crawler / social-scraper hit -> serve the SEO landing shell.
    if ((request.method === "GET" || request.method === "HEAD")
        && wantsHtml(request) && !isAssetPath(url.pathname)) {
      return fetchLanding();
    }

    // Everything else (the Streamlit app, assets, APIs) proxies through.
    return proxyToUpstream(request, upstream);
  },
};
