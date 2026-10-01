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


def test_the_shared_shell_keeps_one_short_downtime_record_for_every_page() -> None:
    """Downtime is a fact about the pair of instances, so it is recorded once.

    Nothing server-side stores downtime, so the record lives in the visitor's
    browser and has to stay a short list: one entry per transition between
    answering ``/health`` and not answering it, capped per instance, and never
    an outage back-dated to a check that did not observe it begin. It is kept by
    the shared shell rather than by a page, because every page shows it - one
    key, one wording, one cap - and the shell is also the only thing that probes
    both instances on one clock.
    """
    shell = (DOCS / "assets" / "site.js").read_text(encoding="utf-8")

    key = re.search(r'TIMELINE_KEY\s*=\s*"([^"]+)"', shell)
    assert key, "the shared downtime record no longer names the storage key it uses"
    assert "localStorage" in shell, "the record must be kept client-side, not implied to be server-side"
    assert re.search(r'TIMELINE_LEGACY_KEY\s*=\s*"mandiiq\.status', shell), \
        "the key the status page used before this was shared is not adopted, so a visiting browser loses its history"

    cap = re.search(r"TIMELINE_MAX\s*=\s*(\d+)", shell)
    assert cap, "the shared record no longer declares a cap"
    assert 0 < int(cap.group(1)) <= 25, "a browser is not a monitor: the record has to stay short"

    for fn in ("recordTimeline", "renderDowntime", "renderSummary", "renderPill"):
        assert fn in shell, f"the shared shell no longer has {fn}"

    # Both hooks exist so a page shows the record by mounting it, not by
    # reimplementing it: the short list and the one-line pill.
    for hook in ("[data-downtime-summary]", "[data-downtime-pill]"):
        assert hook in shell, f"the shared shell no longer renders into {hook}"

    # One reading of every instance per poll: the record cannot be built from a
    # failover chain that only ever reaches the host that answered.
    assert "probeAll" in shell and "HOSTS.map(probeHost)" in shell, \
        "the shell no longer probes every instance on one round"

    # An outage already running at the first check must be labelled as such,
    # not dated to the check that happened to find it.
    assert "first_check" in shell, "an outage seen in progress is not distinguished from one seen start"

    # And the copy has to admit the shape of the record it is showing.
    assert "not a monitor" in shell, "the shared summary does not state that it only sees what a visit sees"
    assert "this browser only" in shell or "in this browser" in shell, \
        "the record does not say where it is kept"


def test_the_status_page_renders_the_shared_record_instead_of_its_own() -> None:
    """The status page shows the long form of the one record, and owns none of it."""
    page = (DOCS / "status.html").read_text(encoding="utf-8")

    assert 'id="timeline"' in page, "the timeline panel has no mount point"
    assert "renderTimeline" in page, "the status page no longer renders the record in full"
    assert 'id="downtime-pill"' in page and "data-downtime-pill" in page, \
        "the header has no downtime pill for the shared shell to fill"

    assert "MandiiqShell" in page and "shell.downtime" in page, \
        "the status page does not read the record the shared shell keeps"
    assert "downtime.describe" in page, \
        "the status page tells its own version of an entry, so the long and short forms can drift apart"

    # The record has exactly one owner: a second storage key here would be a
    # second record, and two pages would start disagreeing about one outage.
    assert "TIMELINE_KEY" not in page, "the status page went back to keeping its own downtime record"
    assert "localStorage" not in page, "the status page stores downtime itself instead of reading the shared record"

    assert "not a monitor" in page, "the timeline does not state that it only sees what a visit sees"


def test_the_console_and_the_heartbeat_page_show_the_same_downtime_summary() -> None:
    """A page shows the shared record by mounting it - no page keeps its own copy."""
    for name in ("live.html", "heartbeat-dashboard.html"):
        page = (DOCS / name).read_text(encoding="utf-8")
        assert "data-downtime-summary" in page, f"{name} does not mount the shared downtime summary"
        assert "data-downtime-pill" in page, f"{name} has no downtime pill to fill"
        assert "./assets/site.js" in page, f"{name} does not load the shell that fills them"
        for fn in ("recordTimeline", "renderSummary", "loadTimeline"):
            assert fn not in page, f"{name} reimplements {fn} instead of showing the shared record"


def test_the_status_page_labels_a_build_skew_between_the_instances() -> None:
    """Two hostnames can answer two different images, and that has to be visible.

    The mirror has answered a /health payload with no version field in it at all,
    so a page cannot label a build skew by comparing version strings: it has to
    read what each payload publishes, and treat a field that is not there as
    absent rather than as a value. The panel and the pill are pinned here so an
    older image keeps being named as old instead of read as a broken one.
    """
    page = (DOCS / "status.html").read_text(encoding="utf-8")

    assert 'id="build-skew"' in page, "the build comparison has no panel"
    assert 'id="build-pill"' in page, "the header has no one-line build summary"
    for fn in ("buildProfile", "buildSkew", "renderBuildSkew"):
        assert fn in page, f"the status page no longer has {fn}"

    # The comparison is made from the published payloads themselves, not from a
    # list of fields somebody remembered to keep in step with the API.
    assert "Object.keys" in page, "the build comparison no longer reads the fields a build publishes"
    assert "BUILD_ABSENT" in page, "the page no longer has words for a field a build does not publish"
    assert "keyFirst" in page, "the fields a reader has to know about are no longer named first"

    # And the wording has to keep absence distinct from a false or a zero.
    assert "is not a value" in page, "an unpublished field is not distinguished from a value"
    assert "not zero, not false" in page, "the panel does not say what an absent field is not"

    # The observed case: a payload with no version field at all.
    assert "reports no build version at all" in page, \
        "a build that names no version is no longer called out as an older image"


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
