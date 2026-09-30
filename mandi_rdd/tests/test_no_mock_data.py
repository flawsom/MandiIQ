"""No-mock-data guard.

Every number MandiIQ surfaces must come from a real ingested source
(data.gov.in, IMD/Open-Meteo, Sentinel Hub, Ashoka CEDA). This module fails
the build if fabricated-data markers creep into shipping code outside a
policy statement or the deliberate RDD placebo tests.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPING_DIR = REPO_ROOT / "mandi_rdd"
SKIP_PARTS = {"tests", "__pycache__", "frontend", "node_modules", "data", "models"}

MOCK_MARKER = re.compile(r"\b(mock|fake|dummy)\b", re.IGNORECASE)

# Lines that legitimately discuss mock data: UI policy statements ("no mock
# data") and the RDD falsification suite, which runs at deliberately fake
# cutoffs as a placebo check.
POLICY_OR_PLACEBO = re.compile(
    r"(no[- ]mock|mock fallback|never mock|fake cutoff|fake_cutoff|placebo|falsification)",
    re.IGNORECASE,
)

FORBIDDEN_IMPORTS = ("faker", "factory_boy", "mock")


def _read(path: Path) -> str:
    # A couple of legacy modules were saved in cp1252; replace undecodable
    # bytes rather than fail the guard on an encoding accident.
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _shipping_python_files():
    for path in SHIPPING_DIR.rglob("*.py"):
        if SKIP_PARTS & set(path.parts):
            continue
        yield path


def test_no_fabricated_data_markers_in_shipping_code():
    violations = []
    for path in _shipping_python_files():
        for number, line in enumerate(_read(path).splitlines(), 1):
            if MOCK_MARKER.search(line) and not POLICY_OR_PLACEBO.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not violations, "Fabricated-data markers found:\n" + "\n".join(violations)


def test_no_mock_libraries_imported_in_shipping_code():
    hits = []
    for path in _shipping_python_files():
        text = _read(path)
        for lib in FORBIDDEN_IMPORTS:
            if re.search(rf"^\s*(?:import|from)\s+{lib}\b", text, re.MULTILINE):
                hits.append(f"{path.relative_to(REPO_ROOT)} imports {lib}")
    assert not hits, "\n".join(hits)


def test_no_mock_fixture_files_in_shipping_tree():
    bad_tokens = ("mock", "fake", "dummy", "seed_data", "sample_data", "synthetic")
    hits = [
        str(path.relative_to(REPO_ROOT))
        for path in SHIPPING_DIR.rglob("*")
        if path.is_file()
        and not (SKIP_PARTS & set(path.parts))
        and any(token in path.name.lower() for token in bad_tokens)
    ]
    assert not hits, f"Mock/fixture-like files found: {hits}"
