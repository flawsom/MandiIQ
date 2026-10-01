/* ==========================================================================
   MandiIQ shared site shell behaviour
   - Highlights the current page in the navigation.
   - Probes every hosted instance of the API on one clock, keeps the pages
     talking to the one that actually answers, and drives the small status LED
     in the nav.
   - Keeps the one browser-side record of when each instance was down and what
     the instance or the platform edge said at the time, and renders it for
     every page that mounts [data-downtime-summary] / [data-downtime-pill].

   Two independently hosted instances serve the same image behind two
   hostnames. On 2026-10-01 the primary answered HTTP 503 "no healthy
   upstream" on every route while the mirror answered normally, and every page
   on this site went blank at once because each one only knew one hostname.
   So the list below is walked on every poll and the winner is remembered for
   the rest of the session: a dead host degrades the page to the other
   instance instead of removing the page.

   Nothing here stores downtime server-side, because nothing can: the record
   is the visitor's own, it lives in this browser, and it says so on the page.
   The pages above this one own their own data rendering; they get a base URL
   from window.MandiiqShell.resolveApi(), and this file only ever writes to
   [data-shell-status], [data-downtime-pill] and [data-downtime-summary].
   ========================================================================== */
(function () {
  "use strict";

  var HOSTS = [
    { base: "https://p01--mandiiq--x4n8x4gkmzht.code.run", label: "primary" },
    { base: "https://p01--mandiiq--zbvjrztgjqgw.code.run", label: "mirror" }
  ];

  var API = window.MANDIIQ_API || HOSTS[0].base;
  var STALE_AFTER_DAYS = 3;
  var HEALTH_TIMEOUT_MS = 9000;
  var CACHE_KEY = "mandiiq.api.base";
  var resolved = null;
  var pending = null;
  var probing = null;
  var lastResults = null;
  var listeners = [];

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

  /* ----------------------------------------------------------------- probes --- */
  function fetchWithTimeout(url, ms) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, ms) : null;
    function done() { if (timer) clearTimeout(timer); }
    return fetch(url, { cache: "no-store", signal: controller ? controller.signal : undefined })
      .then(function (res) {
        return res.text().then(function (body) { done(); return { res: res, body: body }; });
      }, function (err) { done(); throw err; });
  }

  /*
   * One probe per instance, in the shape every page needs: the HTTP status, the
   * parsed /health and the returned text. The body is kept because when an
   * instance is down that text *is* the reason - a platform 503 says "no healthy
   * upstream" in the only words anyone has for it - and the record is not
   * allowed to paraphrase it.
   *
   * This never rejects. A host that does not answer is a result, not an error:
   * the caller has to be able to say "the mirror is down" as calmly as "the
   * mirror is up", and both pages and the downtime record read it that way.
   */
  function probeHost(host) {
    var started = Date.now();
    if (!host) {
      return Promise.resolve({ host: null, state: "net", http: null, ms: 0, health: null, body: "no such host" });
    }
    return fetchWithTimeout(host.base + "/health", HEALTH_TIMEOUT_MS).then(function (r) {
      var parsed = null;
      try { parsed = JSON.parse(r.body); } catch (e) { parsed = null; }
      return {
        host: host, state: r.res.ok ? "ok" : "http", http: r.res.status,
        ms: Date.now() - started, health: parsed, body: r.body
      };
    }, function (err) {
      return {
        host: host, state: "net", http: null, ms: Date.now() - started, health: null,
        body: (err && err.name === "AbortError")
          ? "timed out after " + (HEALTH_TIMEOUT_MS / 1000) + "s"
          : String((err && err.message) || err)
      };
    });
  }

  /* Every instance is probed on the same round, in the order the shell lists
     them, so one page poll answers both questions: which instance is serving,
     and which instances are down right now. Concurrent callers share one round
     instead of firing a second pair of requests. */
  function probeAll() {
    if (probing) return probing;
    probing = Promise.all(HOSTS.map(probeHost)).then(function (results) {
      probing = null;
      return results;
    }, function (err) {
      probing = null;
      throw err;
    });
    return probing;
  }

  /* The instance a page should talk to: the one this visitor already settled on
     if it is still answering, otherwise the first in the shell's own order, so
     the primary is preferred for anyone who has never failed over. */
  function pickHost(results) {
    var preferred = resolved ? resolved.base : (readCache() || API);
    var ok = results.filter(function (r) { return r.state === "ok" && r.health; });
    for (var i = 0; i < ok.length; i++) {
      if (ok[i].host && ok[i].host.base === preferred) return ok[i];
    }
    return ok[0] || null;
  }

  function asHit(result) {
    return result && result.host
      ? { base: result.host.base, label: result.host.label, host: result.host, health: result.health }
      : null;
  }

  /* Resolve once per page load, then reuse. The remembered host is preferred,
     so a visitor who already failed over stays on the instance that worked for
     them. */
  function resolveApi() {
    if (resolved) return Promise.resolve(resolved);
    if (pending) return pending;

    var remembered = readCache();
    if (remembered && hostByBase(remembered)) API = remembered;

    pending = probeAll().then(function (results) {
      var hit = asHit(pickHost(results));
      pending = null;
      if (!hit) throw new Error("no instance answered /health");
      resolved = hit;
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

  /* ---------------------------------------------------------------- record ---
     The shell's own record of when each instance stopped answering, and what
     the instance or the platform edge said at the time. It is written by the
     poll above, kept in this browser, and deliberately modest about what it can
     claim: an outage that starts and ends while no tab is open is invisible, an
     entry can never be finer than one check, and the reason shown is the
     returned text - never an inference about why.

     It lives here rather than on one page because the fact is about the pair of
     instances, not about a page: every page that mounts the hooks below shows
     the same record, and the status page shows the long form of it.

     No hostname appears in the markup. Entries are keyed by the base URL the
     shell hands over, so the instance list still lives in exactly one file.
     -------------------------------------------------------------------------- */
  var TIMELINE_KEY = "mandiiq.timeline.v1";
  var TIMELINE_LEGACY_KEY = "mandiiq.status.timeline.v1"; // what the status page used before this was shared
  var TIMELINE_MAX = 8;            // closed outages kept per instance
  var TIMELINE_REASON_MAX = 200;   // characters of returned text kept per entry
  var TIMELINE_SHOWN = 3;          // entries the short form lists
  var timeline = null;

  function store() {
    try { return window.localStorage || null; } catch (e) { return null; }
  }

  function loadTimeline() {
    if (timeline) return timeline;
    var s = store();
    var readable = !!s;
    var parsed = null;
    var legacy = false;
    if (s) {
      try { parsed = JSON.parse(s.getItem(TIMELINE_KEY) || "null"); }
      catch (e) { readable = false; parsed = null; }
      if (!parsed) {
        // Adopt what this browser recorded while the status page owned the list,
        // so sharing it across pages does not look like a wiped history.
        try {
          parsed = JSON.parse(s.getItem(TIMELINE_LEGACY_KEY) || "null");
          legacy = !!parsed;
        } catch (e) { parsed = null; }
      }
    }
    timeline = { persisted: readable, states: {}, episodes: [] };
    if (parsed && typeof parsed === "object") {
      if (parsed.states && typeof parsed.states === "object") timeline.states = parsed.states;
      if (parsed.episodes && parsed.episodes.length) timeline.episodes = parsed.episodes;
    }
    if (legacy) saveTimeline();
    return timeline;
  }

  function saveTimeline() {
    var s = store();
    if (!timeline) return;
    if (!s) { timeline.persisted = false; return; }
    try {
      s.setItem(TIMELINE_KEY, JSON.stringify({
        v: 1, states: timeline.states, episodes: timeline.episodes
      }));
      timeline.persisted = true;
    } catch (e) {
      // Private mode, a full quota, storage blocked mid-session: keep the
      // entries for this visit and stop claiming they will survive it.
      timeline.persisted = false;
    }
  }

  function oneLine(text) {
    var s = String(text == null ? "" : text).replace(/\s+/g, " ").trim();
    return s.length > TIMELINE_REASON_MAX ? s.slice(0, TIMELINE_REASON_MAX - 3) + "..." : s;
  }

  // Why an instance is not serving, in its own words wherever it has any - and
  // with the one client-side cause named separately, because a browser that is
  // itself offline fails both instances at once and would otherwise look like
  // two simultaneous outages.
  function downReason(r) {
    if (typeof navigator !== "undefined" && navigator && navigator.onLine === false) {
      return "No response from this browser while it reports itself offline" +
        (r.body ? ": " + oneLine(r.body) : "");
    }
    if (r.state === "http") return "HTTP " + r.http + (r.body ? " - " + oneLine(r.body) : "");
    return "No response" + (r.body ? ": " + oneLine(r.body) : " at all");
  }

  function closeEpisode(t, base, at, r) {
    var h = r.health || {};
    for (var i = t.episodes.length - 1; i >= 0; i--) {
      var ep = t.episodes[i];
      if (ep.base !== base || ep.end) continue;
      ep.end = at;
      // The container that came back can say more about the gap than the edge
      // could: a run marker left on the volume is the one honest evidence that
      // the work was what killed the process.
      var died = h.last_unclean_refresh || {};
      var booted = died.detected_utc ? Date.parse(died.detected_utc) : NaN;
      var during = !isNaN(booted) && booted >= Date.parse(ep.start) && booted <= Date.parse(at);
      if (ep.unclean != null && h.unclean_refresh_runs != null && h.unclean_refresh_runs > ep.unclean) {
        ep.note = "The container that came back reports " +
          (h.unclean_refresh_runs - ep.unclean) + " refresh run(s) killed mid-flight" +
          (during && died.step ? ", the last one during " + died.step : "") +
          ". A container restarted while a run was working looks exactly like this.";
      }
      if (ep.version && h.version && h.version !== ep.version) {
        ep.note = (ep.note ? ep.note + " " : "") + "It answered again on build v" + h.version +
          " (it was v" + ep.version + " before), so a redeploy is at least part of this gap.";
      }
      return;
    }
  }

  function capEpisodes(list) {
    var counts = {}, kept = [], i, ep, n;
    // A running outage is never dropped whatever the cap says; closed ones are
    // kept newest-first up to TIMELINE_MAX.
    for (i = list.length - 1; i >= 0; i--) {
      ep = list[i];
      if (!ep.end) { kept.push(ep); counts[ep.base] = (counts[ep.base] || 0) + 1; }
    }
    for (i = list.length - 1; i >= 0; i--) {
      ep = list[i];
      if (!ep.end) continue;
      n = counts[ep.base] || 0;
      if (n >= TIMELINE_MAX) continue;
      kept.push(ep);
      counts[ep.base] = n + 1;
    }
    return kept.sort(function (a, b) { return Date.parse(a.start) - Date.parse(b.start); });
  }

  function recordTimeline(results) {
    var t = loadTimeline();
    var now = new Date().toISOString();
    var known = {};
    results.forEach(function (r) {
      if (!r.host) return;
      var base = r.host.base;
      var prev = t.states[base] || null;
      var h = r.health || {};
      var down = r.state !== "ok";
      var counts = h.unclean_refresh_runs == null ? null : h.unclean_refresh_runs;
      // An instance that is not answering cannot report its own counters, so the
      // baseline for "what changed during the outage" comes from the last check
      // that did answer.
      var baseline = counts != null ? counts : (prev && prev.unclean != null ? prev.unclean : null);
      var build = h.version || (prev && prev.version) || null;

      known[base] = true;
      if (down && (!prev || prev.state !== "down")) {
        t.episodes.push({
          base: base, label: r.host.label, start: now, end: null,
          // Separates "it was already down when this browser first looked" from
          // "it went down while this browser was watching": a page must not
          // back-date an outage it never saw begin.
          first_check: !prev,
          reason: downReason(r), http: r.http,
          unclean: baseline, version: build
        });
      }
      if (!down && prev && prev.state === "down") closeEpisode(t, base, now, r);

      t.states[base] = {
        state: down ? "down" : "up",
        at: (!prev || prev.state !== (down ? "down" : "up")) ? now : prev.at,
        first_at: prev && prev.first_at ? prev.first_at : now,
        label: r.host.label,
        version: h.version || null,
        unclean: counts,
        reason: down ? downReason(r) : null
      };
    });
    // An instance the shell no longer lists is neither shown nor kept: the
    // record describes the current pair, not hosts that have been renamed away.
    t.episodes = capEpisodes(t.episodes.filter(function (ep) { return known[ep.base]; }));
    saveTimeline();
  }

  function humanDuration(ms) {
    if (isNaN(ms) || ms < 0) return "unknown";
    var s = Math.round(ms / 1000);
    if (s < 90) return s + " s";
    var min = Math.round(s / 60);
    if (min < 90) return min + " min";
    var hours = Math.floor(min / 60), rest = min % 60;
    if (hours < 48) return hours + " h" + (rest ? " " + rest + " min" : "");
    return Math.round(hours / 24) + " d";
  }

  function clock(iso) {
    var ms = Date.parse(iso);
    if (isNaN(ms)) return iso || "an unknown time";
    return new Date(ms).toLocaleString();
  }

  function summarize() {
    var t = loadTimeline();
    var running = 0, recorded = 0, i, ep;
    for (i = 0; i < t.episodes.length; i++) {
      ep = t.episodes[i];
      if (ep.end) recorded++; else running++;
    }
    return {
      persisted: t.persisted, running: running, recorded: recorded,
      episodes: t.episodes, states: t.states
    };
  }

  // The same words on every page: the long list on the status page and the short
  // list everywhere else are rendered from one description of an entry, so they
  // cannot drift into telling different stories about one outage.
  function describeEpisode(ep) {
    return {
      live: !ep.end,
      head: (ep.first_check ? "Already down at the first check" : "Stopped answering") +
        " - " + clock(ep.start),
      tail: ep.end
        ? "Answered again " + clock(ep.end) + ", down for " +
          humanDuration(Date.parse(ep.end) - Date.parse(ep.start))
        : "Still down, for " + humanDuration(Date.now() - Date.parse(ep.start)),
      reason: ep.reason || "no reason was returned",
      note: ep.note || null
    };
  }

  /* The one-line form: how many outages this browser has recorded, and whether
     one of them is running now. The page supplies the pill's own styling, so
     this only adds the state every page already has a rule for. */
  function renderPill() {
    var pills = document.querySelectorAll("[data-downtime-pill]");
    if (!pills.length) return;
    var s = summarize();
    var state, text;
    if (s.running) {
      state = "down";
      text = s.running + " outage" + (s.running === 1 ? "" : "s") + " running";
    } else if (s.recorded) {
      state = "warn";
      text = s.recorded + " outage" + (s.recorded === 1 ? "" : "s") + " recorded";
    } else {
      state = "ok";
      text = "no downtime seen";
    }
    for (var i = 0; i < pills.length; i++) {
      var el = pills[i];
      if (!el.getAttribute("data-base-class")) {
        el.setAttribute("data-base-class", el.className || "");
      }
      // A page that drew an LED in the pill keeps it; the state class and the
      // data-state attribute are what actually colour it.
      var wantsLed = !!el.querySelector(".led");
      el.className = (el.getAttribute("data-base-class") + " " + state).replace(/\s+/g, " ").trim();
      el.setAttribute("data-state", state);
      el.setAttribute("title", "Recorded in this browser only, by the shared page shell that " +
        "probes every instance every minute - the downtime list says what a browser-side record cannot see.");
      if (wantsLed) el.innerHTML = '<span class="led" aria-hidden="true"></span>' + esc(text);
      else el.textContent = text;
    }
  }

  /* The short form, for every page that is not the status page. It shows the
     entries a visitor is most likely to care about - anything running now,
     then the newest - and then says plainly what the record cannot see. */
  function renderSummary() {
    var mounts = document.querySelectorAll("[data-downtime-summary]");
    if (!mounts.length) return;
    var s = summarize();
    var hosts = HOSTS;
    var html;

    if (!hosts.length) {
      html = '<p class="downtime__limits">The shared shell (site.js) did not load, so this page ' +
        "does not know which instances to keep a record of.</p>";
    } else {
      var ordered = s.episodes.slice().sort(function (a, b) {
        if (!a.end !== !b.end) return a.end ? 1 : -1;   // running first
        return Date.parse(b.start) - Date.parse(a.start);
      }).slice(0, TIMELINE_SHOWN);

      var counts = hosts.map(function (h) {
        var mine = s.episodes.filter(function (ep) { return ep.base === h.base; });
        var down = s.states[h.base] && s.states[h.base].state === "down";
        if (down) return esc(h.label) + " is not answering now";
        if (!mine.length) return esc(h.label) + ": no outage recorded";
        return esc(h.label) + ": " + mine.length + " outage" + (mine.length === 1 ? "" : "s") + " recorded";
      }).join(" \u00b7 ");

      var list = ordered.length
        ? '<ol class="downtime__list">' + ordered.map(function (ep) {
            var d = describeEpisode(ep);
            return '<li class="downtime__item' + (d.live ? " is-live" : "") + '">' +
              '<span class="downtime__host">' + esc(ep.label || "instance") + "</span>" +
              '<span class="downtime__when">' + esc(d.head) + "</span>" +
              '<span class="downtime__tail">' + esc(d.tail) + "</span>" +
              '<span class="downtime__why">' + esc(d.reason) + "</span>" +
              (d.note ? '<span class="downtime__note">' + esc(d.note) + "</span>" : "") +
              "</li>";
          }).join("") + "</ol>"
        : '<p class="downtime__none">No outage recorded: this browser has been checking ' +
          "both instances " + (firstCheckAt(s) ? "since " + esc(clock(firstCheckAt(s))) : "since this page loaded") + ".</p>";

      html = counts
        ? '<p class="downtime__counts">' + counts + "</p>" + list
        : list;
      html += (s.persisted ? "" :
        '<p class="downtime__limits">This browser refuses to store anything, so the entries above ' +
        "describe this visit only - the page will not pretend to remember more than it can.</p>");
      html += '<p class="downtime__limits">Recorded by this browser only. This is a page, not a monitor: an ' +
        "outage that begins and ends while no tab is open is invisible, an entry can never be more precise " +
        "than the 60-second check, and the reason shown is the text the instance or the platform edge " +
        "returned - never a guess about it. Entries are capped to the last few outages per instance and " +
        'clearing site data erases them. <a href="./status.html">Status</a> lists every entry it kept.</p>';
    }

    for (var i = 0; i < mounts.length; i++) {
      mounts[i].className = "downtime";
      mounts[i].innerHTML = html;
    }
  }

  function firstCheckAt(s) {
    var first = null, i, base;
    for (base in s.states) {
      if (!Object.prototype.hasOwnProperty.call(s.states, base)) continue;
      if (!s.states[base].first_at) continue;
      if (!first || Date.parse(s.states[base].first_at) < Date.parse(first)) first = s.states[base].first_at;
    }
    return first;
  }

  function renderDowntime() {
    renderPill();
    renderSummary();
  }

  /* ----------------------------------------------------------------- events --- */
  function onProbe(fn) {
    listeners.push(fn);
    if (lastResults) fn(lastResults);
    return function () {
      var i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  }

  function notify(results) {
    for (var i = 0; i < listeners.length; i++) {
      try {
        listeners[i](results);
      } catch (e) {
        // A page's own render must not be able to stop the shell's clock.
        if (window.console && console.warn) console.warn("probe listener failed", e);
      }
    }
  }

  function pollStatus() {
    return probeAll().then(function (results) {
      lastResults = results;
      recordTimeline(results);
      renderDowntime();

      var hit = asHit(pickHost(results));
      if (hit) {
        resolved = hit;
        API = hit.base;
        writeCache(hit.base);
        repointLinks(hit.base);
        applyHealth(hit);
      } else {
        // It answered a moment ago and is gone now (a redeploy, or a platform
        // 503). Drop the choice so the next poll re-probes from the top.
        forget();
        setStatus("api unreachable", "down",
          "Neither the " + HOSTS[0].label + " nor the " + HOSTS[1].label +
          " instance answered /health (checked " + new Date().toLocaleTimeString() + ").");
      }
      notify(results);
      return results;
    });
  }

  /* --------------------------------------------------------------- boot --- */
  function boot() {
    buildNav();
    buildFooter();
    markCurrentPage();
    renderDowntime();
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
    pollStatus: pollStatus,
    probeNow: function () { return pollStatus(); },
    lastProbe: function () { return lastResults; },
    onProbe: onProbe,
    downtime: {
      KEY: TIMELINE_KEY,
      MAX: TIMELINE_MAX,
      load: loadTimeline,
      record: recordTimeline,
      summary: summarize,
      describe: describeEpisode,
      clock: clock,
      humanDuration: humanDuration,
      render: renderDowntime
    }
  };
})();
