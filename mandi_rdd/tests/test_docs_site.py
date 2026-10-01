"""Contract tests for the static site that Vercel serves from ``docs/``.

The pages under ``docs/`` are the product's public face: ``/`` is the landing
page, and the console, heartbeat, status and write-up hang off it. They are
plain HTML with no build step, so nothing catches a broken link at build time -
and that has already cost this project twice. Once a page was moved while its
``../docs/...`` links stayed behind, so every asset and nav link 404'd on the
host that actually serves the directory; once the sitemap advertised a URL that
did not exist.

These tests pin the invariants that keep the served site coherent:
the pages exist, their relative links resolve inside ``docs/``, the shared
navigation and the sitemap only point at files that are really there, and the
landing page and the write-up do not both claim the site root as canonical.

No network access is required.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
CANONICAL_HOST = "https://mandiiq.unifies.codes"

# ``/`` is index.html. overview.html is the technical write-up that used to be
# served at the root before the landing page took that URL.
SERVED_PAGES = (
    "index.html",
    "overview.html",
    "live.html",
    "status.html",
    "heartbeat-dashboard.html",
)

ASSETS = ("assets/site.css", "assets/site.js")

# A real link target. JS-templated hrefs (``href="'+r.html_url+'"``) are not
# file paths, so anything carrying a quote or a concatenation is skipped.
LINK_RE = re.compile(r'(?:href|src)="([^"]+)"')
NOT_A_PATH = re.compile(r"[\'+$]")


def docs_files() -> list[Path]:
    return sorted(DOCS.glob("*.html"))


def local_refs(text: str) -> list[str]:
    refs = []
    for ref in LINK_RE.findall(text):
        if ref.startswith(("http://", "https://", "#", "mailto:", "data:")):
            continue
        if NOT_A_PATH.search(ref):
            continue
        refs.append(ref.split("#")[0].split("?")[0])
    return [r for r in refs if r]


def test_every_served_page_exists() -> None:
    missing = [name for name in SERVED_PAGES if not (DOCS / name).is_file()]
    assert not missing, f"docs/ is missing served pages: {missing}"

    missing_assets = [name for name in ASSETS if not (DOCS / name).is_file()]
    assert not missing_assets, f"docs/ is missing shared assets: {missing_assets}"


def served_path(page_name: str) -> str:
    """The URL path a docs/ file is served at. The host publishes docs/ as the root."""
    return "/" if page_name == "index.html" else "/" + page_name


def resolve_ref(page_name: str, ref: str) -> Path | None:
    """Resolve a link the way the host does, not the way the filesystem does.

    ``../docs/assets/site.css`` exists on disk, so a filesystem check passes it -
    and it still 404s, because the browser resolves it against the served URL and
    there is no ``/docs/`` above the root. Anything that climbs out of the served
    root is broken by definition; everything else must land on a real file.
    """
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(served_path(page_name)), ref))
    if resolved.startswith("../") or resolved == "..":
        return None
    return DOCS / resolved.lstrip("/")


def test_relative_links_resolve_inside_docs() -> None:
    """A page moved inside docs/ must not keep pointing at ../docs/ or a ghost."""
    broken = {}
    for page in docs_files():
        for ref in local_refs(page.read_text(encoding="utf-8")):
            target = resolve_ref(page.name, ref)
            if target is None or not target.exists():
                broken.setdefault(page.name, []).append(ref)
    assert not broken, f"links that do not resolve from the served root: {broken}"


def test_the_navigation_and_the_sitemap_only_point_at_real_pages() -> None:
    """site.js builds the nav for every page, so a stale entry breaks all of them."""
    shell = (DOCS / "assets/site.js").read_text(encoding="utf-8")
    nav = re.findall(r'href: "\./([^"]+)"', shell)
    assert nav, "docs/assets/site.js no longer exposes a PAGES list to check"

    nav_missing = [href for href in nav if not (DOCS / href).exists()]

    sitemap = (DOCS / "sitemap.xml").read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locs, "docs/sitemap.xml lists no URLs"
    sitemap_missing = []
    for loc in locs:
        if not loc.startswith(CANONICAL_HOST):
            sitemap_missing.append(loc)
            continue
        path = loc[len(CANONICAL_HOST):].lstrip("/") or "index.html"
        if not (DOCS / path).exists():
            sitemap_missing.append(loc)

    assert not nav_missing, f"the shared nav points at missing pages: {nav_missing}"
    assert not sitemap_missing, f"the sitemap advertises URLs that do not exist: {sitemap_missing}"


def test_the_status_page_reads_its_hosts_from_the_shell() -> None:
    """Two instances behind two hostnames is one fact, and it lives in site.js."""
    page = (DOCS / "status.html").read_text(encoding="utf-8")

    assert "MandiiqShell" in page, "the status page must take the instance list from the shared shell"
    assert "shell.hosts" in page, "the status page must read the hosts the shell resolved"
    assert ".code.run" not in page, "the status page duplicated the instance hostnames"

    for hook in ("data-site-nav", "data-site-footer"):
        assert hook in page, f"the status page is missing the shared {hook} mount"


def test_the_status_page_keeps_a_short_downtime_timeline_in_the_browser() -> None:
    """The status page records its own outages, and says how little that means.

    Nothing server-side stores downtime, so the timeline lives in the visitor's
    browser and has to stay a short list: one entry per transition between
    answering ``/health`` and not answering it, capped per instance, and never
    an outage back-dated to a check that did not observe it begin. The page is
    checked here for all four properties - stored, bounded, recorded, rendered -
    so a later edit cannot quietly turn a browser-side note into a fake log.
    """
    page = (DOCS / "status.html").read_text(encoding="utf-8")

    key = re.search(r'TIMELINE_KEY\s*=\s*"([^"]+)"', page)
    assert key, "the downtime timeline no longer names the storage key it uses"
    assert "localStorage" in page, "the timeline must be kept client-side, not implied to be server-side"

    cap = re.search(r"TIMELINE_MAX\s*=\s*(\d+)", page)
    assert cap, "the timeline no longer declares a cap"
    assert 0 < int(cap.group(1)) <= 25, "a browser is not a monitor: the timeline has to stay short"

    for fn in ("recordTimeline", "renderTimeline"):
        assert fn in page, f"the status page no longer has {fn}"
    assert 'id="timeline"' in page, "the timeline panel has no mount point"
    assert 'id="downtime-pill"' in page, "the header has no downtime summary to fill"
    assert "setDowntimePill" in page, "the header pill is never updated from the recorded timeline"

    # An outage already running at the first check must be labelled as such,
    # not dated to the check that happened to find it.
    assert "first_check" in page, "an outage seen in progress is not distinguished from one seen start"

    # And the copy has to admit the shape of the record it is showing.
    assert "not a monitor" in page, "the timeline does not state that it only sees what a visit sees"


def test_the_landing_page_and_the_write_up_claim_different_urls() -> None:
    """Two pages sharing one canonical URL is how a site de-indexes itself."""
    landing = (DOCS / "index.html").read_text(encoding="utf-8")
    write_up = (DOCS / "overview.html").read_text(encoding="utf-8")

    def canonical(text: str) -> str:
        found = re.search(r'<link rel="canonical" href="([^"]+)"', text)
        assert found, "a served page has no canonical URL"
        return found.group(1)

    landing_url, write_up_url = canonical(landing), canonical(write_up)
    assert landing_url == CANONICAL_HOST + "/", f"/ is not served by index.html ({landing_url})"
    assert write_up_url != landing_url, "the landing page and the write-up claim the same canonical URL"
    assert write_up_url.startswith(CANONICAL_HOST + "/"), f"write-up canonical is off-host: {write_up_url}"


def test_nothing_points_at_the_removed_landing_directory() -> None:
    """landing/ was undeployed from the start: it is not a directory the host serves."""
    stale = {
        page.name: sorted(set(local_refs(page.read_text(encoding="utf-8"))))
        for page in docs_files()
    }
    stale = {name: [r for r in refs if r.startswith(("../", "landing/"))] for name, refs in stale.items()}
    assert not {k: v for k, v in stale.items() if v}, f"docs/ still references undeployed paths: {stale}"
    assert not (REPO_ROOT / "landing").exists(), "landing/ came back: the site is served from docs/ only"
