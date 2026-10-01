/* ==========================================================================
   MandiIQ shared site shell behaviour
   - Highlights the current page in the navigation.
   - Probes each hosted instance of the API and keeps talking to the one that
     actually answers, driving the small status LED in the nav.

   Two independently hosted instances serve the same image behind two
   hostnames. On 2026-10-01 the primary answered HTTP 503 "no healthy
   upstream" on every route while the mirror answered normally, and every page
   on this site went blank at once because each one only knew one hostname.
   So the list below is now walked in order and the winner is remembered for
   the rest of the session: a dead host degrades the page to the other
   instance instead of removing the page.

   The pages above this one own their own data rendering; they get a base URL
   from window.MandiiqShell.resolveApi() and this file only ever writes to
   [data-shell-status].
   ========================================================================== */
(function () {
  "use strict";

  var HOSTS = [
    { base: "https://p01--mandiiq--x4n8x4gkmzht.code.run", label: "primary" },
    { base: "https://p01--mandiiq--zbvjrztgjqgw.code.run", label: "mirror" }
  ];

  var API = window.MANDIIQ_API || HOSTS[0].base;
  var STALE_AFTER_DAYS = 3;
  var PROBE_TIMEOUT_MS = 9000;
  var CACHE_KEY = "mandiiq.api.base";
  var resolved = null;
  var pending = null;

  /* -----------------------------------------------------------------------
     Single source of truth for the site map. Every page renders its
     navigation and footer from this list, so a new surface is added once and
     appears everywhere instead of drifting page by page.
     ----------------------------------------------------------------------- */
  var PAGES = [
    { href: "./index.html", label: "Home" },
    { href: "./overview.html", label: "Overview" },
    { href: "./live.html", label: "Live console" },
    { href: "./status.html", label: "Status" },
    { href: "./heartbeat-dashboard.html", label: "Heartbeat" },
    { href: "./system_design.md", label: "System design" },
    { href: API + "/docs", label: "API", external: true }
  ];

  var EXTERNAL = [
    { href: "https://mandiiq.streamlit.app/", label: "Streamlit cockpit" },
    { href: API + "/health", label: "/health" },
    { href: API + "/data-quality", label: "/data-quality" },
    { href: API + "/freshness", label: "/freshness" },
    { href: API + "/admin/source-probe", label: "/admin/source-probe" }
  ];

  var REPO = "https://github.com/flawsom/MandiIQ";

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function buildNav() {
    var host = document.querySelector("[data-site-nav]");
    if (!host) return;
    var links = PAGES.map(function (p) {
      return '<li><a href="' + esc(p.href) + '"' +
        (p.external ? ' class="is-external" target="_blank" rel="noopener"' : "") +
        ">" + esc(p.label) + "</a></li>";
    }).join("");
    host.className = "site-nav";
    host.innerHTML =
      '<nav class="site-nav__inner" aria-label="Primary">' +
        '<a class="skip-link" href="#main">Skip to content</a>' +
        '<a class="site-nav__brand" href="./index.html">' +
          '<span class="mark" aria-hidden="true"></span>' +
          '<span>MandiIQ<small class="tag">agricultural price intelligence</small></span>' +
        "</a>" +
        '<span class="site-nav__status" data-shell-status="" data-state=""' +
          ' role="status" title="Fetching live status from the production API">' +
          '<span class="led" aria-hidden="true"></span>connecting</span>' +
        '<ul class="site-nav__links">' + links + "</ul>" +
      "</nav>";
  }

  function buildFooter() {
    var host = document.querySelector("[data-site-footer]");
    if (!host) return;
    host.className = "site-footer";
    host.innerHTML =
      '<div class="site-footer__inner">' +
        "<div>" +
          '<div class="site-footer__brand">MandiIQ</div>' +
          '<p class="site-footer__blurb">Causal price intelligence over the national ' +
          "mandi record: regression discontinuity on the rainfall-deficiency threshold, " +
          "conformal intervals, drift monitors and tail risk - served from a live warehouse " +
          "with date integrity enforced on every ingest.</p>" +
        "</div>" +
        "<div><h3>Surfaces</h3><ul>" +
          PAGES.map(function (p) {
            return "<li><a href=\"" + esc(p.href) + "\"" +
              (p.external ? ' target="_blank" rel="noopener"' : "") + ">" + esc(p.label) + "</a></li>";
          }).join("") +
          '<li><a href="https://mandiiq.streamlit.app/" target="_blank" rel="noopener">Streamlit cockpit</a></li>' +
        "</ul></div>" +
        "<div><h3>Live status</h3><ul>" +
          EXTERNAL.slice(1).map(function (p) {
            return '<li><a href="' + esc(p.href) + '" target="_blank" rel="noopener">' + esc(p.label) + "</a></li>";
          }).join("") +
          '<li><a href="' + REPO + '" target="_blank" rel="noopener">Source repository</a></li>' +
        "</ul></div>" +
      "</div>" +
      '<div class="site-footer__legal">' +
        "<span>Underlying data: Agmarknet daily mandi prices via data.gov.in, IMD/Datameet " +
        "rainfall departures, Sentinel-2 NDVI.</span>" +
        "<span>Every figure on this site is fetched live from the production API. " +
        "Nothing is cached, hardcoded or mocked.</span>" +
      "</div>";
  }

  /* ---------------------------------------------------------------- nav --- */
  function markCurrentPage() {
    var here = window.location.pathname;
    var file = here.substring(here.lastIndexOf("/") + 1) || "index.html";
    var links = document.querySelectorAll(".site-nav__links a");
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute("href") || "";
      var target = href.substring(href.lastIndexOf("/") + 1);
      if (target === file) {
        links[i].setAttribute("aria-current", "page");
      }
    }
  }

  /* ------------------------------------------------------------- status --- */
  function setStatus(text, state, title) {
    var el = document.querySelector("[data-shell-status]");
    if (!el) return;
    var led = el.querySelector(".led");
    el.setAttribute("data-state", state || "");
    el.setAttribute("title", title || text);
    if (led) {
      el.innerHTML = "";
      el.appendChild(led);
      el.appendChild(document.createTextNode(text));
    } else {
      el.textContent = text;
    }
  }

  function relativeAge(hours) {
    if (hours == null || isNaN(hours)) return null;
    if (hours < 1) return Math.round(hours * 60) + " min";
    if (hours < 48) return Math.round(hours) + " h";
    return Math.round(hours / 24) + " d";
  }

  /* -------------------------------------------------------------- hosts --- */
  function hostByBase(base) {
    for (var i = 0; i < HOSTS.length; i++) {
      if (HOSTS[i].base === base) return HOSTS[i];
    }
    return null;
  }

  function readCache() {
    try {
      return window.sessionStorage ? window.sessionStorage.getItem(CACHE_KEY) : null;
    } catch (e) {
      return null; // storage can be blocked entirely
    }
  }

  function writeCache(base) {
    try {
      if (window.sessionStorage) window.sessionStorage.setItem(CACHE_KEY, base);
    } catch (e) { /* not worth failing a page over */ }
  }

  function clearCache() {
    try {
      if (window.sessionStorage) window.sessionStorage.removeItem(CACHE_KEY);
    } catch (e) { /* ignore */ }
  }

  function forget() {
    resolved = null;
    clearCache();
  }

  /* Point every host-qualified link at the instance that is answering, so the
     /health, /docs and /data-quality links in the nav and footer do not lead
     into a 503. */
  function repointLinks(base) {
    var links, i, j, href, stale;
    for (i = 0; i < HOSTS.length; i++) {
      stale = HOSTS[i].base;
      if (stale === base) continue;
      links = document.querySelectorAll('a[href^="' + stale + '"]');
      for (j = 0; j < links.length; j++) {
        href = links[j].getAttribute("href") || "";
        links[j].setAttribute("href", href.replace(stale, base));
      }
    }
  }

  function probeHost(host) {
    if (!host) return Promise.reject(new Error("unknown host"));
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, PROBE_TIMEOUT_MS) : null;

    function done() { if (timer) clearTimeout(timer); }

    return fetch(host.base + "/health", {
      cache: "no-store",
      signal: controller ? controller.signal : undefined
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (health) {
        done();
        if (!health || typeof health !== "object") throw new Error("unreadable /health");
        return { base: host.base, label: host.label, health: health };
      }, function (err) {
        done();
        throw err;
      });
  }

  /* Resolve once per page load, then reuse. The remembered host is probed
     first, so a visitor who already failed over stays on the instance that
     worked for them. */
  function resolveApi() {
    if (resolved) return Promise.resolve(resolved);
    if (pending) return pending;

    var remembered = readCache();
    if (remembered && hostByBase(remembered)) API = remembered;
    var first = hostByBase(API) || HOSTS[0];

    pending = probeHost(first)
      .catch(function (firstError) {
        return HOSTS
          .filter(function (h) { return h.base !== first.base; })
          .reduce(function (chain, host) {
            return chain.catch(function () { return probeHost(host); });
          }, Promise.reject(firstError));
      })
      .then(function (hit) {
        resolved = hit;
        pending = null;
        API = hit.base;
        writeCache(hit.base);
        repointLinks(hit.base);
        return hit;
      }, function (err) {
        pending = null;
        throw err;
      });

    return pending;
  }

  function applyHealth(hit) {
    var h = hit.health || {};
    var behind = h.days_behind;
    var fresh = behind != null && behind <= STALE_AFTER_DAYS;
    var age = h.last_refresh_success_utc || h.last_run_utc;
    var ageText = age ? relativeAge((Date.now() - Date.parse(age)) / 3.6e6) : null;
    var label = fresh ? "live" : "stale";
    if (behind != null) label += " \u00b7 " + behind + "d behind";
    if (hit.label !== "primary") label += " \u00b7 " + hit.label;
    var title = [
      "served by the " + hit.label + " instance (" + hit.base + ")",
      "version " + (h.version || "?"),
      "status " + (h.status || "unknown"),
      (h.n_prices != null ? Number(h.n_prices).toLocaleString("en-IN") + " price rows" : ""),
      (h.data_max_date ? "newest " + h.data_max_date : ""),
      (ageText ? "last successful refresh " + ageText + " ago" : ""),
      (h.last_price_source ? "served by " + h.last_price_source : "no price source reported")
    ].filter(Boolean).join(" \u00b7 ");
    setStatus(label, fresh ? "ok" : "warn", title);
  }

  function pollStatus() {
    var attempt = resolved ? probeHost(hostByBase(resolved.base)) : resolveApi();
    return attempt
      .catch(function () {
        // It answered a moment ago and is gone now (a redeploy, or a platform
        // 503). Drop the choice so the next poll re-probes from the top.
        forget();
        return resolveApi();
      })
      .then(applyHealth, function () {
        setStatus("api unreachable", "down",
          "Neither the " + HOSTS[0].label + " nor the " + HOSTS[1].label +
          " instance answered /health (checked " + new Date().toLocaleTimeString() + ").");
      });
  }

  /* --------------------------------------------------------------- boot --- */
  function boot() {
    buildNav();
    buildFooter();
    markCurrentPage();
    pollStatus();
    setInterval(pollStatus, 60000);
    // Re-check as soon as the tab is looked at again, so a page left open
    // overnight does not keep showing a stale badge.
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) pollStatus();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.MandiiqShell = {
    hosts: HOSTS,
    apiBase: function () { return resolved ? resolved.base : API; },
    resolveApi: resolveApi,
    pollStatus: pollStatus
  };
})();
