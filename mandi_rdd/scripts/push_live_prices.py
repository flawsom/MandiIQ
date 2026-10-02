"""Hand production today's mandi prices, from a network that can read them.

Why this exists: the production container cannot reach the source. Measured on
2026-10-02, ``api.data.gov.in`` resets TLS from every cloud network this project
can run on (``SSL: UNEXPECTED_EOF_WHILE_READING`` from Northflank *and* from a
plain cloud sandbox), the official portal answers a cloud address with 403, and
the CEDA mirror is an archive whose daily coverage ends around 2025-10. So "the
newest date must move every day" cannot be a fetch inside the container - it has
to be a *push* from somewhere that can read the source: a laptop on an Indian
connection, an office gateway, a VPN. This is that push.

What it does:

  1. reads the current snapshot with the same fetcher the pipeline uses
     (``fetch_all_prices``: paginated, normalised, impossible/future dates
     already rejected), or takes a CSV you downloaded or exported by hand;
  2. writes the ten canonical columns;
  3. POSTs them to ``/admin/ingest-historical``, which now upserts through the
     same anti-join the pipeline uses - so re-running it, or overlapping with a
     scheduled run, cannot duplicate a row;
  4. prints the freshness before and after. "It worked" is the newest arrival
     date moving, not an HTTP 200.

Usage:

    # fetch today's snapshot and push it
    python -m mandi_rdd.scripts.push_live_prices

    # another instance, or a file you already have (any export shape the
    # endpoint understands: canonical, data.gov.in snapshot, Agmarknet archive)
    python -m mandi_rdd.scripts.push_live_prices --api https://host --file mandi.csv

    # show what would be sent, send nothing
    python -m mandi_rdd.scripts.push_live_prices --dry-run

    # afterwards, prove it from the outside
    python -m mandi_rdd.scripts.check_production_freshness

Exit codes: 0 pushed (or dry-run), 2 nothing could be read from the source,
3 the push itself failed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import urllib.error
import urllib.request

CANONICAL_COLUMNS = (
    "arrival_date", "state", "district", "market", "commodity",
    "variety", "grade", "min_price", "max_price", "modal_price",
)

DEFAULT_API = os.environ.get(
    "MANDIIQ_API_URL", "https://p01--mandiiq--x4n8x4gkmzht.code.run"
)


def canonical_row(record: dict) -> dict:
    """One record reduced to the canonical columns, values stringified.

    The endpoint reads these by name, so a missing column is written empty
    rather than dropped - a row with no variety is still a price.
    """
    row = {}
    for column in CANONICAL_COLUMNS:
        value = record.get(column)
        # NaN is not a price, and "nan" in a CSV is a price the other end has to
        # guess about - both become an empty field.
        if value is None or (isinstance(value, float) and math.isnan(value)):
            value = ""
        row[column] = str(value)
    return row


def build_csv(records: list[dict]) -> bytes:
    """The upload body: canonical columns, header, no index column."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CANONICAL_COLUMNS))
    writer.writeheader()
    for record in records:
        writer.writerow(canonical_row(record))
    return buffer.getvalue().encode("utf-8")


def freshness(api_base: str, timeout: int = 20) -> dict:
    """The figures /health publishes, or {} when it cannot be read."""
    try:
        with urllib.request.urlopen(
            api_base.rstrip("/") + "/health", timeout=timeout
        ) as response:
            payload = json.load(response)
    except Exception:  # noqa: BLE001 - a missing reading is not a failure here
        return {}
    return {
        key: payload.get(key)
        for key in ("status", "data_max_date", "days_behind", "n_prices", "version")
    }


def push(api_base: str, filename: str, body: bytes, timeout: int = 900) -> dict:
    """POST the CSV to /admin/ingest-historical and return its JSON answer."""
    import requests

    response = requests.post(
        api_base.rstrip("/") + "/admin/ingest-historical",
        files={"file": (filename, body, "text/csv")},
        timeout=timeout,
    )
    try:
        return response.json()
    except ValueError:
        return {
            "status": "error",
            "message": f"HTTP {response.status_code}: {response.text[:200]}",
        }


def _source_records(max_records: int | None) -> list[dict]:
    """The current snapshot, via the fetcher the pipeline uses."""
    from mandi_rdd.ingestion.fetch_prices import fetch_all_prices

    return fetch_all_prices(max_records=max_records)


def _print_freshness(label: str, reading: dict) -> None:
    if not reading:
        print(f"{label}: /health could not be read")
        return
    behind = reading.get("days_behind")
    print(
        f"{label}: newest {reading.get('data_max_date') or 'unknown'} "
        f"({behind if behind is not None else '?'} behind) - "
        f"{reading.get('n_prices')} rows - build {reading.get('version') or 'unlabelled'}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Push a live mandi price snapshot into the production warehouse."
    )
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL to push into.")
    parser.add_argument(
        "--file",
        help="Upload this CSV instead of fetching (any shape the endpoint accepts).",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Stop the fetch after this many records (default: the whole snapshot).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build the upload, send nothing.")
    parser.add_argument("--json", action="store_true", help="Print the endpoint's raw answer.")
    args = parser.parse_args(argv)

    before = freshness(args.api)
    _print_freshness("before", before)

    if args.file:
        with open(args.file, "rb") as handle:
            body = handle.read()
        filename = os.path.basename(args.file)
        print(f"upload: {filename} ({len(body)} bytes) read from disk")
    else:
        try:
            records = _source_records(args.max_records)
        except (urllib.error.URLError, OSError) as exc:
            print(f"nothing to push: the source could not be read ({exc})")
            print(
                "This is the expected outcome on a cloud network: api.data.gov.in "
                "does not answer one. Run this from a network that can read it, or "
                "use --file with a CSV you already have."
            )
            return 2
        if not records:
            print("nothing to push: the source answered with no records")
            return 2
        body = build_csv(records)
        filename = "mandiiq-live-snapshot.csv"
        print(
            f"upload: {filename} ({len(body)} bytes) built from {len(records)} "
            "fetched record(s)"
        )

    if args.dry_run:
        print("dry run: nothing sent")
        return 0

    answer = push(args.api, filename, body)
    if args.json:
        print(json.dumps(answer, indent=2, default=str))

    if answer.get("status") != "ok":
        print("push failed: " + str(answer.get("message"))[:400])
        return 3

    print(
        f"accepted: format={answer.get('format')} rows_read={answer.get('rows_read')} "
        f"rows_new={answer.get('rows_new')} newest_in_file={answer.get('newest_in_file')} "
        f"dates_rejected={answer.get('dates_rejected')}"
    )
    after = freshness(args.api)
    _print_freshness("after", after)

    if (
        before.get("data_max_date")
        and after.get("data_max_date")
        and after["data_max_date"] == before["data_max_date"]
    ):
        print(
            "the newest date did not move: the upload carried nothing newer than "
            f"{after['data_max_date']}. A snapshot for today is what moves it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
