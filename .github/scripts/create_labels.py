#!/usr/bin/env python3
"""
One-time script to create GitHub labels for the MandiIQ repo.

Usage:
    1. Install gh CLI and authenticate: gh auth login
    2. Run this script: python .github/scripts/create_labels.py

This will create/update all defined labels on the remote repo.
"""

LABELS = [
    # name, color, description
    ("bug",           "D73A4A", "Something isn't working as expected"),
    ("enhancement",   "A2EEEF", "New feature or improvement request"),
    ("documentation", "0075CA", "Documentation changes or additions"),
    ("question",      "D876E3", "Further information is requested"),
    ("good first issue", "7057FF", "Good for newcomers — smaller scope, clear instructions"),
    ("help wanted",   "008672", "Extra attention is needed — maintainer could use assistance"),
    ("wontfix",       "FFFFFF", "This will not be worked on — closed without action"),
    ("duplicate",     "CFD3D7", "This issue or discussion already exists"),
    ("invalid",       "E4E669", "This doesn't seem right — not actionable"),
]


def create_labels():
    import subprocess, sys

    repo = "flawsom/MIS"

    for name, color, description in LABELS:
        cmd = [
            "gh", "label", "create", name,
            "--color", color,
            "--description", description,
            "--repo", repo,
            "--force",  # update if already exists
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✓  {name}")
        else:
            print(f"  ✗  {name}: {result.stderr.strip()}")


if __name__ == "__main__":
    print(f"Creating {len(LABELS)} labels for flawsom/MIS...")
    create_labels()
    print("Done.")
